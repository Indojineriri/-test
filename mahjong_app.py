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

# 回答の選択肢。クリックするたびにこの順で循環する（"" = 未回答）。
STATUS_CYCLE = ["", "○", "△", "×"]
SLOTS = [("day", "昼"), ("night", "夜")]
WEEKDAY_LABELS = ["月", "火", "水", "木", "金", "土", "日"]

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

    members = state["members"]
    options = ["（新しい名前を入力）"] + members
    default_idx = options.index(ss.me) if ss.me in members else 0
    choice = st.selectbox("自分の名前", options, index=default_idx)
    if choice == "（新しい名前を入力）":
        new_name = st.text_input("名前を入力してEnter")
        if new_name and new_name not in members:
            members.append(new_name)
            mahjong_store.save(state)
            ss.me = new_name
            st.rerun()
        ss.me = new_name or ""
    else:
        ss.me = choice

    threshold = st.number_input(
        "成立に必要な人数", min_value=2, max_value=8, value=4, step=1,
        help="○ がこの人数以上そろった枠を成立候補としてハイライトします。",
    )

    st.divider()
    st.caption("凡例: ○=参加 / △=たぶん / ×=不可")
    st.caption(f"保存先 — {mahjong_store.location_label()}")
    if st.button("最新の状態に更新"):
        ss.state = mahjong_store.load()
        st.rerun()


st.title("🀄 麻雀 日程調整")
if not ss.me:
    st.info("まずはサイドバーで自分の名前を選択（または入力）してください。")

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

            # 日付の見出し（当月外は薄く、今日は太字）。
            num = f"**{d.day}**" if d == today else str(d.day)
            if not in_month:
                col.markdown(
                    f"<span style='color:#bbb'>{d.day}</span>",
                    unsafe_allow_html=True,
                )
            else:
                col.markdown(num)

            if not (in_month and is_weekend):
                continue

            for slot, slot_label in SLOTS:
                c = counts(d, slot)
                ok = c["○"] >= threshold
                mark = my_status(d, slot) or "－"
                btn_label = f"{slot_label} {mark}"
                if st.button(
                    btn_label,
                    key=slot_key(d, slot),
                    use_container_width=True,
                    disabled=not ss.me,
                    type="primary" if ok else "secondary",
                ):
                    cur = my_status(d, slot)
                    nxt = STATUS_CYCLE[(STATUS_CYCLE.index(cur) + 1) % len(STATUS_CYCLE)]
                    set_my_status(d, slot, nxt)
                    st.rerun()
                badge = "✅ " if ok else ""
                st.caption(f"{badge}○{c['○']} △{c['△']} ×{c['×']}")

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
        slot_label = dict(SLOTS)[slot]
        wd = WEEKDAY_LABELS[d.weekday()]
        yes = "、".join(n for n, v in entry.items() if v == "○")
        st.markdown(f"- **{d.month}/{d.day}（{wd}）{slot_label}** — ○{o}人: {yes}")
