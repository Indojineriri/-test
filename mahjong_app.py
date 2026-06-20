"""麻雀の日程調整ツール（月カレンダー）。

候補枠は「金曜の夜」「土・日の昼・夜」。月カレンダーの各マスに出欠プルダウンを置き、
各メンバーが出欠（○参加 / △未定 / ×欠席）を選ぶ。必要人数（既定4人）の○がそろった
枠を成立候補として自動でハイライトする。

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

# 状態色（参加○=緑 / 未定△=黄 / 欠席×=赤）。
COLORS = {"○": "#2e7d32", "△": "#f9a825", "×": "#c62828"}
# 出欠プルダウンの選択肢。表示ラベル → 内部値（"" = 未回答）。
ATTEND_OPTIONS = ["未回答", "○ 参加", "△ 未定", "× 欠席"]
LABEL_TO_VALUE = {"未回答": "", "○ 参加": "○", "△ 未定": "△", "× 欠席": "×"}
VALUE_TO_LABEL = {v: k for k, v in LABEL_TO_VALUE.items()}
STATUS_NAMES = {"○": "参加", "△": "未定", "×": "欠席"}
SLOT_LABEL = {"day": "昼", "night": "夜"}
WEEKDAY_LABELS = ["月", "火", "水", "木", "金", "土", "日"]


def day_slots(d: datetime.date):
    """その日に調整する枠を返す。金=夜のみ / 土日=昼・夜 / それ以外=なし。"""
    wd = d.weekday()
    if wd == 4:  # 金曜
        return ["night"]
    if wd >= 5:  # 土日
        return ["day", "night"]
    return []

ss = st.session_state
ss.setdefault("state", mahjong_store.load())
ss.setdefault("me", "")
# 表示中の年月（その月の1日を保持）。
ss.setdefault("ym", datetime.date.today().replace(day=1))

state = ss.state


def slot_key(d: datetime.date, slot: str) -> str:
    return f"{d.isoformat()}|{slot}"


def my_status(d: datetime.date, slot: str) -> str:
    return state["responses"].get(slot_key(d, slot), {}).get(ss.me, "")


def set_my_status(d: datetime.date, slot: str, value: str) -> None:
    """自分の出欠を保存する。保存前に最新を読み直すので同時更新に強い。"""
    key = slot_key(d, slot)

    def mutate(st_state):
        entry = st_state["responses"].setdefault(key, {})
        if value:
            entry[ss.me] = value
        else:
            entry.pop(ss.me, None)  # 未回答に戻したら削除しておく
            if not entry:
                st_state["responses"].pop(key, None)

    ss.state = mahjong_store.update(mutate)


def counts(d: datetime.date, slot: str) -> dict:
    entry = state["responses"].get(slot_key(d, slot), {})
    return {
        "○": sum(1 for v in entry.values() if v == "○"),
        "△": sum(1 for v in entry.values() if v == "△"),
        "×": sum(1 for v in entry.values() if v == "×"),
    }


# --- Header: name input lives in the main body (sidebar is hidden on mobile) --
st.title("🀄 麻雀 日程調整")

# 名前は手入力。スマホでサイドバーが埋もれないよう本文の最上部に置く。
name = st.text_input("お名前を入力してください", value=ss.me, placeholder="例）山田").strip()
ss.me = name

# 未登録の名前なら登録して保存（最新を読み直してマージ）。
if name and name not in state["members"]:
    ss.state = mahjong_store.update(
        lambda st_state: st_state["members"].append(name)
        if name not in st_state["members"]
        else None
    )
    state = ss.state

if not ss.me:
    st.info("お名前を入力すると出欠を登録できます。")
else:
    st.caption(f"こんにちは、{ss.me} さん。各枠のプルダウンで出欠を選んでください。")

# 設定（必要人数・更新）は折りたたみで本文に。
with st.expander("⚙️ 設定"):
    threshold = st.number_input(
        "成立に必要な人数", min_value=2, max_value=8, value=4, step=1,
        help="○ がこの人数以上そろった枠を成立候補としてハイライトします。",
    )
    st.caption("凡例: ○=参加 / △=未定 / ×=欠席")
    if st.button("最新の状態に更新"):
        ss.state = mahjong_store.load()
        st.rerun()

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

# --- Upcoming candidate days (mobile-friendly vertical list) -----------------
today = datetime.date.today()
weekday_colors = {4: "#00897b", 5: "#1565c0", 6: "#c62828"}  # 金=緑/土=青/日=赤


def render_detail(key: str) -> None:
    """枠の参加者内訳（○/△/× 別の名前）を表示する。"""
    entry = state["responses"].get(key, {})
    for mk, label in STATUS_NAMES.items():
        people = [n for n, v in entry.items() if v == mk]
        body = "、".join(people) if people else "なし"
        st.markdown(
            f"<span style='color:{COLORS[mk]};font-weight:bold'>{mk} {label}"
            f"（{len(people)}）</span>: {body}",
            unsafe_allow_html=True,
        )


st.caption("候補は 金=夜 / 土日=昼・夜。プルダウンで出欠を選ぶだけです。")

# その月の候補日（金・土・日）のうち、今日以降のものを並べる。
days = [
    d
    for day in range(1, calendar.monthrange(ym.year, ym.month)[1] + 1)
    for d in [datetime.date(ym.year, ym.month, day)]
    if day_slots(d) and d >= today
]

if not days:
    st.info("この月に調整できる日はありません。「次の月 ▶」で来月を確認してください。")

for d in days:
    wd = WEEKDAY_LABELS[d.weekday()]
    color = weekday_colors[d.weekday()]
    today_tag = "（今日）" if d == today else ""
    st.markdown(
        f"<h4 style='margin:0.9em 0 0.1em;color:{color}'>"
        f"{d.month}/{d.day}（{wd}）{today_tag}</h4>",
        unsafe_allow_html=True,
    )

    for slot in day_slots(d):
        key = slot_key(d, slot)
        c = counts(d, slot)
        ok = c["○"] >= threshold

        cur_label = VALUE_TO_LABEL.get(my_status(d, slot), "未回答")
        choice = st.selectbox(
            SLOT_LABEL[slot],
            ATTEND_OPTIONS,
            index=ATTEND_OPTIONS.index(cur_label),
            key=f"sel_{key}",
            disabled=not ss.me,
        )
        if ss.me and LABEL_TO_VALUE[choice] != my_status(d, slot):
            set_my_status(d, slot, LABEL_TO_VALUE[choice])
            st.rerun()

        if ok:
            hint = "　✅ 成立"
        elif 0 < threshold - c["○"] <= 2 and c["○"] > 0:
            hint = f"　あと{threshold - c['○']}人"
        else:
            hint = ""
        st.markdown(
            f"<div style='margin:-0.4em 0 0.2em'>"
            f"<span style='color:{COLORS['○']}'>○{c['○']}</span>　"
            f"<span style='color:{COLORS['△']}'>△{c['△']}</span>　"
            f"<span style='color:{COLORS['×']}'>×{c['×']}</span>"
            f"<span style='color:#888'>{hint}</span></div>",
            unsafe_allow_html=True,
        )

    # 参加者の内訳は折りたたみで（回答があるときだけ）。
    if any(sum(counts(d, s).values()) > 0 for s in day_slots(d)):
        with st.expander("👥 参加状況を見る"):
            for slot in day_slots(d):
                st.markdown(f"**{SLOT_LABEL[slot]}**")
                render_detail(slot_key(d, slot))

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
    for d, slot, o, entry in sorted(candidates, key=lambda x: (x[0], x[1])):
        slot_label = SLOT_LABEL[slot]
        wd = WEEKDAY_LABELS[d.weekday()]
        names = "、".join(n for n, v in entry.items() if v == "○")
        st.markdown(
            f"- **{d.month}/{d.day}（{wd}）{slot_label}** — ○{o}人: {names}"
        )
