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
6. 共有 Secret `anthropic-api-key` があれば `ANTHROPIC_API_KEY` として注入（生成AI フェーズ用）
7. Cloud Run へデプロイし、URL を表示

## 動作確認

```bash
URL="$(gcloud run services describe keiba --region asia-northeast1 \
       --format 'value(status.url)')"

curl "$URL/healthz"                          # {"status":"ok"}
curl "$URL/fetch?race_id=202605021211"       # 取得して GCS に保存（2026ダービー）
curl "$URL/races"                            # 取得済みレース一覧
curl "$URL/races/202605021211"               # メタ + 取得サマリ
curl "$URL/races/202605021211/entries.csv"   # 出馬表 CSV
```

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
| `KEIBA_FETCH_TOKEN` | （無し） | 設定すると `/fetch` に `?token=` / `X-Auth-Token` を要求 |

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
