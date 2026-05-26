#!/usr/bin/env bash
# Deploy the research-app to Google Cloud Run.
#
# Run this script from the research-app/ directory:
#   cd research-app
#   ./deploy.sh
#
# Optional overrides:
#   PROJECT_ID (default clean-pen-422206-d7), REGION (default asia-northeast1),
#   SERVICE (default research-app), ANTHROPIC_MODEL (default claude-opus-4-7),
#   RUNTIME_SA (default compute SA)
#
# Prereqs: gcloud CLI authenticated (`gcloud auth login`) with rights to deploy.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-clean-pen-422206-d7}"
REGION="${REGION:-asia-northeast1}"
SERVICE="${SERVICE:-research-app}"
MODEL="${ANTHROPIC_MODEL:-claude-opus-4-7}"
SECRET_NAME="anthropic-api-key"

gcloud config set project "$PROJECT_ID"

echo "==> Enabling required APIs..."
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com

# Reuse the same Anthropic key secret as the meeting-support app if it exists.
if ! gcloud secrets describe "$SECRET_NAME" >/dev/null 2>&1; then
  echo "==> Enter your ANTHROPIC_API_KEY (input hidden):"
  read -rs API_KEY
  printf '%s' "$API_KEY" | gcloud secrets create "$SECRET_NAME" --data-file=-
else
  echo "==> Secret '$SECRET_NAME' already exists; reusing it."
fi

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
RUNTIME_SA="${RUNTIME_SA:-${PROJECT_NUMBER}-compute@developer.gserviceaccount.com}"

echo "==> Granting the runtime SA access to the secret..."
gcloud secrets add-iam-policy-binding "$SECRET_NAME" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/secretmanager.secretAccessor" >/dev/null

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
  --timeout 600 \
  --set-env-vars "ANTHROPIC_MODEL=${MODEL}" \
  --set-secrets "ANTHROPIC_API_KEY=${SECRET_NAME}:latest"

echo "==> Done. Service URL:"
gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)'
