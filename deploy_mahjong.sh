#!/usr/bin/env bash
# Deploy the mahjong scheduler to Google Cloud Run.
#
# Usage:
#   ./deploy_mahjong.sh
#
# Optional overrides:
#   PROJECT_ID (default clean-pen-422206-d7), GCS_BUCKET (default test_reseach),
#   GCS_PREFIX (default mahjong), REGION (default asia-northeast1),
#   SERVICE (default mahjong-scheduler), RUNTIME_SA (default compute SA)
#
# Prereqs: gcloud CLI authenticated with rights to deploy, and an existing
# GCS bucket. No Anthropic API key is needed — this app does not call the API.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-clean-pen-422206-d7}"
GCS_BUCKET="${GCS_BUCKET:-test_reseach}"
GCS_PREFIX="${GCS_PREFIX:-mahjong}"
REGION="${REGION:-asia-northeast1}"
SERVICE="${SERVICE:-mahjong-scheduler}"

gcloud config set project "$PROJECT_ID"

echo "==> Enabling required APIs..."
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  storage.googleapis.com

# Runtime service account (defaults to the project's compute SA).
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
RUNTIME_SA="${RUNTIME_SA:-${PROJECT_NUMBER}-compute@developer.gserviceaccount.com}"

echo "==> Granting the runtime SA read/write access to the bucket..."
gcloud storage buckets add-iam-policy-binding "gs://${GCS_BUCKET}" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/storage.objectAdmin"

echo "==> Building and deploying to Cloud Run..."
# WARNING: --allow-unauthenticated makes the URL public. Anyone with the link
# can view and edit the schedule. To restrict, swap for
# --no-allow-unauthenticated and grant run.invoker to specific users.
gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --service-account "$RUNTIME_SA" \
  --allow-unauthenticated \
  --memory 1Gi \
  --cpu 1 \
  --timeout 300 \
  --set-env-vars "APP_FILE=mahjong_app.py,GCS_BUCKET=${GCS_BUCKET},MAHJONG_GCS_PREFIX=${GCS_PREFIX}"

echo "==> Done. Service URL:"
gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)'
