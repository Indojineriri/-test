"""出展企業データ（スクレイピング結果）の永続化。

一度取得したデータを保存し、次回アクセス時に自動で復元するための薄い層。
Cloud Run はインスタンスのローカルディスクが揮発するため、GCS バケットが
設定されていれば GCS を正とし、未設定ならローカルファイルにフォールバックする。

保存対象は「出展企業の dict 配列」（exhibitor_scraper の出力と同形式）。
"""

from __future__ import annotations

import json
import os

import config

LOCAL_PATH = os.environ.get("EXHIBITOR_DATA_PATH", ".exhibitor_data/exhibitors.json")
GCS_OBJECT = os.environ.get("EXHIBITOR_DATA_OBJECT", "exhibitor-finder/exhibitors.json")


def _gcs_blob():
    from google.cloud import storage

    client = storage.Client()
    return client.bucket(config.GCS_BUCKET).blob(GCS_OBJECT)


def save_exhibitors(data: list[dict]) -> str:
    """データを保存し、保存先を示す文字列を返す（"gcs+local" / "local"）。

    GCS への保存に失敗してもローカル保存は行い、例外で全体を止めない。
    """
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")

    os.makedirs(os.path.dirname(LOCAL_PATH) or ".", exist_ok=True)
    with open(LOCAL_PATH, "wb") as f:
        f.write(payload)

    if config.GCS_BUCKET:
        try:
            _gcs_blob().upload_from_string(payload, content_type="application/json")
            return "gcs+local"
        except Exception:  # noqa: BLE001
            return "local"
    return "local"


def load_exhibitors() -> list[dict] | None:
    """保存済みデータを復元。GCS を優先し、無ければローカル。無ければ None。"""
    if config.GCS_BUCKET:
        try:
            blob = _gcs_blob()
            if blob.exists():
                return json.loads(blob.download_as_bytes())
        except Exception:  # noqa: BLE001
            pass
    if os.path.exists(LOCAL_PATH):
        try:
            with open(LOCAL_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            return None
    return None


def has_saved() -> bool:
    if config.GCS_BUCKET:
        try:
            if _gcs_blob().exists():
                return True
        except Exception:  # noqa: BLE001
            pass
    return os.path.exists(LOCAL_PATH)
