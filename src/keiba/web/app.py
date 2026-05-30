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

エンドポイント（参照専用 + 任意の取得系）:
    GET  /healthz                     ヘルスチェック
    GET  /                            使い方 + 保存済みレース一覧（HTML）
    GET  /races                       保存済みレースID一覧（JSON）
    GET  /races/<race_id>             メタ + 取得サマリ（JSON）
    GET  /races/<race_id>/<name>.csv  entries/results/races/horses の CSV
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
    items = "".join(
        f'<li><a href="/races/{r}">{r}</a> '
        f'(<a href="/races/{r}/entries.csv">entries.csv</a>)</li>'
        for r in races
    ) or "<li><em>まだ取得済みレースはありません</em></li>"
    if _fetch_enabled():
        fetch_section = (
            '<h2>レース取得（開発時のみ有効）</h2>'
            '<form action="/fetch" method="get">'
            'race_id: <input name="race_id" placeholder="202605021211" size="16">'
            '<input type="submit" value="取得"></form>'
        )
    else:
        fetch_section = (
            '<h2>データ取得について</h2>'
            '<p>このデプロイは<strong>参照・予想専用</strong>です。データ取得は'
            '手元の回線で次を実行し、GCS に保存してください:</p>'
            '<pre>python3 -m keiba.cli fetch --race-id 202605021211 '
            '--out gs://&lt;bucket&gt;/keiba</pre>'
        )
    html = f"""<!doctype html><meta charset="utf-8">
<title>keiba データ参照サービス</title>
<h1>keiba — 競馬データ参照・予想サービス</h1>
<p>参照先: <code>{store.uri()}</code></p>
{fetch_section}
<h2>取得済みレース</h2>
<ul>{items}</ul>
"""
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


def _env_int(key: str):
    val = os.environ.get(key)
    return int(val) if val else None


if __name__ == "__main__":
    # ローカル開発用。本番は gunicorn 経由（Dockerfile 参照）。
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), debug=True)
