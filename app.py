import os

import streamlit as st

import config
import claude_client
import gcs_store
import ppt_generator
import ppt_parser

st.set_page_config(page_title="定例会議 資料作成サポート", page_icon="📊", layout="wide")

ss = st.session_state
ss.setdefault("slides", None)
ss.setdefault("minutes_text", None)
ss.setdefault("gcs_uri", None)
ss.setdefault("understanding", None)
ss.setdefault("material", None)

# --- Sidebar: settings -------------------------------------------------------
with st.sidebar:
    st.header("設定")
    api_key = st.text_input(
        "Anthropic API Key",
        type="password",
        value="",
        help="未入力の場合は環境変数 ANTHROPIC_API_KEY を使用します。",
    )
    model = st.selectbox("モデル", config.AVAILABLE_MODELS, index=0)
    include_images = st.checkbox(
        "スライド画像も解析に含める（高精度・高コスト）", value=True
    )
    st.divider()
    if config.GCS_BUCKET:
        st.success(f"GCSバケット: {config.GCS_BUCKET}")
    else:
        st.info("GCS_BUCKET 未設定。アップロードはスキップされます。")

    key_ready = bool(api_key) or bool(os.getenv("ANTHROPIC_API_KEY"))
    if not key_ready:
        st.warning("API キーが未設定です。")


def client():
    return claude_client.get_client(api_key or None)


st.title("📊 定例会議 資料作成サポート")

# --- Step 1: upload ----------------------------------------------------------
st.header("① 資料のアップロード")
col1, col2 = st.columns(2)
with col1:
    pptx_file = st.file_uploader("PPT（.pptx）", type=["pptx"])
with col2:
    minutes_file = st.file_uploader(
        "議事録（任意）", type=["txt", "md", "pdf", "docx"]
    )

if pptx_file is not None and st.button("② 取り込み・GCS保存", type="primary"):
    pptx_bytes = pptx_file.getvalue()

    # ② Save to GCS (best-effort; the app stays usable without GCS configured).
    ss.gcs_uri = None
    if config.GCS_BUCKET:
        try:
            ss.gcs_uri = gcs_store.upload_bytes(
                pptx_bytes,
                pptx_file.name,
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                config.GCS_BUCKET,
                config.GCS_PREFIX,
            )
            st.success(f"GCSに保存しました: {ss.gcs_uri}")
        except Exception as e:  # noqa: BLE001
            st.warning(f"GCS保存に失敗しました（解析は続行します）: {e}")

    # Parse deck text first (always works), then optionally rasterise slides.
    with st.spinner("PPTを解析中..."):
        try:
            ss.slides = ppt_parser.parse_text(pptx_bytes)
        except Exception as e:  # noqa: BLE001
            st.error(f"PPTの解析に失敗しました: {e}")
            ss.slides = None
        if ss.slides and include_images:
            try:
                ppt_parser.render_images_into(
                    pptx_bytes, ss.slides, config.IMAGE_LONG_EDGE
                )
            except Exception as e:  # noqa: BLE001
                st.warning(
                    "スライドの画像化に失敗しました（LibreOffice/Impress が必要です）。"
                    f"テキストのみで続行します: {e}"
                )

    # Optional minutes.
    ss.minutes_text = None
    if minutes_file is not None:
        try:
            ss.minutes_text = ppt_parser.extract_minutes_text(
                minutes_file.name, minutes_file.getvalue()
            )
        except Exception as e:  # noqa: BLE001
            st.warning(f"議事録の読み込みに失敗しました: {e}")

    # Reset downstream state.
    ss.understanding = None
    ss.material = None

if ss.slides:
    st.caption(
        f"取り込み済み: {len(ss.slides)} スライド"
        + ("（画像あり）" if any(s.image_b64 for s in ss.slides) else "（テキストのみ）")
        + ("／議事録あり" if ss.minutes_text else "")
    )
    with st.expander("抽出テキストを確認"):
        for s in ss.slides:
            st.markdown(f"**スライド {s.index}**")
            st.text(s.text or "(テキストなし)")

# --- Step 3: understand ------------------------------------------------------
st.header("③ 内容の理解")
if ss.slides and st.button("資料を理解する", disabled=not key_ready):
    usage_sink: dict = {}
    gen = claude_client.stream_analysis(
        client(), ss.slides, ss.minutes_text, model, usage_sink
    )
    try:
        ss.understanding = st.write_stream(gen)
    except Exception as e:  # noqa: BLE001
        st.error(f"理解の生成に失敗しました: {e}")
    if usage_sink.get("usage"):
        u = usage_sink["usage"]
        st.caption(
            f"tokens — in:{u.input_tokens} out:{u.output_tokens} "
            f"cache_write:{u.cache_creation_input_tokens} cache_read:{u.cache_read_input_tokens}"
        )
elif ss.understanding:
    st.markdown(ss.understanding)

# --- Step 4: agenda & messages ----------------------------------------------
st.header("④ 論点入力 → アジェンダ・メッセージ作成")
discussion_points = st.text_area(
    "論点（会議で扱いたいテーマ・課題などを入力）",
    height=140,
    placeholder="例）\n- 今期の売上未達要因と打ち手\n- 新機能のリリース可否判断\n- 来月の重点施策",
)

if st.button(
    "アジェンダ・メッセージを作成",
    type="primary",
    disabled=not (ss.slides and ss.understanding and discussion_points.strip() and key_ready),
):
    with st.spinner("生成中..."):
        try:
            material, usage = claude_client.generate_material(
                client(),
                ss.slides,
                ss.minutes_text,
                ss.understanding,
                discussion_points,
                model,
            )
            ss.material = material
            st.caption(
                f"tokens — in:{usage.input_tokens} out:{usage.output_tokens} "
                f"cache_read:{usage.cache_read_input_tokens}"
            )
        except Exception as e:  # noqa: BLE001
            st.error(f"生成に失敗しました: {e}")

if ss.material:
    st.markdown(claude_client.material_to_markdown(ss.material))

    # --- Step 5: PPT output --------------------------------------------------
    st.header("⑤ PPT出力")
    pptx_out = ppt_generator.build_pptx(ss.material)
    st.download_button(
        "アジェンダPPTをダウンロード",
        data=pptx_out,
        file_name="meeting_agenda.pptx",
        mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )
