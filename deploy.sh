#!/usr/bin/env bash
# Deploy the meeting-support app to Google Cloud Run.
#
# Usage (defaults baked in for this project):
#   ./deploy.sh
#
# Optional overrides:
#   PROJECT_ID (default clean-pen-422206-d7), GCS_BUCKET (default test_reseach),
#   REGION (default asia-northeast1), SERVICE (default meeting-support),
#   ANTHROPIC_MODEL (default claude-opus-4-7), RUNTIME_SA (default compute SA)
#
# Prereqs: gcloud CLI authenticated (`gcloud auth login`) with rights to deploy,
# and an existing GCS bucket.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-clean-pen-422206-d7}"
GCS_BUCKET="${GCS_BUCKET:-test_reseach}"
REGION="${REGION:-asia-northeast1}"
SERVICE="${SERVICE:-meeting-support}"
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

# Store the Anthropic API key in Secret Manager (asked once, input hidden).
if ! gcloud secrets describe "$SECRET_NAME" >/dev/null 2>&1; then
  echo "==> Enter your ANTHROPIC_API_KEY (input hidden):"
  read -rs API_KEY
  printf '%s' "$API_KEY" | gcloud secrets create "$SECRET_NAME" --data-file=-
else
  echo "==> Secret '$SECRET_NAME' already exists; reusing it."
fi

# Runtime service account (defaults to the project's compute SA).
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
RUNTIME_SA="${RUNTIME_SA:-${PROJECT_NUMBER}-compute@developer.gserviceaccount.com}"

echo "==> Granting the runtime SA access to the secret and the bucket..."
gcloud secrets add-iam-policy-binding "$SECRET_NAME" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/secretmanager.secretAccessor"
gcloud storage buckets add-iam-policy-binding "gs://${GCS_BUCKET}" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/storage.objectAdmin"

echo "==> Building and deploying to Cloud Run..."
# WARNING: --allow-unauthenticated makes the URL public, so anyone who finds it
# can use your Anthropic credits. To restrict, swap for --no-allow-unauthenticated
# and put it behind IAP or grant run.invoker to specific users.
gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --service-account "$RUNTIME_SA" \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 2 \
  --timeout 3600 \
  --set-env-vars "GCS_BUCKET=${GCS_BUCKET},GCS_PREFIX=meeting-decks,ANTHROPIC_MODEL=${MODEL}" \
  --set-secrets "ANTHROPIC_API_KEY=${SECRET_NAME}:latest"

echo "==> Done. Service URL:"
gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)'
