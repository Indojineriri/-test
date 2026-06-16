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

# 状態色（参加○=緑 / たぶん△=黄 / 不可×=赤）。
COLORS = {"○": "#2e7d32", "△": "#f9a825", "×": "#c62828"}
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
    st.caption("土日の枠をクリックすると、参加者の確認とあなたの回答ができます。")

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

# --- Calendar grid -----------------------------------------------------------
header_cols = st.columns(7)
for i, label in enumerate(WEEKDAY_LABELS):
    color = {5: "#1565c0", 6: "#c62828"}.get(i, "inherit")  # 土=青, 日=赤
    header_cols[i].markdown(
        f"<div style='text-align:center;font-weight:bold;color:{color}'>{label}</div>",
        unsafe_allow_html=True,
    )

today = datetime.date.today()
cal = calendar.Calendar(firstweekday=0)  # 月曜始まり
for week in cal.monthdatescalendar(ym.year, ym.month):
    cols = st.columns(7)
    for col, d in zip(cols, week):
        with col:
            in_month = d.month == ym.month
            is_weekend = d.weekday() >= 5

            # 日付の見出し（土=青/日=赤、当月外は薄く、今日は背景強調）。
            if not in_month:
                day_color = "#cfcfcf"
            elif d.weekday() == 5:
                day_color = "#1565c0"
            elif d.weekday() == 6:
                day_color = "#c62828"
            else:
                day_color = "inherit"
            bg = "background:#fff3cd;border-radius:4px;" if d == today else ""
            col.markdown(
                f"<div style='text-align:center;font-weight:bold;color:{day_color};{bg}'>"
                f"{d.day}</div>",
                unsafe_allow_html=True,
            )

            if not (in_month and is_weekend):
                continue

            for slot, slot_label in SLOTS:
                key = slot_key(d, slot)
                c = counts(d, slot)
                ok = c["○"] >= threshold
                mark = my_status(d, slot) or "・"
                selected = ss.selected == key
                btn_label = f"{'📍' if selected else ''}{slot_label} {mark}"
                if st.button(
                    btn_label,
                    key=key,
                    use_container_width=True,
                    type="primary" if ok else "secondary",
                ):
                    ss.selected = key
                    st.rerun()
                # 人数の内訳を色付きで表示（成立枠は✅）。
                badge = "✅" if ok else ""
                col.markdown(
                    f"<div style='text-align:center;font-size:0.8em'>{badge}"
                    f"<span style='color:{COLORS['○']}'>○{c['○']}</span> "
                    f"<span style='color:{COLORS['△']}'>△{c['△']}</span> "
                    f"<span style='color:{COLORS['×']}'>×{c['×']}</span></div>",
                    unsafe_allow_html=True,
                )

st.divider()

# --- Selected slot: participants & my answer ---------------------------------
if ss.selected:
    d = datetime.date.fromisoformat(ss.selected.split("|")[0])
    slot = ss.selected.split("|")[1]
    slot_label = dict(SLOTS)[slot]
    wd = WEEKDAY_LABELS[d.weekday()]
    entry = state["responses"].get(ss.selected, {})
    contacts = state["contacts"]

    st.subheader(f"📋 {d.month}/{d.day}（{wd}）{slot_label} の参加状況")

    # 状態ごとに参加者名を表示（○はメールがあれば mailto リンク）。
    for mk, label in [("○", "参加"), ("△", "たぶん"), ("×", "不可")]:
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

    # 自分の回答を設定。
    if ss.me:
        st.write(f"**あなた（{ss.me}）の回答:**")
        cur = entry.get(ss.me, "")
        bcols = st.columns(4)
        for col_btn, (val, lab) in zip(
            bcols, [("○", "○ 参加"), ("△", "△ たぶん"), ("×", "× 不可"), ("", "クリア")]
        ):
            shown = f"✅ {lab}" if val and cur == val else lab
            if col_btn.button(shown, key=f"ans_{val}", use_container_width=True):
                set_my_status(d, slot, val)
                st.rerun()
    else:
        st.info("サイドバーにお名前を入力すると回答できます。")

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
