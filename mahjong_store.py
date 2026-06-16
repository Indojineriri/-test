"""Persistence for the mahjong scheduler.

A single JSON document holds the whole state (members + their availability per
weekend slot). It is stored in GCS when a bucket is configured, otherwise on the
local filesystem so the app stays usable without any cloud setup.

Document shape::

    {
      "members": ["あき", "ぼぶ", ...],
      "responses": {
        "2026-06-20|day":   {"あき": "○", "ぼぶ": "×"},
        "2026-06-20|night": {"あき": "△"},
        ...
      }
    }
"""

import json
import os

import config

_BLOB_NAME = "mahjong/availability.json"
_LOCAL_PATH = os.getenv("MAHJONG_LOCAL_PATH", "data/mahjong.json")

EMPTY = {"members": [], "responses": {}}


def _gcs_blob():
    """Return the GCS blob handle, or None if GCS is not configured."""
    if not config.GCS_BUCKET:
        return None
    from google.cloud import storage

    client = storage.Client()
    return client.bucket(config.GCS_BUCKET).blob(_BLOB_NAME)


def load() -> dict:
    """Load the state document, returning a fresh empty one if none exists."""
    blob = _gcs_blob()
    if blob is not None:
        if not blob.exists():
            return json.loads(json.dumps(EMPTY))
        return json.loads(blob.download_as_text())

    if os.path.exists(_LOCAL_PATH):
        with open(_LOCAL_PATH, encoding="utf-8") as f:
            return json.load(f)
    return json.loads(json.dumps(EMPTY))


def save(state: dict) -> None:
    """Persist the state document to GCS or the local filesystem."""
    payload = json.dumps(state, ensure_ascii=False, indent=2)

    blob = _gcs_blob()
    if blob is not None:
        blob.upload_from_string(payload, content_type="application/json")
        return

    os.makedirs(os.path.dirname(_LOCAL_PATH) or ".", exist_ok=True)
    with open(_LOCAL_PATH, "w", encoding="utf-8") as f:
        f.write(payload)


def location_label() -> str:
    """Human-readable description of where data is being stored."""
    if config.GCS_BUCKET:
        return f"GCS: gs://{config.GCS_BUCKET}/{_BLOB_NAME}"
    return f"ローカル: {_LOCAL_PATH}"
