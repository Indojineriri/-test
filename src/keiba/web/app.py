"""③④⑤を見据えた keiba の HTTP サービス（Flask）。

Cloud Run 上で動く WSGI アプリ。エントリポイントは `keiba.web.app:app`。

【運用方針】Cloud Run は **GCS に貯めたデータの参照・予想・可視化に専念**する。
データ取得（スクレイピング）は netkeiba が GCP/データセンタ IP を 403 で弾くため
**手元の回線で `keiba.cli fetch` を実行**して GCS に保存する（取得は Cloud Run で
行わない）。そのため取得系エンドポイント（/fetch・/diag）は **既定で無効**。
どうしても有効化したい開発時のみ KEIBA_ENABLE_FETCH=1 を設定する。

環境変数:
    KEIBA_STORAGE_URI   参照するデータの場所。Cloud Run では gs://<bucket>/keiba。
                        未設定ならローカル /tmp/keiba（開発用）。
    KEIBA_ENABLE_FETCH  "1"/"true" で取得系(/fetch・/diag)を有効化（既定 無効）。
    KEIBA_FETCH_WAIT    リクエスト間ウェイト秒（既定 1.5。取得有効時のみ）
    KEIBA_MAX_HISTORY   1頭あたり遡る過去レース数の上限（既定 無制限）
    KEIBA_FETCH_TOKEN   設定すると取得系に ?token= or X-Auth-Token を要求（簡易保護）
    KEIBA_PROXY         外向きプロキシ URL（取得有効時のみ。通常は使わない）

エンドポイント（参照・予想 + 任意の取得系）:
    GET  /healthz                     ヘルスチェック
    GET  /                            使い方 + 保存済みレース一覧（HTML）
    GET  /races                       保存済みレースID一覧（JSON）
    GET  /races/<race_id>             メタ + 取得サマリ（JSON）
    GET  /races/<race_id>/<name>.csv  entries/results/races/horses の CSV
    GET  /predict/<race_id>           ⑤ML予想: 複勝確率ランキング（JSON）
    GET  /genai-predict/<race_id>     ④⑤生成AI予想: 示唆+予想（JSON。要 ANTHROPIC_API_KEY）
    -- 以下は KEIBA_ENABLE_FETCH=1 のときだけ動作（既定 無効=403）--
    GET  /diag                        netkeiba への到達性チェック
    GET/POST /fetch?race_id=...       取得して保存
"""

from __future__ import annotations

import os

from flask import Flask, Response, jsonify, request

from ..collect.netkeiba_client import AccessBlockedError
from ..service import (check_connectivity, fetch_and_store, list_races,
                       load_meta, read_csv_text)
from ..storage import Storage

app = Flask(__name__)


def _store() -> Storage:
    uri = os.environ.get("KEIBA_STORAGE_URI", "/tmp/keiba")
    return Storage.from_uri(uri)


def _check_token() -> bool:
    """KEIBA_FETCH_TOKEN 設定時のみ、トークン一致を要求する簡易保護。"""
    expected = os.environ.get("KEIBA_FETCH_TOKEN")
    if not expected:
        return True
    given = request.headers.get("X-Auth-Token") or request.args.get("token")
    return given == expected


def _fetch_enabled() -> bool:
    """取得系(/fetch・/diag)が有効か。Cloud Run は参照専用なので既定 無効。"""
    return os.environ.get("KEIBA_ENABLE_FETCH", "").lower() in ("1", "true", "yes")


def _fetch_disabled_response():
    return jsonify(
        error="fetch is disabled on this deployment",
        detail="このデプロイは参照・予想専用です。データ取得は手元の回線で "
               "`python3 -m keiba.cli fetch --race-id <id> --out gs://<bucket>/keiba` "
               "を実行してください（netkeiba は GCP/DC IP を 403 で弾くため）。",
    ), 403


@app.get("/healthz")
def healthz():
    return jsonify(status="ok")


@app.get("/diag")
def diag():
    """netkeiba に実際に到達できるか確認する。Cloud Run からの IP ブロック診断用。

    例: {"ok": true, "status": 200, "via_proxy": false}
        {"ok": false, "status": 403, "blocked": true, ...}  ← IP ブロックの疑い
    """
    if not _fetch_enabled():
        return _fetch_disabled_response()
    if not _check_token():
        return jsonify(error="unauthorized"), 401
    result = check_connectivity(proxy=os.environ.get("KEIBA_PROXY"))
    # 到達できていれば 200、ブロック/失敗なら 502 で返す（監視しやすく）
    code = 200 if result.get("ok") else 502
    return jsonify(result), code


@app.get("/")
def index():
    store = _store()
    races = list_races(store)
    genai_on = bool(os.environ.get("ANTHROPIC_API_KEY"))

    if races:
        rows = []
        for r in races:
            genai_btn = (
                f'<button onclick="run(\'genai-predict\',\'{r}\',this)">🤖 生成AI予想</button>'
                if genai_on else
                '<button disabled title="ANTHROPIC_API_KEY 未設定">🤖 生成AI予想(無効)</button>'
            )
            rows.append(
                f'<tr><td><b>{r}</b></td>'
                f'<td><a href="/races/{r}">メタ</a> / '
                f'<a href="/races/{r}/entries.csv">出馬表CSV</a></td>'
                f'<td><button onclick="run(\'predict\',\'{r}\',this)">📊 ML予想</button> '
                f'{genai_btn}</td></tr>'
                f'<tr><td colspan="3"><div class="result" id="res-{r}"></div></td></tr>'
            )
        race_table = (
            '<table><thead><tr><th>レースID</th><th>データ</th>'
            '<th>予想を実行</th></tr></thead><tbody>'
            + "".join(rows) + '</tbody></table>'
        )
    else:
        race_table = "<p><em>まだ取得済みレースはありません。</em></p>"

    fetch_note = (
        '<p class="note">データ取得（スクレイピング）は Cloud Run では行いません。'
        '手元の回線で <code>python3 -m keiba.cli fetch ...</code> を実行し GCS に保存してください。</p>'
    )

    html = f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>keiba 予想サービス</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 2rem; line-height: 1.6; color: #222; }}
  h1 {{ font-size: 1.5rem; }}
  table {{ border-collapse: collapse; width: 100%; max-width: 900px; }}
  th, td {{ border-bottom: 1px solid #ddd; padding: 8px 10px; text-align: left; vertical-align: top; }}
  th {{ background: #f4f4f4; }}
  button {{ font-size: 0.9rem; padding: 6px 12px; margin: 2px; cursor: pointer;
           border: 1px solid #3b7dd8; background: #3b7dd8; color: #fff; border-radius: 4px; }}
  button:disabled {{ background: #aaa; border-color: #aaa; cursor: not-allowed; }}
  button.secondary {{ background:#fff; color:#3b7dd8; }}
  .result {{ margin: 4px 0; }}
  .result:empty {{ display: none; }}
  .card {{ background:#f8f9fb; border:1px solid #dde; border-radius:6px; padding:10px 14px; margin:6px 0; }}
  .rank {{ font-variant-numeric: tabular-nums; }}
  .mark {{ font-weight: bold; }}
  .note {{ color:#666; font-size:0.9rem; }}
  .spinner {{ color:#3b7dd8; }}
  code {{ background:#eef; padding:1px 4px; border-radius:3px; }}
</style></head><body>
<h1>keiba — 競馬予想サービス</h1>
<p>参照先: <code>{store.uri()}</code>　生成AI予想: <b>{'有効' if genai_on else '無効(キー未設定)'}</b></p>
{fetch_note}
<h2>レースを選んで予想を実行</h2>
{race_table}

<script>
async function run(kind, raceId, btn) {{
  const box = document.getElementById('res-' + raceId);
  box.innerHTML = '<span class="spinner">⏳ 予想中…' +
    (kind === 'genai-predict' ? '（生成AIは数十秒かかります）' : '') + '</span>';
  btn.disabled = true;
  try {{
    const resp = await fetch('/' + kind + '/' + raceId);
    const data = await resp.json();
    if (!resp.ok) {{ box.innerHTML = '<div class="card">⚠ ' + (data.error || resp.status) + '</div>'; return; }}
    box.innerHTML = (kind === 'predict') ? renderML(data) : renderGenAI(data);
  }} catch (e) {{
    box.innerHTML = '<div class="card">⚠ 通信エラー: ' + e + '</div>';
  }} finally {{ btn.disabled = false; }}
}}

function renderML(d) {{
  const marks = ['◎','○','▲','△','△'];
  let rows = d.ranking.slice(0, 8).map((r, i) =>
    `<tr><td class="mark">${{marks[i] || (i+1)}}</td>` +
    `<td class="rank">${{r.horse_no}}</td><td>${{r.horse_name}}</td>` +
    `<td class="rank">${{(r.show_prob*100).toFixed(0)}}%</td>` +
    `<td>${{r.pit_running_style || ''}}</td></tr>`).join('');
  return '<div class="card"><b>📊 ML予想（複勝確率）</b>' +
    `<div class="note">学習: ${{d.trained_on.join(', ')}}</div>` +
    '<table><thead><tr><th>印</th><th>馬番</th><th>馬名</th><th>複勝率</th><th>脚質</th></tr></thead>' +
    '<tbody>' + rows + '</tbody></table></div>';
}}

function renderGenAI(d) {{
  const ins = (d.insights.insights || []).map(x =>
    `<li>[${{x.weight}}] ${{x.pattern}}</li>`).join('');
  const marks = ['◎','○','▲','△','△'];
  const rows = (d.prediction.ranking || []).map((p, i) =>
    `<tr><td class="mark">${{marks[i] || (i+1)}}</td>` +
    `<td class="rank">${{p.horse_no}}</td><td>${{p.horse_name}}</td>` +
    `<td class="rank">${{(p.score*100).toFixed(0)}}%</td>` +
    `<td>${{p.reason || ''}}</td></tr>`).join('');
  return '<div class="card"><b>🤖 生成AI予想</b>' +
    `<div class="note">学習: ${{d.trained_on.join(', ')}}</div>` +
    '<p><b>傾向:</b> ' + (d.insights.summary || '') + '</p>' +
    '<p><b>導いた示唆:</b></p><ul>' + ins + '</ul>' +
    '<p><b>本命:</b> 馬番' + d.prediction.honmei_horse_no + '</p>' +
    '<table><thead><tr><th>印</th><th>馬番</th><th>馬名</th><th>期待度</th><th>理由</th></tr></thead>' +
    '<tbody>' + rows + '</tbody></table>' +
    '<p class="note">' + (d.prediction.commentary || '') + '</p></div>';
}}
</script>
</body></html>"""
    return Response(html, mimetype="text/html")


@app.get("/races")
def races():
    return jsonify(races=list_races(_store()))


@app.route("/fetch", methods=["GET", "POST"])
def fetch():
    if not _fetch_enabled():
        return _fetch_disabled_response()
    if not _check_token():
        return jsonify(error="unauthorized"), 401

    payload = request.get_json(silent=True) or {}
    race_id = (request.args.get("race_id") or payload.get("race_id") or "").strip()
    if not race_id:
        return jsonify(error="race_id is required"), 400

    max_history = request.args.get("max_history") or payload.get("max_history")
    max_history = int(max_history) if max_history else _env_int("KEIBA_MAX_HISTORY")
    wait = float(os.environ.get("KEIBA_FETCH_WAIT", "1.5"))
    proxy = os.environ.get("KEIBA_PROXY")

    try:
        summary = fetch_and_store(race_id, _store(), wait=wait,
                                  max_history_per_horse=max_history, proxy=proxy)
    except AccessBlockedError as e:
        # IP ブロックの可能性 → 502 + 対処法を明示
        return jsonify(error=str(e), race_id=race_id, blocked=True,
                       hint="KEIBA_PROXY に外向きプロキシを設定してください。"
                            "/diag で到達性を確認できます。"), 502
    except Exception as e:  # その他の取得・パース失敗
        return jsonify(error=str(e), race_id=race_id), 500
    return jsonify(summary)


@app.get("/races/<race_id>")
def race_meta(race_id: str):
    meta = load_meta(race_id, _store())
    if meta is None:
        return jsonify(error="not found", race_id=race_id), 404
    return jsonify(meta)


@app.get("/races/<race_id>/<name>.csv")
def race_csv(race_id: str, name: str):
    try:
        text = read_csv_text(race_id, name, _store())
    except ValueError as e:
        return jsonify(error=str(e)), 400
    if text is None:
        return jsonify(error="not found", race_id=race_id, csv=name), 404
    return Response(text, mimetype="text/csv; charset=utf-8")


def _train_ids_from_request(default_exclude):
    """?train=a,b,c があればそれを、無ければ既知ダービー全年(対象除外)を返す。"""
    from ..service import default_derby_train_ids
    raw = request.args.get("train")
    if raw:
        return [r.strip() for r in raw.split(",") if r.strip()]
    return default_derby_train_ids(exclude=default_exclude)


@app.get("/predict/<race_id>")
def predict(race_id: str):
    """⑤ML予想: 過去ダービーで学習し、対象レースの複勝確率ランキングを JSON で返す。"""
    from .. import ml
    from ..service import load_context, load_derby_items

    store = _store()
    train_items, skipped = load_derby_items(store, _train_ids_from_request(race_id))
    if not train_items:
        return jsonify(error="学習データがありません（fetch --past で過去ダービーを保存）",
                       skipped=skipped), 422
    try:
        ctx = load_context(race_id, store)
    except Exception as e:
        return jsonify(error=str(e), race_id=race_id), 404

    label = request.args.get("label", "show")
    X, y, _ = ml.build_target_training_data(train_items, label=label)
    model = ml.ShowProbModel(label=label).fit(X, y)
    pred = ml.predict_context(model, ctx)
    return jsonify(
        race_id=race_id,
        race_name=ctx.race_name,
        label=label,
        trained_on=[c.race_id for c, _ in train_items],
        ranking=pred.to_dict(orient="records"),
    )


@app.get("/genai-predict/<race_id>")
def genai_predict(race_id: str):
    """④⑤生成AI予想: 過去ダービーから示唆を導出し、対象レースを予想して JSON で返す。

    要 ANTHROPIC_API_KEY（Cloud Run では Secret 注入済み）。
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify(error="ANTHROPIC_API_KEY 未設定。生成AI予想は無効です。"), 503

    from .. import genai
    from ..service import load_context, load_derby_items

    store = _store()
    past_items, skipped = load_derby_items(store, _train_ids_from_request(race_id))
    if not past_items:
        return jsonify(error="過去ダービーがありません（fetch --past で保存）",
                       skipped=skipped), 422
    try:
        ctx = load_context(race_id, store)
    except Exception as e:
        return jsonify(error=str(e), race_id=race_id), 404

    import anthropic
    client = anthropic.Anthropic()
    model = request.args.get("model", genai.MODEL)
    try:
        insights = genai.derive_insights(client, past_items, model=model)
        pred = genai.apply_insights(client, insights, ctx, model=model)
    except Exception as e:  # API エラー等
        return jsonify(error=f"生成AI 呼び出し失敗: {e}", race_id=race_id), 502

    return jsonify(
        race_id=race_id,
        race_name=ctx.race_name,
        trained_on=[c.race_id for c, _ in past_items],
        insights=insights.model_dump(),
        prediction=pred.model_dump(),
    )


def _env_int(key: str):
    val = os.environ.get(key)
    return int(val) if val else None


if __name__ == "__main__":
    # ローカル開発用。本番は gunicorn 経由（Dockerfile 参照）。
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), debug=True)
