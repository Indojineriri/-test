from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from models import Case, VendorCase

GCS_BUCKET = os.getenv("GCS_BUCKET", "test_reseach")
GCS_PREFIX = os.getenv("RESEARCH_HISTORY_PREFIX", "research-history")
LOCAL_FALLBACK_DIR = Path("/tmp/research-history")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _filename(entry_id: str, created_at: str) -> str:
    """Sort-friendly filename: YYYYMMDDTHHMMSS-uuid.json (lexicographic = chronological)."""
    ts = created_at.replace(":", "").replace("-", "")[:15]
    return f"{ts}-{entry_id}.json"


def _gcs_client():
    try:
        from google.cloud import storage
        return storage.Client()
    except Exception:
        return None


def save_entry(
    *,
    theme: str,
    mode: str,
    model: str,
    user_name: str,
    user_email: str,
    cases: list = None,
    vendors: list = None,
    kind: str = "case",
) -> dict:
    """Persist one research run. Returns the saved entry (with `_storage`).

    `kind` is either 'case' (technical case research) or 'vendor' (vendor research).
    Pass `cases=` for kind=case, `vendors=` for kind=vendor.
    """
    payload: list[dict] = []
    if vendors is not None:
        payload = [v.model_dump() for v in vendors]
    elif cases is not None:
        payload = [c.model_dump() for c in cases]
    entry = {
        "id": str(uuid.uuid4()),
        "created_at": _now_iso(),
        "kind": kind,
        "theme": theme.strip(),
        "mode": mode,
        "model": model,
        "user_name": (user_name or "").strip(),
        "user_email": (user_email or "").strip(),
        "n_cases": len(payload),
        "cases": payload,
    }
    blob_name = _filename(entry["id"], entry["created_at"])
    data = json.dumps(entry, ensure_ascii=False, indent=2).encode("utf-8")

    if GCS_BUCKET:
        client = _gcs_client()
        if client is not None:
            try:
                bucket = client.bucket(GCS_BUCKET)
                blob = bucket.blob(f"{GCS_PREFIX}/{blob_name}")
                blob.upload_from_string(data, content_type="application/json")
                entry["_storage"] = f"gs://{GCS_BUCKET}/{GCS_PREFIX}/{blob_name}"
                return entry
            except Exception as e:
                entry["_storage_error"] = str(e)

    LOCAL_FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
    path = LOCAL_FALLBACK_DIR / blob_name
    path.write_bytes(data)
    entry.setdefault("_storage", f"file://{path}")
    return entry


def list_entries(limit: int = 200) -> list[dict]:
    """List entries newest first."""
    if GCS_BUCKET:
        client = _gcs_client()
        if client is not None:
            try:
                bucket = client.bucket(GCS_BUCKET)
                blobs = list(bucket.list_blobs(prefix=f"{GCS_PREFIX}/"))
                blobs.sort(key=lambda b: b.name, reverse=True)
                out = []
                for blob in blobs[:limit]:
                    try:
                        out.append(json.loads(blob.download_as_bytes()))
                    except Exception:
                        continue
                return out
            except Exception:
                pass

    if LOCAL_FALLBACK_DIR.exists():
        files = sorted(LOCAL_FALLBACK_DIR.glob("*.json"), reverse=True)[:limit]
        out = []
        for f in files:
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue
        return out

    return []


def storage_status() -> str:
    if GCS_BUCKET:
        if _gcs_client() is not None:
            return f"GCS: gs://{GCS_BUCKET}/{GCS_PREFIX}/"
        return f"GCS 設定済みだがクライアント初期化に失敗 → ローカル: {LOCAL_FALLBACK_DIR}"
    return f"GCS 未設定 → ローカル: {LOCAL_FALLBACK_DIR}"


def cases_from_dict(entry: dict) -> list[Case]:
    """Reconstruct Case objects from a stored entry (kind='case')."""
    return [Case.model_validate(c) for c in entry.get("cases", [])]


def vendors_from_dict(entry: dict) -> list[VendorCase]:
    """Reconstruct VendorCase objects from a stored entry (kind='vendor')."""
    return [VendorCase.model_validate(c) for c in entry.get("cases", [])]
