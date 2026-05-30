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
    from ..service import load_actual, load_meta
    store = _store()
    races = list_races(store)
    genai_on = bool(os.environ.get("ANTHROPIC_API_KEY"))

    # 予想対象（actual.csv が無い＝未施行）と、学習用の過去データ（actual.csv あり）に分ける。
    # 過去側はダービー(優駿/ダービー)のみ採用。2016-2018 は別レース(薫風S)なので名前で除外される。
    upcoming, past = [], []
    for r in races:
        meta = load_meta(r, store) or {}
        name = (meta.get("race_meta") or {}).get("race_name") or r
        date = (meta.get("race_meta") or {}).get("date") or ""
        info = {"id": r, "name": name, "date": date}
        if load_actual(r, store) is not None:
            if "優駿" in name or "ダービー" in name:  # 別レースは過去一覧に出さない
                past.append(info)
        else:
            upcoming.append(info)

    def _genai_btn(rid):
        if genai_on:
            return f'<button onclick="predict(\'{rid}\',\'genai-predict\',this)">🤖 生成AI予想</button>'
        return '<button disabled title="ANTHROPIC_API_KEY 未設定">🤖 生成AI予想(無効)</button>'

    # 主役: 予想対象レース（馬柱を主体に、予想ボタンで複勝率列＋コメントを追記）
    if upcoming:
        cards = []
        for u in upcoming:
            rid = u["id"]
            genai_link = (f'<a class="btn" href="/genai/{rid}">🤖 生成AI予想ページ</a>'
                          if genai_on else
                          '<span class="btn disabled" title="ANTHROPIC_API_KEY 未設定">🤖 生成AI予想(無効)</span>')
            cards.append(
                f'<div class="target" data-race="{rid}">'
                f'<h3>🎯 {u["name"]} <span class="note">{u["date"]}</span></h3>'
                f'<div class="actions">'
                f'<button onclick="predict(\'{rid}\',\'predict\',this)">📊 ML予想</button> '
                f'<label class="opt"><input type="checkbox" id="h2h-{rid}"> '
                f'直接対決を反映</label> '
                f'{genai_link} '
                f'<a class="btn secondary" href="/visualize/{rid}">📈 データ可視化ページ</a>'
                f'</div>'
                f'<div class="comment" id="cm-{rid}"></div>'
                f'<div class="umabashira" id="uma-{rid}">'
                f'<span class="note">馬柱を読み込み中…</span></div>'
                f'</div>'
            )
        target_section = "".join(cards)
    else:
        target_section = ('<p><em>予想対象レース（未施行）がまだありません。'
                          '今年のレースを <code>fetch --race-id ...</code>（--past 無し）で取得してください。</em></p>')

    # 脇役: 過去レース = 示唆を得る道具。「実際の結果」と「見解(示唆)」だけを見せる。
    if past:
        # 各年の実結果（上位3頭）をインラインで
        result_rows = []
        for p in sorted(past, key=lambda x: x["date"]):
            actual = load_actual(p["id"], store)
            top = ""
            if actual is not None and "finish_pos" in actual:
                a = actual.sort_values("finish_pos").head(3)
                marks = ["1着", "2着", "3着"]
                top = "　".join(
                    f'{m} {row.get("horse_name", row.get("horse_id",""))}'
                    for m, (_, row) in zip(marks, a.iterrows()))
            result_rows.append(
                f'<tr><td>{p["name"]}<br><span class="note">{p["date"]}</span></td>'
                f'<td>{top}</td></tr>')
        insight_btn = (
            '<button onclick="runInsights(this)">🤖 過去全体から示唆（見解）を出す</button>'
            if genai_on else
            '<button disabled title="ANTHROPIC_API_KEY 未設定">🤖 示唆を出す(無効)</button>'
        )
        past_section = (
            f'<h2>📚 過去ダービーの結果と見解</h2>'
            f'<p class="note">これらは予想の根拠（示唆）を得るための材料です。</p>'
            f'<table><thead><tr><th>レース</th><th>実際の結果（上位3頭）</th></tr></thead>'
            f'<tbody>{"".join(result_rows)}</tbody></table>'
            f'<p>{insight_btn}</p><div class="result" id="res-insights"></div>'
        )
    else:
        past_section = ('<p class="note">⚠ 学習用の過去ダービーがありません。'
                        '<code>fetch --derby-years 2019-2024 --past</code> で取得すると予想精度が上がります。</p>')

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
  .charts {{ margin:8px 0; }}
  .charts .card > div {{ margin:10px 0; }}
  .actions {{ margin:8px 0; display:flex; flex-wrap:wrap; gap:8px; align-items:center; }}
  .btn {{ display:inline-block; padding:6px 12px; border-radius:4px; text-decoration:none;
         border:1px solid #3b7dd8; background:#3b7dd8; color:#fff; font-size:0.9rem; }}
  .btn.secondary {{ background:#fff; color:#3b7dd8; }}
  .btn.disabled {{ background:#aaa; border-color:#aaa; cursor:not-allowed; }}
  .opt {{ font-size:0.9rem; color:#333; }}
  .umabashira {{ overflow-x:auto; margin-top:8px; }}
  table.uma {{ font-size:0.88rem; white-space:nowrap; }}
  table.uma th, table.uma td {{ padding:5px 8px; }}
  table.uma tr.hi {{ background:#fff5e6; }}
  .pcol {{ background:#eef4ff; }}
  .comment {{ margin:6px 0; }}
  .target {{ background:#fff; border:2px solid #3b7dd8; border-radius:8px;
            padding:14px 18px; margin:12px 0; max-width:900px; }}
  .target h3 {{ margin:0 0 6px; }}
  details {{ margin:16px 0; max-width:900px; }}
  summary {{ cursor:pointer; font-weight:bold; }}
</style></head><body>
<h1>keiba — 競馬予想サービス</h1>
<p class="note">参照先: <code>{store.uri()}</code>　生成AI予想: <b>{'有効' if genai_on else '無効(キー未設定)'}</b></p>

<h2>🏇 予想する</h2>
<p>「過去ダービーの傾向 → 共通するポイント → だから今年はこう」という流れで予想します。</p>
{target_section}

{past_section}

<script>
const MARKS = ['◎','○','▲','△','△'];
const fmtPct = v => (v == null ? '-' : (v <= 1 ? (v*100).toFixed(0) : v.toFixed(0)) + '%');

// ページ読み込み時に各レースの馬柱を取得して表示
document.addEventListener('DOMContentLoaded', () => {{
  document.querySelectorAll('.target[data-race]').forEach(el =>
    loadUmabashira(el.dataset.race));
}});

async function loadUmabashira(raceId) {{
  const box = document.getElementById('uma-' + raceId);
  try {{
    const r = await fetch('/entrants/' + raceId);
    const d = await r.json();
    if (!r.ok) {{ box.innerHTML = '<span class="note">馬柱を取得できません: ' + (d.error||'') + '</span>'; return; }}
    box.dataset.rows = JSON.stringify(d.rows);
    box.innerHTML = renderUma(d.rows, null);
  }} catch (e) {{ box.innerHTML = '<span class="note">通信エラー: ' + e + '</span>'; }}
}}

// 馬柱を描画。pred があれば「印」「予想複勝率」列を追加してハイライト
function renderUma(rows, pred) {{
  // pred: {{ byNo: {{horse_no: {{rank, prob, mark}}}} }}
  const head =
    '<tr><th>枠</th><th>馬番</th><th>馬名</th><th>性齢</th><th>騎手</th>' +
    '<th>勝率</th><th>複勝率(実績)</th><th>近3走</th><th>脚質</th><th>賞金(万)</th>' +
    (pred ? '<th class="pcol">印</th><th class="pcol">予想複勝率</th>' : '') + '</tr>';
  const body = rows.map(x => {{
    const p = pred && pred.byNo[x.horse_no];
    const cls = p && p.rank <= 3 ? ' class="hi"' : '';
    return `<tr${{cls}}>` +
      `<td>${{x.frame_no ?? '-'}}</td><td class="rank">${{x.horse_no ?? '-'}}</td>` +
      `<td>${{x.horse_name ?? ''}}</td>` +
      `<td>${{(x.sex||'')}}${{x.age ?? ''}}</td><td>${{x.jockey || ''}}</td>` +
      `<td class="rank">${{fmtPct(x.pit_win_rate)}}</td>` +
      `<td class="rank">${{fmtPct(x.pit_show_rate)}}</td>` +
      `<td class="rank">${{x.pit_avg_finish_last3 != null ? x.pit_avg_finish_last3.toFixed(1) : '-'}}</td>` +
      `<td>${{x.pit_running_style || ''}}</td>` +
      `<td class="rank">${{x.pit_total_prize != null ? Math.round(x.pit_total_prize) : '-'}}</td>` +
      (pred ? `<td class="pcol mark">${{p ? p.mark : ''}}</td>` +
              `<td class="pcol rank">${{p ? fmtPct(p.prob) : '-'}}</td>` : '') +
      '</tr>';
  }}).join('');
  return '<table class="uma"><thead>' + head + '</thead><tbody>' + body + '</tbody></table>';
}}

// 予想を実行し、コメントを上に、馬柱に印＋予想複勝率を追記
async function predict(raceId, kind, btn) {{
  const cm = document.getElementById('cm-' + raceId);
  const uma = document.getElementById('uma-' + raceId);
  cm.innerHTML = '<div class="card spinner">⏳ 予想中…</div>';
  btn.disabled = true;
  const h2hEl = document.getElementById('h2h-' + raceId);
  const q = (h2hEl && h2hEl.checked) ? '?h2h=0.3' : '';
  try {{
    const r = await fetch('/' + kind + '/' + raceId + q);
    const d = await r.json();
    if (!r.ok) {{ cm.innerHTML = '<div class="card">⚠ ' + (d.error || r.status) + '</div>'; return; }}
    const pred = (kind === 'predict') ? mlToPred(d) : genaiToPred(d);
    cm.innerHTML = pred.comment;
    const rows = JSON.parse(uma.dataset.rows || '[]');
    uma.innerHTML = renderUma(rows, pred);
  }} catch (e) {{
    cm.innerHTML = '<div class="card">⚠ 通信エラー: ' + e + '</div>';
  }} finally {{ btn.disabled = false; }}
}}

// ML予想 → 印/複勝率マップ + コメント
function mlToPred(d) {{
  const byNo = {{}};
  d.ranking.forEach((r, i) => byNo[r.horse_no] = {{
    rank: r.pred_rank ?? (i+1), prob: r.show_prob, mark: MARKS[i] || ''
  }});
  const top = d.ranking.slice(0,3).map((r,i) =>
    `${{MARKS[i]}} ${{r.horse_name}}（${{fmtPct(r.show_prob)}}）`).join('　');
  const h2hNote = (d.h2h_weight > 0)
    ? `<div class="note">出走馬どうしの直接対決を ${{Math.round(d.h2h_weight*100)}}% 反映しています。</div>` : '';
  return {{ byNo, comment:
    '<div class="card"><b>📊 ML予想（過去ダービーで学習）</b>' +
    `<div class="note">学習: ${{d.trained_on.join(', ')}}</div>` + h2hNote +
    '<p>' + top + '</p></div>' }};
}}

// 生成AI予想 → 印/期待度マップ + コメント（①傾向→②共通点→③本命）
function genaiToPred(d) {{
  const byNo = {{}};
  (d.prediction.ranking || []).forEach((p, i) => byNo[p.horse_no] = {{
    rank: i+1, prob: p.score, mark: MARKS[i] || ''
  }});
  const ins = (d.insights.insights || []).map(x =>
    `<li>${{x.pattern}}<br><span class="note">根拠: ${{x.rationale||''}}</span></li>`).join('');
  const reasons = (d.prediction.ranking || []).slice(0,3).map((p,i) =>
    `<li>${{MARKS[i]}} ${{p.horse_name}}: ${{p.reason||''}}</li>`).join('');
  return {{ byNo, comment:
    '<div class="card"><b>🤖 生成AI予想</b>' +
    `<div class="note">参考にした過去ダービー: ${{d.trained_on.join(', ')}}</div>` +
    '<p><b>① 過去の傾向:</b> ' + (d.insights.summary || '') + '</p>' +
    '<p><b>② 共通するポイント:</b></p><ul>' + ins + '</ul>' +
    '<p><b>③ 予想（本命 馬番' + d.prediction.honmei_horse_no + '）:</b></p><ul>' + reasons + '</ul>' +
    '<p class="note">' + (d.prediction.commentary || '') + '</p></div>' }};
}}

async function runInsights(btn) {{
  const box = document.getElementById('res-insights');
  box.innerHTML = '<span class="spinner">⏳ 過去全体から示唆を導出中…（数十秒かかります）</span>';
  btn.disabled = true;
  try {{
    const resp = await fetch('/insights');
    const data = await resp.json();
    if (!resp.ok) {{ box.innerHTML = '<div class="card">⚠ ' + (data.error || resp.status) + '</div>'; return; }}
    box.innerHTML = renderInsights(data);
  }} catch (e) {{
    box.innerHTML = '<div class="card">⚠ 通信エラー: ' + e + '</div>';
  }} finally {{ btn.disabled = false; }}
}}

function renderInsights(d) {{
  const ins = (d.insights.insights || []).map(x =>
    `<li>[重要度 ${{x.weight}}] <b>${{x.pattern}}</b><br>` +
    `<span class="note">根拠: ${{x.rationale || ''}}</span></li>`).join('');
  const cav = (d.insights.caveats || []).map(c => `<li>${{c}}</li>`).join('');
  return '<div class="card"><b>🤖 過去ダービーから得た見解（示唆）</b>' +
    `<div class="note">参考: ${{(d.based_on||[]).join(', ')}}</div>` +
    '<p><b>全体傾向:</b> ' + (d.insights.summary || '') + '</p>' +
    '<p><b>複数年に共通するポイント:</b></p><ul>' + ins + '</ul>' +
    (cav ? '<p class="note"><b>注意:</b></p><ul class="note">' + cav + '</ul>' : '') +
    '</div>';
}}

</script>
</body></html>"""
    return Response(html, mimetype="text/html")


def _page_head(title):
    return ('<!doctype html><html lang="ja"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{title}</title><style>'
            'body{font-family:system-ui,sans-serif;margin:2rem;line-height:1.6;color:#222;}'
            'h1{font-size:1.4rem;} a{color:#3b7dd8;} .note{color:#666;font-size:0.9rem;}'
            'img{max-width:100%;border:1px solid #eee;border-radius:6px;margin:6px 0;}'
            '.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:16px;}'
            '.card{background:#f8f9fb;border:1px solid #dde;border-radius:8px;padding:12px;}'
            'button{font-size:0.95rem;padding:8px 16px;cursor:pointer;border:1px solid #3b7dd8;'
            'background:#3b7dd8;color:#fff;border-radius:5px;} .back{display:inline-block;margin-bottom:1rem;}'
            '</style></head><body>')


@app.get("/chart-result/<race_id>.png")
def chart_result(race_id: str):
    """過去レースを指標でプロットし、3着内を色分けした PNG を返す（傾向が見える）。

    ?indicator=pit_total_prize|pit_show_rate|pit_best_last3f|pit_avg_corner_pos|pit_max_grade_win
    """
    from .. import viz
    from ..service import load_actual, load_context
    actual = load_actual(race_id, _store())
    if actual is None:
        return jsonify(error="この race_id に実結果がありません", race_id=race_id), 404
    try:
        ctx = load_context(race_id, _store())
    except Exception as e:
        return jsonify(error=str(e), race_id=race_id), 404
    indicator = request.args.get("indicator", "pit_total_prize")
    if indicator not in viz.PAST_INDICATORS:
        indicator = "pit_total_prize"
    return Response(viz.render_past_result(ctx, actual, indicator=indicator),
                   mimetype="image/png")


@app.get("/visualize/<race_id>")
def visualize(race_id: str):
    """③可視化ページ: 対象レースの各種グラフ + 過去ダービーの結果(3着内を色分け)。"""
    from .. import viz
    from ..service import load_meta, load_actual, list_races, default_derby_train_ids
    store = _store()
    meta = load_meta(race_id, store)
    if meta is None:
        return Response(_page_head("可視化") +
                        f'<a class="back" href="/">← 戻る</a><p>race_id={race_id} のデータがありません。</p>'
                        '</body></html>', mimetype="text/html")
    name = (meta.get("race_meta") or {}).get("race_name") or race_id

    # 対象レースの各種グラフ
    imgs = "".join(
        f'<div class="card"><b>{viz.CHART_LABELS.get(k, k)}</b><br>'
        f'<img loading="lazy" src="/chart/{race_id}/{k}.png" alt="{k}"></div>'
        for k in viz.CHART_KINDS)

    # 過去ダービー（actual.csv あり）を「指標 × 好走(3着内)」でプロット。
    # 指標をラジオで切替え、橙＝複勝圏。どんな指標の馬が来たかの傾向が見える。
    past_ids = [r for r in default_derby_train_ids() if load_actual(r, store) is not None]
    if past_ids:
        ind_labels = {"pit_total_prize": "賞金", "pit_show_rate": "複勝率",
                      "pit_best_last3f": "上がり3F", "pit_avg_corner_pos": "脚質(通過順)",
                      "pit_max_grade_win": "最高勝鞍格"}
        radios = "".join(
            f'<label><input type="radio" name="ind" value="{k}"'
            f'{" checked" if k == "pit_total_prize" else ""} '
            f'onchange="switchInd(this.value)"> {lbl}</label> '
            for k, lbl in ind_labels.items())
        past_imgs = "".join(
            f'<div class="card"><b>{(load_meta(r, store).get("race_meta") or {{}}).get("race_name", r)}</b><br>'
            f'<img class="pastimg" data-race="{r}" loading="lazy" '
            f'src="/chart-result/{r}.png?indicator=pit_total_prize" alt="{r}"></div>'
            for r in sorted(past_ids))
        past_block = (
            f'<h2>📚 過去ダービー：好走馬の傾向（橙＝複勝圏 3着以内）</h2>'
            f'<p class="note">指標を選ぶと、その指標で各馬をプロットし、'
            f'実際に3着以内に来た馬を橙で示します。好走馬がどのあたりに固まるかで傾向が分かります。</p>'
            f'<p>指標: {radios}</p>'
            f'<div class="grid">{past_imgs}</div>'
        )
    else:
        past_block = ('<p class="note">過去ダービーの結果データがありません'
                      '（fetch --past で取得すると表示されます）。</p>')

    script = ('<script>function switchInd(v){'
              'document.querySelectorAll("img.pastimg").forEach(function(im){'
              'im.src="/chart-result/"+im.dataset.race+".png?indicator="+v;});}</script>')
    html = (_page_head(f"可視化 - {name}") +
            f'<a class="back" href="/">← 予想ページに戻る</a>'
            f'<h1>📈 {name} のデータ可視化</h1>'
            f'<p class="note">いろいろな尺度で出走馬を比較します。</p>'
            f'<div class="grid">{imgs}</div>'
            f'{past_block}{script}</body></html>')
    return Response(html, mimetype="text/html")


@app.get("/genai/<race_id>")
def genai_page(race_id: str):
    """④⑤生成AI予想ページ: ボタンで示唆→予想を実行して表示（馬柱とは別ページ）。"""
    from ..service import load_meta
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return Response(_page_head("生成AI予想") +
                        '<a class="back" href="/">← 戻る</a>'
                        '<p>ANTHROPIC_API_KEY が未設定のため生成AI予想は無効です。</p>'
                        '</body></html>', mimetype="text/html")
    meta = load_meta(race_id, _store())
    name = ((meta or {}).get("race_meta") or {}).get("race_name") or race_id
    html = (_page_head(f"生成AI予想 - {name}") +
            f'<a class="back" href="/">← 予想ページに戻る</a>'
            f'<h1>🤖 {name} の生成AI予想</h1>'
            f'<p>過去ダービーから「①傾向 → ②共通するポイント → ③今年の予想」を導きます。'
            f'（数十秒かかります）</p>'
            f'<button id="go" onclick="runGenai()">🤖 生成AI予想を実行</button>'
            f'<div id="out" style="margin-top:1rem"></div>'
            f"""<script>
async function runGenai() {{
  const out = document.getElementById('out'), btn = document.getElementById('go');
  out.innerHTML = '<p>⏳ 生成AIが過去から示唆を導出して予想中…（数十秒）</p>';
  btn.disabled = true;
  try {{
    const r = await fetch('/genai-predict/{race_id}');
    const d = await r.json();
    if (!r.ok) {{ out.innerHTML = '<div class="card">⚠ ' + (d.error||r.status) + '</div>'; return; }}
    const ins = (d.insights.insights||[]).map(x =>
      `<li><b>${{x.pattern}}</b><br><span class="note">根拠: ${{x.rationale||''}}</span></li>`).join('');
    const M = ['◎','○','▲','△','△'];
    const rows = (d.prediction.ranking||[]).map((p,i) =>
      `<tr><td>${{M[i]||(i+1)}}</td><td>${{p.horse_no}}</td><td>${{p.horse_name}}</td>` +
      `<td>${{Math.round(p.score*100)}}%</td><td>${{p.reason||''}}</td></tr>`).join('');
    out.innerHTML =
      '<div class="card"><h3>① 過去の傾向</h3><p>' + (d.insights.summary||'') + '</p>' +
      '<h3>② 複数年に共通するポイント</h3><ul>' + ins + '</ul>' +
      '<h3>③ 今年の予想（本命 馬番' + d.prediction.honmei_horse_no + '）</h3>' +
      '<table border="1" cellpadding="5" style="border-collapse:collapse"><tr><th>印</th><th>馬番</th><th>馬名</th><th>期待度</th><th>理由</th></tr>' +
      rows + '</table><p class="note">' + (d.prediction.commentary||'') + '</p></div>';
  }} catch (e) {{ out.innerHTML = '<div class="card">⚠ ' + e + '</div>'; }}
  finally {{ btn.disabled = false; }}
}}
</script></body></html>""")
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


@app.get("/entrants/<race_id>")
def entrants(race_id: str):
    """馬柱データ（出馬表 + レース前分析指標）を JSON で返す。

    1 頭 = 1 行。枠番・馬番・馬名・騎手・斤量に、勝率/複勝率/近走/脚質/賞金などを添える。
    """
    import numpy as np
    import pandas as pd
    from ..analyze import analyze_entrants
    from ..service import load_context
    try:
        ctx = load_context(race_id, _store())
    except Exception as e:
        return jsonify(error=str(e), race_id=race_id), 404

    feat = analyze_entrants(ctx)  # horse_no, horse_name, pit_* 指標
    ent = ctx.entries.copy()
    keep = [c for c in ["horse_no", "frame_no", "horse_name", "sex", "age",
                        "impost", "jockey", "odds", "popularity"] if c in ent.columns]
    base = ent[keep]
    merged = base.merge(feat.drop(columns=["horse_name"], errors="ignore"),
                        on="horse_no", how="left")
    merged = merged.sort_values("horse_no")
    # NaN を None に（JSON で null）
    rows = merged.replace({np.nan: None}).to_dict(orient="records")
    return jsonify(race_id=race_id, race_name=ctx.race_name,
                   date=str(ctx.race.get("date", "")), rows=rows)


@app.get("/chart/<race_id>/<kind>.png")
def chart(race_id: str, kind: str):
    """③可視化: 出走馬分析のグラフを PNG 画像で返す。

    kind = show_rate（複勝率）/ prize（賞金）/ last3f（上がり）/ style（脚質分布）。
    """
    from .. import viz
    from ..service import load_context
    if kind not in viz.CHART_KINDS:
        return jsonify(error=f"未知のチャート: {kind}",
                       available=viz.CHART_KINDS), 400
    try:
        ctx = load_context(race_id, _store())
    except Exception as e:
        return jsonify(error=str(e), race_id=race_id), 404
    png = viz.render_chart(ctx, kind=kind)
    return Response(png, mimetype="image/png")


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
    try:
        h2h_w = float(request.args.get("h2h", "0"))
    except ValueError:
        h2h_w = 0.0
    X, y, _ = ml.build_target_training_data(train_items, label=label)
    model = ml.ShowProbModel(label=label).fit(X, y)
    pred = ml.predict_context(model, ctx, h2h_weight=h2h_w)
    return jsonify(
        race_id=race_id,
        race_name=ctx.race_name,
        label=label,
        h2h_weight=h2h_w,
        trained_on=[c.race_id for c, _ in train_items],
        ranking=pred.to_dict(orient="records"),
    )


@app.get("/h2h/<race_id>")
def h2h(race_id: str):
    """出走馬どうしの直接対決（上下関係）を JSON で返す。"""
    from ..h2h import head_to_head
    from ..service import load_context
    try:
        ctx = load_context(race_id, _store())
    except Exception as e:
        return jsonify(error=str(e), race_id=race_id), 404
    h = head_to_head(ctx)
    order = [{"horse_id": i, "horse_no": _int(h["id2no"].get(i)),
              "horse_name": h["id2name"].get(i, i), **h["meta"][i]}
             for i in h["order"]]
    return jsonify(race_id=race_id, race_name=ctx.race_name,
                   order=order, records=h["records"])


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


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


@app.get("/result/<race_id>")
def result(race_id: str):
    """過去レースの実際の結果（着順）を JSON で返す。ユーザーが見たい『実結果』。"""
    from ..service import load_actual, load_meta
    store = _store()
    actual = load_actual(race_id, store)
    if actual is None:
        return jsonify(error="この race_id に実結果(actual.csv)がありません",
                       race_id=race_id), 404
    meta = load_meta(race_id, store) or {}
    rm = meta.get("race_meta") or {}
    df = actual.sort_values("finish_pos")
    cols = [c for c in ["finish_pos", "horse_no", "horse_name"] if c in df.columns]
    return jsonify(
        race_id=race_id,
        race_name=rm.get("race_name", race_id),
        date=rm.get("date", ""),
        result=df[cols].to_dict(orient="records"),
    )


@app.get("/insights")
def insights():
    """過去ダービー群から『示唆(見解)』だけを導出して返す（予想は付けない）。

    2016〜2024 は示唆を得る道具なので、その見解だけを見せるためのエンドポイント。
    要 ANTHROPIC_API_KEY。?train=id,id で対象を絞れる（既定は既知ダービー全年）。
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify(error="ANTHROPIC_API_KEY 未設定。示唆出しは無効です。"), 503

    from .. import genai
    from ..service import load_derby_items, default_derby_train_ids

    store = _store()
    raw = request.args.get("train")
    ids = ([r.strip() for r in raw.split(",") if r.strip()] if raw
           else default_derby_train_ids())
    past_items, skipped = load_derby_items(store, ids)
    if not past_items:
        return jsonify(error="過去ダービーがありません（fetch --past で保存）",
                       skipped=skipped), 422

    import anthropic
    client = anthropic.Anthropic()
    model = request.args.get("model", genai.MODEL)
    try:
        ins = genai.derive_insights(client, past_items, model=model)
    except Exception as e:
        return jsonify(error=f"生成AI 呼び出し失敗: {e}"), 502
    return jsonify(
        based_on=[c.race_id for c, _ in past_items],
        insights=ins.model_dump(),
    )


def _env_int(key: str):
    val = os.environ.get(key)
    return int(val) if val else None


if __name__ == "__main__":
    # ローカル開発用。本番は gunicorn 経由（Dockerfile 参照）。
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), debug=True)
