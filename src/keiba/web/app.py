"""③④⑤を見据えた keiba の HTTP サービス（Flask）。

Cloud Run 上で動く WSGI アプリ。エントリポイントは `keiba.web.app:app`。
現フェーズでは ①②データ取得を HTTP 越しに実行・参照できるようにする。

環境変数:
    KEIBA_STORAGE_URI   出力の保存先。Cloud Run では gs://<bucket>/keiba。
                        未設定ならローカル /tmp/keiba（開発用）。
    KEIBA_FETCH_WAIT    リクエスト間ウェイト秒（既定 1.5）
    KEIBA_MAX_HISTORY   1頭あたり遡る過去レース数の上限（既定 無制限）
    KEIBA_FETCH_TOKEN   設定すると /fetch に ?token= or X-Auth-Token を要求（簡易保護）
    KEIBA_PROXY         外向きプロキシ URL（GCP IP がブロックされる場合の回避策）

エンドポイント:
    GET  /healthz                     ヘルスチェック（外部アクセスしない）
    GET  /diag                        netkeiba への到達性チェック（IP ブロック診断）
    GET  /                            使い方 + 保存済みレース一覧（HTML）
    GET  /races                       保存済みレースID一覧（JSON）
    POST /fetch  {race_id,...}        取得して GCS に保存（JSON 返却）
    GET  /fetch?race_id=...           上に同じ（ブラウザ確認用）
    GET  /races/<race_id>             メタ + 取得サマリ（JSON）
    GET  /races/<race_id>/<name>.csv  entries/results/races/horses の CSV
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


@app.get("/healthz")
def healthz():
    return jsonify(status="ok")


@app.get("/diag")
def diag():
    """netkeiba に実際に到達できるか確認する。Cloud Run からの IP ブロック診断用。

    例: {"ok": true, "status": 200, "via_proxy": false}
        {"ok": false, "status": 403, "blocked": true, ...}  ← IP ブロックの疑い
    """
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
    items = "".join(
        f'<li><a href="/races/{r}">{r}</a> '
        f'(<a href="/races/{r}/entries.csv">entries.csv</a>)</li>'
        for r in races
    ) or "<li><em>まだ取得済みレースはありません</em></li>"
    html = f"""<!doctype html><meta charset="utf-8">
<title>keiba データ収集サービス</title>
<h1>keiba — 競馬データ収集サービス</h1>
<p>保存先: <code>{store.uri()}</code></p>
<h2>レース取得</h2>
<p>例（2026 日本ダービー）:
<code>GET /fetch?race_id=202605021211</code></p>
<form action="/fetch" method="get">
  race_id: <input name="race_id" placeholder="202605021211" size="16">
  <input type="submit" value="取得">
</form>
<h2>取得済みレース</h2>
<ul>{items}</ul>
"""
    return Response(html, mimetype="text/html")


@app.get("/races")
def races():
    return jsonify(races=list_races(_store()))


@app.route("/fetch", methods=["GET", "POST"])
def fetch():
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


def _env_int(key: str):
    val = os.environ.get(key)
    return int(val) if val else None


if __name__ == "__main__":
    # ローカル開発用。本番は gunicorn 経由（Dockerfile 参照）。
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), debug=True)
