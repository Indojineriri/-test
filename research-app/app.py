import os

import streamlit as st

import csv_export
import deep_research
import ppt_export
import research
import scraper
from models import Case, CaseList

st.set_page_config(page_title="リサーチ資料作成", page_icon="🔎", layout="wide")

ss = st.session_state
ss.setdefault("findings", None)
ss.setdefault("cases", None)
ss.setdefault("arxiv_used", None)

AVAILABLE_MODELS = ["claude-opus-4-7", "claude-sonnet-4-6"]


with st.sidebar:
    st.header("設定")
    api_key = st.text_input(
        "Anthropic API Key",
        type="password",
        value="",
        help="未入力の場合は環境変数 ANTHROPIC_API_KEY を使用します。",
    )
    model = st.selectbox("モデル", AVAILABLE_MODELS, index=0)
    mode = st.radio(
        "リサーチモード",
        ["標準", "ディープリサーチ"],
        index=0,
        help=(
            "標準: 1 リクエストで Web 検索＋arXiv コンテキストを使い結果を返す。\n"
            "ディープリサーチ: エージェントループで複数ターン検索・ページ閲覧・"
            "裏取りを繰り返す。所要時間と費用は増える。"
        ),
    )
    n_cases = st.slider("ピックアップする事例数", min_value=1, max_value=10, value=5)

    if mode == "標準":
        arxiv_n = st.slider("arXiv 候補数（コンテキストとして渡す）", 0, 20, 10)
        max_web_uses = st.slider("Web 検索の最大回数", 0, 15, 8)
    else:
        arxiv_n = 10  # not used in deep mode
        max_iters = st.slider("最大ループ回数", 5, 30, 15)
        max_web_uses = st.slider("Web 検索の最大回数", 5, 50, 25)

    st.divider()
    key_ready = bool(api_key) or bool(os.getenv("ANTHROPIC_API_KEY"))
    if not key_ready:
        st.warning("API キーが未設定です。")


def client():
    return research.get_client(api_key or None)


st.title("🔎 リサーチ資料作成")
st.caption("テーマを入力 → Claude + Web 検索 + arXiv でリサーチ → CSV / PPT でダウンロード")

# --- Step 1: theme ----------------------------------------------------------
st.header("① テーマ入力")
theme = st.text_area(
    "リサーチしたいテーマ",
    height=100,
    placeholder="例）直近のVLA（Vision-Language-Action モデル）に触覚センサを用いた事例をピックアップしてください",
)

# --- Step 2: research --------------------------------------------------------
st.header("② リサーチ実行")

if mode == "標準":
    if st.button(
        "リサーチを実行",
        type="primary",
        disabled=not (theme.strip() and key_ready),
    ):
        with st.spinner("リサーチ中（Web 検索 + arXiv）..."):
            try:
                findings, arxiv_used, usage = research.research(
                    client(),
                    theme=theme,
                    n_cases=n_cases,
                    model=model,
                    arxiv_n=arxiv_n,
                    max_web_uses=max_web_uses,
                )
                ss.findings = findings
                ss.arxiv_used = arxiv_used
                st.caption(
                    f"tokens — in:{usage.input_tokens} out:{usage.output_tokens}"
                )
            except Exception as e:
                st.error(f"リサーチに失敗しました: {research.format_api_error(e)}")
                ss.findings = None

    if ss.findings:
        with st.expander("リサーチ結果（生テキスト）", expanded=False):
            st.markdown(ss.findings)
        with st.expander("使用した arXiv 候補リスト", expanded=False):
            st.text(ss.arxiv_used or "(なし)")

    # --- Step 3 (standard mode): structure ----------------------------------
    st.header("③ 構造化 → 事例リスト")
    if ss.findings and st.button("事例リストを生成（構造化）", disabled=not key_ready):
        with st.spinner("構造化中..."):
            try:
                case_list, usage = research.structure(client(), ss.findings, model=model)
                ss.cases = case_list.cases
                st.caption(
                    f"tokens — in:{usage.input_tokens} out:{usage.output_tokens}"
                )
            except Exception as e:
                st.error(f"構造化に失敗しました: {research.format_api_error(e)}")

else:  # ディープリサーチモード
    st.caption(
        "エージェントが web_search / fetch_url / arxiv_search を多段で呼び出し、"
        "裏取りを繰り返してから構造化された事例リストを直接提出します。"
    )
    if st.button(
        "ディープリサーチを実行",
        type="primary",
        disabled=not (theme.strip() and key_ready),
    ):
        progress_box = st.empty()
        log_lines: list[str] = []

        def _on_progress(p: deep_research.Progress) -> None:
            icon = {
                "think": "💭",
                "web_search": "🔍",
                "fetch": "📄",
                "arxiv": "📚",
                "submit": "✅",
                "nudge": "↪️",
            }.get(p.kind, "•")
            detail = (p.detail or "").replace("\n", " ")
            if len(detail) > 200:
                detail = detail[:200] + "…"
            log_lines.append(f"{icon} **[{p.iteration}] {p.kind}** — {detail}")
            progress_box.markdown("\n\n".join(log_lines[-25:]))

        try:
            case_list, usages = deep_research.deep_research(
                client(),
                theme=theme,
                n_cases=n_cases,
                model=model,
                max_iters=max_iters,
                max_web_uses=max_web_uses,
                on_progress=_on_progress,
            )
            ss.cases = case_list.cases
            ss.findings = None  # not used in deep mode
            total_in = sum(u.input_tokens for u in usages)
            total_out = sum(u.output_tokens for u in usages)
            st.success(
                f"ディープリサーチ完了 — {len(usages)} ターン / "
                f"tokens in:{total_in} out:{total_out}"
            )
        except Exception as e:
            st.error(f"ディープリサーチに失敗しました: {research.format_api_error(e)}")

if ss.cases:
    st.success(f"{len(ss.cases)} 件の事例を取得しました。")

    # Per-case preview with image diagnosis and manual override.
    for i, c in enumerate(ss.cases):
        with st.expander(f"{i+1}. {c.title} — {c.organization} ({c.year or '?'})"):
            st.markdown(f"**見出し**: {c.headline or '(未設定 → 自動生成されます)'}")
            st.markdown(f"**要約**: {c.subtitle}")
            st.markdown("**概要**")
            for x in c.overview[:4]:
                st.markdown(f"- {x}")
            st.markdown("**なぜ難しいのか**")
            for x in c.challenges[:4]:
                st.markdown(f"- {x}")
            st.markdown("**解決した技術的課題**")
            for x in c.solutions[:4]:
                st.markdown(f"- {x}")
            st.markdown(f"**URL**: {c.url}")
            st.markdown(f"**Link text**: {c.link_text}")

            # Manual image_url override.
            new_url = st.text_input(
                "画像 URL（手動オーバーライド）",
                value=c.image_url or "",
                key=f"img_url_{i}",
                help="空欄なら PPT 生成時に og:image / arXiv PDF から自動取得を試みる。",
            )
            if new_url != (c.image_url or ""):
                c.image_url = new_url or None

            col_a, col_b = st.columns([1, 1])
            with col_a:
                if st.button("画像をテストフェッチ", key=f"test_img_{i}"):
                    trace: list[str] = []
                    with st.spinner("取得中..."):
                        got = scraper.get_case_image(c.url, c.image_url, trace=trace)
                    for line in trace:
                        st.text(f"• {line}")
                    if got is not None:
                        st.success(f"取得成功: {len(got[0])} bytes / {got[1]}")
                        st.image(got[0], width=300)
                    else:
                        st.warning("画像を取得できませんでした。")
            with col_b:
                if c.image_url:
                    try:
                        st.image(c.image_url, width=300, caption="現在の image_url")
                    except Exception:
                        st.text(f"プレビュー失敗: {c.image_url}")

# --- Step 4: export ---------------------------------------------------------
st.header("④ 出力")
if ss.cases:
    col1, col2 = st.columns(2)
    with col1:
        csv_bytes = csv_export.to_csv_bytes(ss.cases)
        st.download_button(
            "事例リストを CSV でダウンロード",
            data=csv_bytes,
            file_name="cases.csv",
            mime="text/csv",
        )
    with col2:
        if st.button("PPT を生成", type="primary"):
            with st.spinner("PPT 生成中..."):
                try:
                    pptx_bytes = ppt_export.build_pptx(ss.cases)
                    st.session_state["_pptx_bytes"] = pptx_bytes
                except Exception as e:
                    st.error(f"PPT 生成に失敗しました: {e}")
        if "_pptx_bytes" in st.session_state:
            st.download_button(
                "PPT をダウンロード",
                data=st.session_state["_pptx_bytes"],
                file_name="research_cases.pptx",
                mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            )
else:
    st.info("リサーチを実行して事例リストを生成すると、ここから出力できます。")
