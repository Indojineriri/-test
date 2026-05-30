#!/usr/bin/env bash
set -euo pipefail

# Cloud Run deployment script for the keiba (競馬データ収集) service.
# Usage (run from anywhere; the script cd's to repo root):
#   ./deploy/keiba/deploy.sh
# Override via env vars, e.g.:
#   PROJECT_ID=my-proj SERVICE=keiba-staging ./deploy/keiba/deploy.sh
#
# Prereqs: gcloud CLI authenticated (`gcloud auth login`) with deploy rights.

# 既存の meeting-support と同じプロジェクトを既定にする
PROJECT_ID="${PROJECT_ID:-clean-pen-422206-d7}"
REGION="${REGION:-asia-northeast1}"
SERVICE="${SERVICE:-keiba}"
REPO="${REPO:-app-images}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
# データ保存用 GCS バケット（Cloud Run は揮発性なので永続化に必須）
# 既存の keiba_shohei バケットを既定にする（手元 fetch の保存先と共有）
BUCKET="${BUCKET:-keiba_shohei}"
# 出力先 URI（バケット直下の keiba/ prefix に保存）
STORAGE_URI="${STORAGE_URI:-gs://${BUCKET}/keiba}"
# 共有 Secret 名（生成AI フェーズで使用。存在すれば自動で注入）
ANTHROPIC_SECRET="${ANTHROPIC_SECRET:-anthropic-api-key}"

# スクリプトの場所から root を特定して移動（ビルドコンテキストが root のため）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

echo "==> Project: $PROJECT_ID  Region: $REGION  Service: $SERVICE"
echo "==> Storage: $STORAGE_URI (bucket: $BUCKET)"

gcloud config set project "$PROJECT_ID"

# 1) 必要な API を有効化
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
    secretmanager.googleapis.com cloudbuild.googleapis.com storage.googleapis.com

# 2) Artifact Registry リポジトリ（無ければ作成）
if ! gcloud artifacts repositories describe "$REPO" --location "$REGION" \
        >/dev/null 2>&1; then
    echo "==> Creating Artifact Registry repo: $REPO"
    gcloud artifacts repositories create "$REPO" \
        --repository-format=docker --location "$REGION"
fi

# 3) GCS バケット（無ければ作成）
if ! gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1; then
    echo "==> Creating GCS bucket: gs://$BUCKET"
    gcloud storage buckets create "gs://$BUCKET" \
        --location "$REGION" --uniform-bucket-level-access
fi

# 4) イメージをビルド & push（root コンテキスト / deploy/keiba/Dockerfile）
IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/$REPO/$SERVICE:$IMAGE_TAG"
echo "==> Building image: $IMAGE"
gcloud builds submit --config deploy/keiba/cloudbuild.yaml \
    --substitutions "_IMAGE=$IMAGE" .

# 5) ランタイム SA を特定し、バケットへの読み書き権限を付与
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
RUNTIME_SA="${RUNTIME_SA:-${PROJECT_NUMBER}-compute@developer.gserviceaccount.com}"
echo "==> Granting storage access to runtime SA: $RUNTIME_SA"
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
    --member "serviceAccount:$RUNTIME_SA" \
    --role roles/storage.objectAdmin >/dev/null

# 6) Secret 注入（あれば）
#    - anthropic-api-key : 生成AI フェーズ用（共有）
#    - keiba-proxy       : 外向きプロキシ URL（netkeiba の IP ブロック回避）
SECRET_ARGS=()
if gcloud secrets describe "$ANTHROPIC_SECRET" >/dev/null 2>&1; then
    echo "==> Found secret '$ANTHROPIC_SECRET' — injecting as ANTHROPIC_API_KEY"
    SECRET_ARGS+=(--set-secrets "ANTHROPIC_API_KEY=${ANTHROPIC_SECRET}:latest")
    gcloud secrets add-iam-policy-binding "$ANTHROPIC_SECRET" \
        --member "serviceAccount:$RUNTIME_SA" \
        --role roles/secretmanager.secretAccessor >/dev/null || true
else
    echo "==> Secret '$ANTHROPIC_SECRET' not found — skipping (生成AI は後フェーズ)"
fi

PROXY_SECRET="${PROXY_SECRET:-keiba-proxy}"
if gcloud secrets describe "$PROXY_SECRET" >/dev/null 2>&1; then
    echo "==> Found secret '$PROXY_SECRET' — injecting as KEIBA_PROXY (IP ブロック回避)"
    # 既存の --set-secrets を上書きしないよう、カンマ連結で追記
    if [ ${#SECRET_ARGS[@]} -gt 0 ]; then
        SECRET_ARGS[1]="${SECRET_ARGS[1]},KEIBA_PROXY=${PROXY_SECRET}:latest"
    else
        SECRET_ARGS=(--set-secrets "KEIBA_PROXY=${PROXY_SECRET}:latest")
    fi
    gcloud secrets add-iam-policy-binding "$PROXY_SECRET" \
        --member "serviceAccount:$RUNTIME_SA" \
        --role roles/secretmanager.secretAccessor >/dev/null || true
else
    echo "==> Secret '$PROXY_SECRET' not found — KEIBA_PROXY 無しでデプロイ"
    echo "    （netkeiba が 403 になる場合は keiba-proxy を作成して再デプロイ。"
    echo "      詳細は deploy/keiba/README.md の『IP ブロックと対策』）"
fi

# 7) Cloud Run へデプロイ
echo "==> Deploying to Cloud Run"
gcloud run deploy "$SERVICE" \
    --image "$IMAGE" \
    --region "$REGION" \
    --platform managed \
    --allow-unauthenticated \
    --service-account "$RUNTIME_SA" \
    --memory 1Gi \
    --cpu 1 \
    --timeout 600 \
    --concurrency 4 \
    --set-env-vars "KEIBA_STORAGE_URI=${STORAGE_URI},KEIBA_FETCH_WAIT=1.5" \
    "${SECRET_ARGS[@]}"

echo "==> Done. Service URL:"
gcloud run services describe "$SERVICE" --region "$REGION" \
    --format 'value(status.url)'

cat <<'EOF'

----------------------------------------------------------------------
このサービスは「参照・予想」専用です（データ取得は手元の回線で行います）。

データ取得（手元の PC で実行 / netkeiba は GCP IP を 403 で弾くため）:
  export PYTHONPATH=src
  python3 -m keiba.cli fetch --race-id 202605021211 --out gs://<bucket>/keiba

Cloud Run で参照:
  URL="$(gcloud run services describe keiba --region asia-northeast1 \
         --format 'value(status.url)')"
  curl "$URL/healthz"
  curl "$URL/races"
  curl "$URL/races/202605021211"
  curl "$URL/races/202605021211/entries.csv"

注意:
  --allow-unauthenticated は URL を知る誰でもアクセス可能になります。
  社内利用なら下記いずれかを推奨:
    (a) --no-allow-unauthenticated にして IAM(run.invoker) で限定
    (b) KEIBA_FETCH_TOKEN を Secret で設定して保護
----------------------------------------------------------------------
EOF
