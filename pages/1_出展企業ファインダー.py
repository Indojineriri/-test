"""出展企業ファインダー（Streamlit ページ）。

展示会の出展企業データに対し、Claude で指定テーマへの関連度を意味的に判定し、
ランキング表示・CSV 出力する。データは scraper の JSON を取り込むか、
（通信が許可された環境なら）この画面から直接取得もできる。
"""

import io
import json

import pandas as pd
import streamlit as st

import claude_client
import config
import exhibitor_finder as finder

st.set_page_config(page_title="出展企業ファインダー", page_icon="🔎", layout="wide")

ss = st.session_state
ss.setdefault("exhibitors", None)  # list[dict]
ss.setdefault("assessments", None)  # list[ExhibitorAssessment]
ss.setdefault("themes", [dict(t) for t in finder.DEFAULT_THEMES])

st.title("🔎 出展企業ファインダー")
st.caption(
    "出展企業データに対し、キーワードの字面一致ではなく技術的な隣接性まで踏まえて "
    "Claude が各テーマへの関連度を判定します。"
)

# --- Sidebar: settings -------------------------------------------------------
with st.sidebar:
    st.header("設定")
    api_key = st.text_input(
        "Anthropic API Key",
        type="password",
        help="未入力の場合は環境変数 ANTHROPIC_API_KEY を使用します。",
    )
    model = st.selectbox("モデル", config.AVAILABLE_MODELS, index=0)
    import os

    key_ready = bool(api_key) or bool(os.getenv("ANTHROPIC_API_KEY"))
    if not key_ready:
        st.warning("API キーが未設定です。")


def client():
    return claude_client.get_client(api_key or None)


# --- Step 1: データ取得 ------------------------------------------------------
st.header("① 出展企業データの取得")
tab_upload, tab_scrape = st.tabs(["JSON を取り込む", "サイトから直接取得"])

with tab_upload:
    up = st.file_uploader(
        "exhibitor_scraper.py が出力した JSON", type=["json"], key="json_up"
    )
    if up is not None and st.button("読み込む", key="load_json"):
        try:
            data = json.load(up)
            if not isinstance(data, list):
                raise ValueError("JSON はオブジェクトの配列である必要があります。")
            ss.exhibitors = data
            ss.assessments = None
            st.success(f"{len(data)} 社を読み込みました。")
        except Exception as e:  # noqa: BLE001
            st.error(f"読み込みに失敗しました: {e}")

with tab_scrape:
    st.caption(
        "RTJ サイトを ChargeNo 連番で巡回します。"
        "**外向き通信が許可された環境でのみ動作**します（サンドボックス等では不可）。"
    )
    c1, c2 = st.columns(2)
    max_no = c1.number_input("最大 ChargeNo", min_value=10, max_value=3000, value=800, step=50)
    delay = c2.number_input("リクエスト間隔（秒）", min_value=0.0, max_value=5.0, value=0.7, step=0.1)
    if st.button("取得を開始", key="run_scrape"):
        import exhibitor_scraper as scraper
        from dataclasses import asdict

        bar = st.progress(0.0, text="取得中…")

        def _prog(done, total, found):
            bar.progress(min(done / total, 1.0), text=f"{done}/{total} 件確認 / {found} 社取得")

        try:
            with st.spinner("サイトを巡回中…"):
                items = scraper.scrape(int(max_no), float(delay), progress=_prog)
            ss.exhibitors = [asdict(x) for x in items]
            ss.assessments = None
            bar.empty()
            st.success(f"{len(items)} 社を取得しました。")
        except Exception as e:  # noqa: BLE001
            bar.empty()
            st.error(f"取得に失敗しました（通信環境を確認してください）: {e}")

if ss.exhibitors:
    st.info(f"対象データ: {len(ss.exhibitors)} 社")
    with st.expander("取得データを確認（先頭20件）"):
        st.dataframe(
            pd.DataFrame(ss.exhibitors)[
                [c for c in ("name", "categories", "website", "url") if c in ss.exhibitors[0]]
            ].head(20),
            use_container_width=True,
        )

# --- Step 2: テーマ設定 ------------------------------------------------------
st.header("② 判定テーマ")
st.caption("既定の5テーマに加え、自由に追加・編集できます。desc（説明）が意味的判定の精度を左右します。")
theme_df = st.data_editor(
    pd.DataFrame(ss.themes),
    num_rows="dynamic",
    use_container_width=True,
    column_config={
        "name": st.column_config.TextColumn("テーマ名", required=True),
        "desc": st.column_config.TextColumn("説明（技術的な射程）", width="large"),
    },
    key="theme_editor",
)
ss.themes = [
    {"name": str(r["name"]).strip(), "desc": str(r.get("desc", "") or "").strip()}
    for _, r in theme_df.iterrows()
    if str(r.get("name", "")).strip()
]

# --- Step 3: 判定 ------------------------------------------------------------
st.header("③ Claude で関連度を判定")
can_run = bool(ss.exhibitors) and bool(ss.themes) and key_ready
if not can_run:
    st.caption("データ取得・テーマ設定・API キーが揃うと実行できます。")

limit = st.number_input(
    "今回判定する社数（先頭から / コスト調整用）",
    min_value=1,
    max_value=len(ss.exhibitors) if ss.exhibitors else 1,
    value=min(50, len(ss.exhibitors)) if ss.exhibitors else 1,
)

if st.button("関連度を判定する", type="primary", disabled=not can_run):
    targets = ss.exhibitors[: int(limit)]
    bar = st.progress(0.0, text="判定中…")

    def _prog(done, total, name):
        bar.progress(min(done / total, 1.0), text=f"{done}/{total}  {name}")

    with st.spinner("Claude が各社を評価中…"):
        try:
            results, errors = finder.assess_many(client(), targets, ss.themes, model, progress=_prog)
            ss.assessments = results
            bar.empty()
            st.success(f"{len(results)} 社を判定しました。")
            if errors:
                st.warning(f"{len(errors)} 社でエラー（スキップ）。例: {errors[0][1]}")
        except Exception as e:  # noqa: BLE001
            bar.empty()
            st.error(f"判定に失敗しました: {claude_client.format_api_error(e)}")

# --- Step 4: 結果 ------------------------------------------------------------
if ss.assessments:
    st.header("④ 結果（関連度ランキング）")
    theme_names = [t["name"] for t in ss.themes]

    rows = []
    for a in ss.assessments:
        row = {"企業": a.company, "最高スコア": a.max_score, "主テーマ": a.top_theme}
        score_map = {s.theme: s.score for s in a.theme_scores}
        for tn in theme_names:
            row[tn] = score_map.get(tn, "")
        row["要約"] = a.summary
        rows.append(row)
    df = pd.DataFrame(rows).sort_values("最高スコア", ascending=False)

    min_score = st.slider("最高スコアの下限でフィルタ", 0, 100, 50, step=5)
    shown = df[df["最高スコア"] >= min_score]
    st.dataframe(shown, use_container_width=True, hide_index=True)

    csv = shown.to_csv(index=False).encode("utf-8-sig")
    st.download_button("CSV をダウンロード", data=csv, file_name="exhibitor_ranking.csv", mime="text/csv")

    st.subheader("根拠の詳細")
    for a in sorted(ss.assessments, key=lambda x: x.max_score, reverse=True):
        if a.max_score < min_score:
            continue
        with st.expander(f"{a.company} — 最高 {a.max_score}（{a.top_theme}）"):
            st.write(a.summary)
            for s in sorted(a.theme_scores, key=lambda x: x.score, reverse=True):
                st.markdown(f"**{s.theme}: {s.score}**  \n{s.rationale}")
                if s.signals:
                    st.caption("シグナル: " + " / ".join(s.signals))
