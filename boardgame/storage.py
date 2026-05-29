"""GCS-backed persistence for the SQLite database.

Cloud Run's local disk is ephemeral, so we keep the canonical SQLite file in a
GCS bucket: download it on startup and re-upload it after every write. This
reuses the same bucket and Application Default Credentials as the existing
meeting-support app — no extra infrastructure (e.g. Cloud SQL) is needed.

Trade-off: GCS is object storage, not a transactional DB. The whole file is
replaced on each write, so concurrent writers can clobber each other ("last
write wins"). For this app's low write volume that is fine *as long as the
service runs a single instance* — see deploy.sh (`--max-instances 1`).
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def _blob(bucket_name: str, blob_name: str):
    from google.cloud import storage

    client = storage.Client()
    return client.bucket(bucket_name).blob(blob_name)


def download_db(bucket_name: str, blob_name: str, local_path: str) -> bool:
    """Fetch the DB file from GCS to local_path. Returns True if it existed.

    Missing object or auth/network errors are non-fatal: we just start from an
    empty DB (which then gets seeded and uploaded).
    """
    try:
        blob = _blob(bucket_name, blob_name)
        if blob.exists():
            blob.download_to_filename(local_path)
            log.info("Loaded DB from gs://%s/%s", bucket_name, blob_name)
            return True
        log.info("No existing DB at gs://%s/%s; starting fresh", bucket_name, blob_name)
    except Exception as e:  # noqa: BLE001
        log.warning("GCS download failed (%s); starting from local DB", e)
    return False


def upload_db(bucket_name: str, blob_name: str, local_path: str) -> None:
    """Upload the local DB file to GCS, overwriting the previous copy."""
    blob = _blob(bucket_name, blob_name)
    blob.upload_from_filename(local_path)
    log.info("Synced DB to gs://%s/%s", bucket_name, blob_name)
