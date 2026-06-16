"""麻雀の日程調整ツール（月表示カレンダー）。

毎週の土日 × 昼/夜 の固定枠に対して、各メンバーが ○/△/× を入力する。
月めくりのカレンダー上で土日のマスをタップして回答し、必要人数（既定4人）の
○が集まった枠を自動でハイライトする。

起動:
    streamlit run mahjong_app.py
"""

import calendar
import datetime

import streamlit as st

import mahjong_store

st.set_page_config(page_title="麻雀 日程調整", page_icon="🀄", layout="wide")

# 見やすさ向上のためのスタイル。成立枠（primaryボタン）を緑にする。
st.markdown(
    """
    <style>
      div.stButton > button[kind="primary"] {
          background-color:#2e7d32; border-color:#2e7d32; color:#fff;
      }
      div.stButton > button[kind="primary"]:hover {
          background-color:#1b5e20; border-color:#1b5e20;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

# 状態色（参加○=緑 / 未定△=黄 / 不参加×=赤）。
COLORS = {"○": "#2e7d32", "△": "#f9a825", "×": "#c62828"}
# 出欠プルダウンの選択肢。表示ラベル → 内部値（"" = 未回答）。
ATTEND_OPTIONS = ["未回答", "⭕ 参加", "🔺 未定", "❌ 不参加"]
LABEL_TO_VALUE = {"未回答": "", "⭕ 参加": "○", "🔺 未定": "△", "❌ 不参加": "×"}
VALUE_TO_LABEL = {v: k for k, v in LABEL_TO_VALUE.items()}
SLOTS = [("day", "昼"), ("night", "夜")]
WEEKDAY_LABELS = ["月", "火", "水", "木", "金", "土", "日"]

ss = st.session_state
ss.setdefault("state", mahjong_store.load())
ss.setdefault("me", "")
ss.setdefault("my_email", "")
ss.setdefault("selected", None)  # クリックで選択中の枠（slot_key 文字列）
# 表示中の年月（その月の1日を保持）。
ss.setdefault("ym", datetime.date.today().replace(day=1))

state = ss.state


def slot_key(d: datetime.date, slot: str) -> str:
    return f"{d.isoformat()}|{slot}"


def my_status(d: datetime.date, slot: str) -> str:
    return state["responses"].get(slot_key(d, slot), {}).get(ss.me, "")


def set_my_status(d: datetime.date, slot: str, value: str) -> None:
    key = slot_key(d, slot)
    entry = state["responses"].setdefault(key, {})
    if value:
        entry[ss.me] = value
    else:
        entry.pop(ss.me, None)  # 未回答に戻したら削除しておく
        if not entry:
            state["responses"].pop(key, None)
    mahjong_store.save(state)


def counts(d: datetime.date, slot: str) -> dict:
    entry = state["responses"].get(slot_key(d, slot), {})
    return {
        "○": sum(1 for v in entry.values() if v == "○"),
        "△": sum(1 for v in entry.values() if v == "△"),
        "×": sum(1 for v in entry.values() if v == "×"),
    }


# --- Sidebar -----------------------------------------------------------------
with st.sidebar:
    st.header("設定")

    # 名前は手入力。メールは任意（入れておくと履歴から連絡を取りやすい）。
    name = st.text_input("お名前", value=ss.me, placeholder="例）山田").strip()
    email = st.text_input(
        "メールアドレス（任意）",
        value=ss.my_email,
        placeholder="例）yamada@example.com",
        help="入れておくと、他のメンバーが成立候補からあなたに連絡できます。",
    ).strip()
    ss.me = name
    ss.my_email = email

    # 名前・メールが変わったら登録内容を更新して保存。
    if name:
        changed = False
        if name not in state["members"]:
            state["members"].append(name)
            changed = True
        if email and state["contacts"].get(name) != email:
            state["contacts"][name] = email
            changed = True
        if changed:
            mahjong_store.save(state)

    threshold = st.number_input(
        "成立に必要な人数", min_value=2, max_value=8, value=4, step=1,
        help="○ がこの人数以上そろった枠を成立候補としてハイライトします。",
    )

    st.divider()
    st.caption("凡例: ○=参加 / △=たぶん / ×=不可")
    if st.button("最新の状態に更新"):
        ss.state = mahjong_store.load()
        st.rerun()


st.title("🀄 麻雀 日程調整")
if not ss.me:
    st.info("まずはサイドバーにお名前を入力してください。")
else:
    st.caption("各枠のプルダウンで出欠を選ぶだけ。「詳細」で参加者を確認できます。")

# --- Month navigation --------------------------------------------------------
ym = ss.ym
nav_prev, nav_label, nav_next = st.columns([1, 2, 1])
if nav_prev.button("◀ 前の月", use_container_width=True):
    ss.ym = (ym - datetime.timedelta(days=1)).replace(day=1)
    st.rerun()
nav_label.markdown(
    f"<h3 style='text-align:center;margin:0'>{ym.year}年 {ym.month}月</h3>",
    unsafe_allow_html=True,
)
if nav_next.button("次の月 ▶", use_container_width=True):
    next_month = (ym.replace(day=28) + datetime.timedelta(days=7)).replace(day=1)
    ss.ym = next_month
    st.rerun()

# --- Attendance list (this month's weekends) ---------------------------------
today = datetime.date.today()


def render_detail(key: str, d: datetime.date, slot_label: str) -> None:
    """枠の参加者内訳（○/△/× 別の名前）を表示する。"""
    entry = state["responses"].get(key, {})
    contacts = state["contacts"]
    wd = WEEKDAY_LABELS[d.weekday()]
    for mk, label in [("○", "参加"), ("△", "未定"), ("×", "不参加")]:
        people = []
        for n, v in entry.items():
            if v != mk:
                continue
            mail = contacts.get(n)
            if mk == "○" and mail:
                subject = f"麻雀 {d.month}/{d.day}（{wd}）{slot_label}"
                people.append(f"[{n}](mailto:{mail}?subject={subject})")
            else:
                people.append(n)
        body = "、".join(people) if people else "なし"
        st.markdown(
            f"<span style='color:{COLORS[mk]};font-weight:bold'>{mk} {label}"
            f"（{len(people)}）</span>: {body}",
            unsafe_allow_html=True,
        )


# その月の土日を取得。
weekend_days = [
    datetime.date(ym.year, ym.month, day)
    for day in range(1, calendar.monthrange(ym.year, ym.month)[1] + 1)
    if datetime.date(ym.year, ym.month, day).weekday() >= 5
]

if not weekend_days:
    st.caption("この月に土日はありません。")

for d in weekend_days:
    wd = WEEKDAY_LABELS[d.weekday()]
    day_color = "#1565c0" if d.weekday() == 5 else "#c62828"
    today_tag = " 🟡今日" if d == today else ""
    st.markdown(
        f"<h4 style='margin:0.6em 0 0.2em;color:{day_color}'>"
        f"{d.month}/{d.day}（{wd}）{today_tag}</h4>",
        unsafe_allow_html=True,
    )

    for slot, slot_label in SLOTS:
        key = slot_key(d, slot)
        c = counts(d, slot)
        ok = c["○"] >= threshold

        c_slot, c_count, c_select, c_detail = st.columns([1, 2, 2, 1])
        c_slot.markdown(f"**{slot_label}**")
        c_count.markdown(
            f"{'✅ ' if ok else ''}"
            f"<span style='color:{COLORS['○']}'>⭕{c['○']}</span> "
            f"<span style='color:{COLORS['△']}'>🔺{c['△']}</span> "
            f"<span style='color:{COLORS['×']}'>❌{c['×']}</span>",
            unsafe_allow_html=True,
        )

        if ss.me:
            cur_label = VALUE_TO_LABEL.get(my_status(d, slot), "未回答")
            choice = c_select.selectbox(
                "出欠",
                ATTEND_OPTIONS,
                index=ATTEND_OPTIONS.index(cur_label),
                key=f"sel_{key}",
                label_visibility="collapsed",
            )
            new_val = LABEL_TO_VALUE[choice]
            if new_val != my_status(d, slot):
                set_my_status(d, slot, new_val)
                st.rerun()
        else:
            c_select.caption("名前未入力")

        if c_detail.button("詳細", key=f"detail_{key}", use_container_width=True):
            ss.selected = None if ss.selected == key else key
            st.rerun()

        if ss.selected == key:
            with st.container(border=True):
                render_detail(key, d, slot_label)

st.divider()

# --- Candidate slots ---------------------------------------------------------
st.subheader("✅ 成立候補（○が必要人数以上）")
candidates = []
for key, entry in state["responses"].items():
    o = sum(1 for v in entry.values() if v == "○")
    if o >= threshold:
        date_str, slot = key.split("|")
        d = datetime.date.fromisoformat(date_str)
        if d >= today:  # 過去は出さない
            candidates.append((d, slot, o, entry))

if not candidates:
    st.caption("まだ成立候補はありません。")
else:
    contacts = state["contacts"]
    for d, slot, o, entry in sorted(candidates, key=lambda x: (x[0], x[1])):
        slot_label = dict(SLOTS)[slot]
        wd = WEEKDAY_LABELS[d.weekday()]
        # ○の人はメールがあれば mailto リンクにして連絡しやすくする。
        names = []
        for n, v in entry.items():
            if v != "○":
                continue
            mail = contacts.get(n)
            if mail:
                subject = f"麻雀 {d.month}/{d.day}（{wd}）{slot_label}"
                names.append(f"[{n}](mailto:{mail}?subject={subject})")
            else:
                names.append(n)
        st.markdown(
            f"- **{d.month}/{d.day}（{wd}）{slot_label}** — ○{o}人: " + "、".join(names)
        )
