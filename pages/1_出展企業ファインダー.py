"""出展企業ファインダー（Streamlit ページ）。

出展企業データに対し、ユーザーが自由記述で書いた「探しているもの」に近い企業を
Claude が意味的に探してランキング表示・CSV 出力する。データは scraper の JSON を
取り込むか、（通信が許可された環境なら）この画面から直接取得もできる。
"""

import hashlib
import json
import os

import pandas as pd
import streamlit as st

import claude_client
import config
import data_store
import exhibitor_finder as finder

CACHE_DIR = os.environ.get("ASSESSMENT_CACHE_DIR", ".assessment_cache")


def _run_id(exhibitors: list[dict], query: str) -> str:
    """判定結果キャッシュの ID。出展企業集合・クエリが変われば別キャッシュ。

    クエリを変えると評価の意味が変わるため、それを ID に織り込み、変更時は
    古い結果を再利用せず新たに評価する（古いキャッシュは別 ID で残る）。
    """
    keys = sorted(finder.exhibitor_key(e) for e in exhibitors)
    sig = "\n".join(keys) + "\n--query--\n" + (query or "")
    return hashlib.sha1(sig.encode("utf-8")).hexdigest()[:12]


def _cache_path(run_id: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{run_id}.jsonl")


def _load_cache(path: str) -> dict:
    """key -> ExhibitorMatch のマップを復元（無ければ空）。"""
    done: dict = {}
    if not os.path.exists(path):
        return done
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                k, a = finder.assessment_from_record(json.loads(line))
                done[k] = a  # 同一キーは後勝ち（再判定を反映）
            except Exception:  # noqa: BLE001
                continue
    return done


def _append_cache(path: str, key: str, match) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(finder.assessment_to_record(key, match), ensure_ascii=False) + "\n")


st.set_page_config(page_title="出展企業ファインダー", page_icon="🔎", layout="wide")

ss = st.session_state
ss.setdefault("exhibitors", None)  # list[dict]
ss.setdefault("restored_note", None)

# 保存済みのスクレイピングデータがあれば自動で復元（前回取得分を反映）。
if ss.exhibitors is None:
    _saved = data_store.load_exhibitors()
    if _saved:
        ss.exhibitors = _saved
        ss.restored_note = f"保存済みデータを復元しました（{len(_saved)} 社）。"

st.title("🔎 出展企業ファインダー")
st.caption(
    "あなたのコメント（探しているもの）に近い出展企業を、キーワードの字面一致ではなく "
    "意図への意味的な近さで Claude が探し、近い順に並べます。"
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

    key_ready = bool(api_key) or bool(os.getenv("ANTHROPIC_API_KEY"))
    if not key_ready:
        st.warning("API キーが未設定です。")


def client():
    return claude_client.get_client(api_key or None)


# --- Step 1: データ取得 ------------------------------------------------------
st.header("① 出展企業データの取得")
if ss.restored_note:
    st.info("💾 " + ss.restored_note + "（再取得すると上書きされます）")
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
            where = data_store.save_exhibitors(data)
            st.success(f"{len(data)} 社を読み込み、保存しました（{where}）。")
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
            bar.empty()
            where = data_store.save_exhibitors(ss.exhibitors)
            st.success(f"{len(items)} 社を取得し、保存しました（{where}）。")
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

# --- Step 2: 探したいもの（自由記述） ----------------------------------------
st.header("② 探したいもの")
query = st.text_area(
    "あなたのコメント（自由記述）",
    height=110,
    placeholder="例）製薬の無菌アイソレータ内で使える、力覚センサ付きの小型ロボットハンド。"
    "Sim2Realで動作学習しているところだと尚良い。",
    help="ここに書いた内容に近い企業を、意図（用途・技術・応用領域）への意味的な近さで探します。",
).strip()

# --- Step 3: 判定 ------------------------------------------------------------
st.header("③ 近い企業を探す")
can_run = bool(ss.exhibitors) and bool(query) and key_ready
if not can_run:
    st.caption("データ取得・コメント入力・API キーが揃うと実行できます。")

# 判定済みキャッシュを読み込む（コメントが変われば別キャッシュ＝再評価される）。
# 手元に無ければ GCS から復元する（Cloud Run 再起動後も結果が残る）。
done_map: dict = {}
pending: list[dict] = []
if ss.exhibitors and query:
    run_id = _run_id(ss.exhibitors, query)
    cache_path = _cache_path(run_id)
    if not os.path.exists(cache_path) and data_store.pull_assessment_cache(run_id, cache_path):
        st.caption("☁️ 保存済みの結果を復元しました。")
    done_map = _load_cache(cache_path)
    pending = [ex for ex in ss.exhibitors if finder.exhibitor_key(ex) not in done_map]
    c1, c2, c3 = st.columns([2, 2, 1])
    c1.metric("判定済み", f"{len(done_map)} 社")
    c2.metric("未判定", f"{len(pending)} 社")
    if c3.button("結果を消す", help="このコメントの判定結果を消して最初からやり直します"):
        data_store.delete_assessment_cache(run_id, cache_path)
        st.rerun()

limit = st.number_input(
    "今回判定する社数（未判定の先頭から / コスト・時間調整用）",
    min_value=1,
    max_value=max(len(pending), 1),
    value=min(20, len(pending)) if pending else 1,
)

btn_label = "未判定を続きから判定する" if done_map else "近い企業を探す"
if st.button(btn_label, type="primary", disabled=not (can_run and pending)):
    targets = pending[: int(limit)]
    bar = st.progress(0.0, text="判定中…")
    live = st.empty()
    cli = client()

    def _prog(done, total, name):
        bar.progress(min(done / total, 1.0), text=f"{done}/{total}  {name}")

    # 1 社完了ごとにローカルへ追記し、数件ごとに GCS へミラー（途中切断にも強い）。
    sync = {"n": 0}

    def _on_result(key, ex, match):
        _append_cache(cache_path, key, match)
        sync["n"] += 1
        if sync["n"] % 5 == 0:
            data_store.push_assessment_cache(run_id, cache_path)
        live.caption(f"✓ {match.company} まで保存済み")

    err_count = {"n": 0}

    def _on_error(ex, msg):
        err_count["n"] += 1

    try:
        finder.assess_many(
            cli, targets, query, model,
            on_result=_on_result, on_error=_on_error, progress=_prog,
        )
    except Exception as e:  # noqa: BLE001
        st.error(f"判定が中断しました（完了分は保存済み・再開できます）: {claude_client.format_api_error(e)}")
    finally:
        # 完了・中断いずれでも最新のローカル内容を GCS に確実に反映。
        data_store.push_assessment_cache(run_id, cache_path)
    bar.empty()
    if err_count["n"]:
        st.warning(f"{err_count['n']} 社でエラー（スキップ）。再クリックで再試行されます。")
    st.rerun()  # キャッシュを読み直して結果表示を最新化

# --- Step 4: 結果 ------------------------------------------------------------
# 表示は常にキャッシュ（=確定保存分）から組み立てる。中断していても残っている。
results = sorted(done_map.values(), key=lambda a: a.score, reverse=True) if done_map else None

if results:
    st.header("④ 結果（近い順）")
    rows = [
        {"企業": a.company, "近さ": a.score, "要約": a.summary, "根拠": a.rationale}
        for a in results
    ]
    df = pd.DataFrame(rows).sort_values("近さ", ascending=False)

    min_score = st.slider("近さの下限でフィルタ", 0, 100, 50, step=5)
    shown = df[df["近さ"] >= min_score]
    st.dataframe(shown, use_container_width=True, hide_index=True)

    csv = shown.to_csv(index=False).encode("utf-8-sig")
    st.download_button("CSV をダウンロード", data=csv, file_name="exhibitor_matches.csv", mime="text/csv")

    st.subheader("根拠の詳細")
    for a in results:
        if a.score < min_score:
            continue
        with st.expander(f"{a.company} — 近さ {a.score}"):
            st.write(a.summary)
            st.markdown(a.rationale)
            if a.signals:
                st.caption("手がかり: " + " / ".join(a.signals))
