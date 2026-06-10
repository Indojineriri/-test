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
# 判定結果キャッシュ（run_id ごとの JSONL）の GCS 配置先プレフィクス。
ASSESS_PREFIX = os.environ.get("ASSESSMENT_OBJECT_PREFIX", "exhibitor-finder/assessments")


def _bucket():
    from google.cloud import storage

    return storage.Client().bucket(config.GCS_BUCKET)


def _gcs_blob():
    return _bucket().blob(GCS_OBJECT)


def _assess_blob(run_id: str):
    return _bucket().blob(f"{ASSESS_PREFIX}/{run_id}.jsonl")


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


# --- 判定結果キャッシュ（run_id 単位の JSONL）の GCS ミラー -------------------
# ローカル JSONL を正本として逐次追記し、その内容を GCS にミラーする。
# Cloud Run はディスクが揮発するため、GCS に置くことでインスタンス入替後も残る。

def push_assessment_cache(run_id: str, local_path: str) -> bool:
    """ローカル JSONL の現在の内容を GCS にアップロード（上書き）。"""
    if not config.GCS_BUCKET or not os.path.exists(local_path):
        return False
    try:
        with open(local_path, "rb") as fh:
            data = fh.read()
        _assess_blob(run_id).upload_from_string(data, content_type="application/x-ndjson")
        return True
    except Exception:  # noqa: BLE001
        return False


def pull_assessment_cache(run_id: str, local_path: str) -> bool:
    """GCS に保存済みで手元に無ければダウンロードして復元。復元したら True。"""
    if not config.GCS_BUCKET:
        return False
    try:
        blob = _assess_blob(run_id)
        if not blob.exists():
            return False
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        blob.download_to_filename(local_path)
        return True
    except Exception:  # noqa: BLE001
        return False


def delete_assessment_cache(run_id: str, local_path: str) -> None:
    """ローカルと GCS の両方の判定結果キャッシュを削除。"""
    if os.path.exists(local_path):
        os.remove(local_path)
    if config.GCS_BUCKET:
        try:
            blob = _assess_blob(run_id)
            if blob.exists():
                blob.delete()
        except Exception:  # noqa: BLE001
            pass
