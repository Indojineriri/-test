# keiba を Cloud Run で運用する

競馬データ収集サービス（keiba）を Google Cloud Run 上で動かすための構成。
既存の PPT 生成アプリ（meeting-support）とは **別イメージ・別サービス**で、
GCP プロジェクト / Artifact Registry / Anthropic Secret のみ共有する。

## 構成

```
HTTP リクエスト
   │  GET /fetch?race_id=202605021211
   ▼
Cloud Run サービス "keiba"  ── Flask + gunicorn（keiba.web.app:app）
   │  ① 出馬表 → ② 各馬キャリア → 各レース結果 をスクレイピング
   ▼
GCS バケット  gs://<project>-keiba/keiba/
   ├ cache/                       … 取得した生 HTML（再取得を避ける永続キャッシュ）
   └ races/<race_id>/             … パース済み CSV + meta.json
        entries.csv / results.csv / races.csv / horses.csv / meta.json
```

Cloud Run のディスクは揮発性なので、**HTML キャッシュも CSV 出力も GCS に永続化**する
（`KEIBA_STORAGE_URI=gs://...`）。ローカル開発では `KEIBA_STORAGE_URI` を省略すると
`/tmp/keiba` に保存され、GCS 無しで動く。

## ファイル

| ファイル | 役割 |
|---|---|
| `deploy/keiba/Dockerfile` | サービスのイメージ（root コンテキストでビルド、`src/` を含む） |
| `deploy/keiba/cloudbuild.yaml` | Cloud Build でのビルド & push 定義 |
| `deploy/keiba/deploy.sh` | API有効化→AR/GCS作成→ビルド→IAM付与→Cloud Run デプロイ |
| `src/keiba/web/app.py` | Flask アプリ（WSGI: `keiba.web.app:app`） |
| `src/keiba/service.py` | 取得→保存→参照の中核（Flask 非依存） |
| `src/keiba/storage.py` | ローカル / GCS を切り替える Storage 抽象 |

## デプロイ

ローカル（`gcloud` 認証済み環境）から:

```bash
gcloud auth login
./deploy/keiba/deploy.sh
```

主な上書き変数（既定値）:

```bash
PROJECT_ID=clean-pen-422206-d7   # GCP プロジェクト（既存アプリと共有）
REGION=asia-northeast1           # リージョン
SERVICE=keiba                    # Cloud Run サービス名
BUCKET=${PROJECT_ID}-keiba       # データ保存バケット（無ければ自動作成）
# 例: ステージングに別名でデプロイ
SERVICE=keiba-staging ./deploy/keiba/deploy.sh
```

`deploy.sh` がやること:
1. 必要な API を有効化（run / artifactregistry / secretmanager / cloudbuild / storage）
2. Artifact Registry リポジトリ `app-images` を作成（無ければ）
3. GCS バケットを作成（無ければ）
4. `cloudbuild.yaml` でイメージをビルド & push
5. ランタイム SA にバケットの `objectAdmin` を付与
6. Secret 注入（あれば）: `anthropic-api-key`→`ANTHROPIC_API_KEY`（生成AI 用）、
   `keiba-proxy`→`KEIBA_PROXY`（netkeiba の IP ブロック回避用プロキシ）
7. Cloud Run へデプロイし、URL を表示

## 動作確認

```bash
URL="$(gcloud run services describe keiba --region asia-northeast1 \
       --format 'value(status.url)')"

curl "$URL/healthz"                          # {"status":"ok"}（外部アクセスしない）
curl "$URL/diag"                             # ★まず到達性を確認（403 なら下記の対策へ）
curl "$URL/fetch?race_id=202605021211"       # 取得して GCS に保存（2026ダービー）
curl "$URL/races"                            # 取得済みレース一覧
curl "$URL/races/202605021211"               # メタ + 取得サマリ
curl "$URL/races/202605021211/entries.csv"   # 出馬表 CSV
```

> **デプロイしたらまず `/diag`。** netkeiba に到達できるか（403 で弾かれていないか）を
> 確認してから `/fetch` を回してください。403 の場合は下記「IP ブロックと対策」へ。

## エンドポイント

| メソッド | パス | 説明 |
|---|---|---|
| GET | `/healthz` | ヘルスチェック |
| GET | `/` | 使い方 + 取得済み一覧（HTML） |
| GET/POST | `/fetch?race_id=` | 取得して GCS に保存（JSON） |
| GET | `/races` | 取得済みレースID一覧（JSON） |
| GET | `/races/<race_id>` | メタ + 取得サマリ（JSON） |
| GET | `/races/<race_id>/<name>.csv` | entries/results/races/horses の CSV |

## 環境変数

| 変数 | 既定 | 説明 |
|---|---|---|
| `KEIBA_STORAGE_URI` | `/tmp/keiba` | 保存先。Cloud Run では `gs://<bucket>/keiba` |
| `KEIBA_FETCH_WAIT` | `1.5` | リクエスト間ウェイト秒（netkeiba への配慮） |
| `KEIBA_MAX_HISTORY` | （無制限） | 1頭あたり遡る過去レース数の上限 |
| `KEIBA_FETCH_TOKEN` | （無し） | 設定すると `/fetch` `/diag` に `?token=` / `X-Auth-Token` を要求 |
| `KEIBA_PROXY` | （無し） | 外向きプロキシ URL。**GCP IP ブロックの回避に使う**（後述） |

## ⚠️ Cloud Run から実スクレイピングする際の最重要ポイント：IP ブロック

**netkeiba は anti-bot を入れており、GCP（Cloud Run）のデータセンタ IP からの
アクセスは `HTTP 403` で弾かれることがあります。** 実際、この開発環境（同じく
データセンタ IP）から netkeiba にアクセスすると 403 が返ります。Cloud Run の
デフォルト egress も GCP の共有 IP なので、**そのままでは 403 になる可能性が高い**。

「コードを書けば動く」問題ではなく、**出口 IP の問題**である点に注意してください。

### まず到達性を診断する（`/diag`）

デプロイ後、スクレイピング本体を走らせる前に到達性を確認できます:

```bash
curl "$URL/diag"
# 到達OK   → {"ok": true, "status": 200, "via_proxy": false}
# ブロック → {"ok": false, "status": 403, "blocked": true, ...}  ← 対策が必要
```

`/fetch` も 403 を検知すると **502 + 対処ヒント**を返します（無駄なリトライはしません）。

### 対策：外向きプロキシを経由する（`KEIBA_PROXY`）

403 になる場合は、ブロックされていない IP を持つプロキシ経由で egress します。
`KEIBA_PROXY` にプロキシ URL を設定すると、全リクエストがそこを通ります。

```bash
# プロキシを Secret に入れて注入（推奨。認証情報を環境変数に直書きしない）
echo -n "http://user:pass@proxy.example.com:8080" | \
  gcloud secrets create keiba-proxy --data-file=-
gcloud secrets add-iam-policy-binding keiba-proxy \
  --member "serviceAccount:<runtime-sa>" --role roles/secretmanager.secretAccessor

gcloud run services update keiba --region asia-northeast1 \
  --set-secrets "KEIBA_PROXY=keiba-proxy:latest"

# 確認
curl "$URL/diag"   # {"ok": true, "via_proxy": true} になれば成功
```

プロキシの選択肢（ブロック回避の確度が高い順）:

| 方式 | 効果 | 備考 |
|---|---|---|
| 住宅用(residential)プロキシ | ◎ | 一般家庭 IP。anti-bot を最も通りやすい。有料サービス |
| データセンタプロキシ | △ | 別 DC の IP。ブロック対象に入っていれば不可 |
| 自前 VM/VPS をプロキシ化 | ○〜△ | その VM の IP 次第。安価だが IP 評価に依存 |

### （補足）Cloud NAT で固定 IP にする方法とその限界

「Cloud Run → VPC コネクタ → Cloud NAT で固定 IP」にすれば egress IP を固定
できますが、**その固定 IP も GCP のレンジなので netkeiba にブロックされ得ます**。
固定 IP 自体はブロック回避の保証にはならない点に注意（業務都合で egress IP を
固定したい場合の手段、と割り切る）。本ツールでは、確度の高い
**プロキシ経由（`KEIBA_PROXY`）を第一の対策**として推奨します。

### ローカル（手元の家庭/オフィス回線）なら通ることが多い

開発・検証段階では、`KEIBA_STORAGE_URI=gs://...` を指定しつつ **手元マシンから**
`fetch` を回して GCS に貯める、という運用も有効です（手元の回線 IP はブロック
されにくい）。Cloud Run はその後の参照 API / 定期ジョブとして使う、という
ハイブリッドも現実的です。

## セキュリティ上の注意

`deploy.sh` は既存アプリに合わせて `--allow-unauthenticated`（URL を知る誰でも
アクセス可）にしている。**スクレイピングが第三者に乱用されない**よう、社内利用なら:

- **(a) IAM で限定**: `--no-allow-unauthenticated` にし、
  `gcloud run services add-iam-policy-binding keiba --member=user:... --role=roles/run.invoker`
- **(b) トークン保護**: `KEIBA_FETCH_TOKEN` を Secret で設定し、`/fetch?token=...` を要求

## スクレイピングのマナー（再掲）

- netkeiba の robots.txt / 利用規約を必ず確認。私的・研究目的の範囲で。
- リクエスト間ウェイト（既定 1.5 秒）と HTML キャッシュで負荷を最小化している。
- 大量レースを一度に取得しない。`KEIBA_MAX_HISTORY` で遡り数を絞れる。

## 将来の拡張（Cloud Run Job / 定期取得）

ダービー以外のレースも継続収集する場合、同じイメージを **Cloud Run Job** として
登録し、Cloud Scheduler から「毎週末に対象レースを取得」と定期実行できる。
`service.fetch_and_store()` は Flask 非依存なので、Job 用の薄い main を足すだけで再利用可能。
