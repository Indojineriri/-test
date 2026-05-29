#!/usr/bin/env bash
# Deploy the board game catalog app (boardgame/) to Google Cloud Run.
#
# Usage (run from the boardgame/ directory):
#   ./deploy.sh
#
# Optional overrides:
#   PROJECT_ID (default clean-pen-422206-d7), REGION (default asia-northeast1),
#   SERVICE (default boardgame), ANTHROPIC_MODEL (default claude-opus-4-7)
#
# Prereqs: gcloud CLI authenticated (`gcloud auth login`) with deploy rights.
#
# Data persistence: the SQLite DB is kept in GCS (download on boot, re-upload
# after writes), reusing the same bucket/credentials as the meeting-support
# app. This survives Cloud Run's ephemeral disk with no Cloud SQL needed.
# Because the whole file is replaced per write, the service is pinned to a
# single instance (--max-instances 1) to avoid concurrent writers clobbering.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-clean-pen-422206-d7}"
GCS_BUCKET="${GCS_BUCKET:-test_reseach}"
GCS_DB_BLOB="${GCS_DB_BLOB:-boardgame/boardgames.db}"
REGION="${REGION:-asia-northeast1}"
SERVICE="${SERVICE:-boardgame}"
MODEL="${ANTHROPIC_MODEL:-claude-opus-4-7}"
SECRET_NAME="anthropic-api-key"

gcloud config set project "$PROJECT_ID"

echo "==> Enabling required APIs..."
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com

# The Anthropic key is OPTIONAL here (only the "AIでルール生成" button uses it).
# We reuse the shared secret if it exists; otherwise the app still runs fine
# without it. Set CREATE_SECRET=1 to be prompted to create one.
USE_SECRET=0
if gcloud secrets describe "$SECRET_NAME" >/dev/null 2>&1; then
  echo "==> Secret '$SECRET_NAME' found; will wire it in for AI rule generation."
  USE_SECRET=1
elif [ "${CREATE_SECRET:-0}" = "1" ]; then
  echo "==> Enter your ANTHROPIC_API_KEY (input hidden):"
  read -rs API_KEY
  printf '%s' "$API_KEY" | gcloud secrets create "$SECRET_NAME" --data-file=-
  USE_SECRET=1
else
  echo "==> No '$SECRET_NAME' secret; deploying without AI generation (catalog works fully)."
fi

# Runtime service account (defaults to the project's compute SA).
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
RUNTIME_SA="${RUNTIME_SA:-${PROJECT_NUMBER}-compute@developer.gserviceaccount.com}"

echo "==> Granting the runtime SA read/write on gs://${GCS_BUCKET} (DB storage)..."
gcloud storage buckets add-iam-policy-binding "gs://${GCS_BUCKET}" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/storage.objectAdmin"

SECRET_ARGS=()
if [ "$USE_SECRET" = "1" ]; then
  gcloud secrets add-iam-policy-binding "$SECRET_NAME" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role="roles/secretmanager.secretAccessor"
  SECRET_ARGS=(--set-secrets "ANTHROPIC_API_KEY=${SECRET_NAME}:latest")
fi

echo "==> Building and deploying to Cloud Run..."
# WARNING: --allow-unauthenticated makes the URL public. The catalog is read-only
# so that's usually fine, but if the Anthropic secret is wired in, anyone could
# trigger AI generation and spend credits. To restrict, swap for
# --no-allow-unauthenticated and put it behind IAP or grant run.invoker.
gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --service-account "$RUNTIME_SA" \
  --allow-unauthenticated \
  --memory 512Mi \
  --cpu 1 \
  --timeout 120 \
  --max-instances 1 \
  --set-env-vars "ANTHROPIC_MODEL=${MODEL},GCS_BUCKET=${GCS_BUCKET},GCS_DB_BLOB=${GCS_DB_BLOB}" \
  "${SECRET_ARGS[@]}"

echo "==> Done. Service URL:"
gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)'
