"""ローカル / GCS を切り替えられるストレージ抽象。

Cloud Run のディスクは揮発性なので、取得した HTML キャッシュや出力 CSV は GCS に
永続化する必要がある。一方、開発・テストはネットワーク無しでローカルに行いたい。
そこで「base URI」で実体を切り替える薄い Storage を用意する:

    Storage.from_uri("gs://my-bucket/keiba")  -> GCS バックエンド
    Storage.from_uri("/var/data/keiba")        -> ローカルファイルバックエンド
    Storage.from_uri("file:///tmp/keiba")      -> 同上

これにより NetkeibaClient（HTMLキャッシュ）も web 層（CSV出力）も同じ API で
ローカル/GCS を意識せず読み書きできる。
"""

from __future__ import annotations

import io
from abc import ABC, abstractmethod
from pathlib import Path
from urllib.parse import urlparse


class Storage(ABC):
    """相対パス(rel)に対する read/write を提供する最小インターフェース。"""

    @abstractmethod
    def read_text(self, rel: str) -> str | None:
        """無ければ None を返す。"""

    @abstractmethod
    def write_text(self, rel: str, text: str) -> None: ...

    @abstractmethod
    def write_bytes(self, rel: str, data: bytes) -> None: ...

    @abstractmethod
    def exists(self, rel: str) -> bool: ...

    @abstractmethod
    def list(self, prefix: str = "") -> list[str]:
        """prefix 配下の相対パス一覧。"""

    @abstractmethod
    def uri(self, rel: str = "") -> str:
        """人間/ログ向けの完全 URI。"""

    @staticmethod
    def from_uri(base: str) -> "Storage":
        parsed = urlparse(base)
        if parsed.scheme == "gs":
            return GcsStorage(bucket=parsed.netloc,
                              prefix=parsed.path.lstrip("/"))
        if parsed.scheme in ("", "file"):
            path = parsed.path if parsed.scheme == "file" else base
            return LocalStorage(path)
        raise ValueError(f"未対応のストレージ URI: {base}")


class LocalStorage(Storage):
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, rel: str) -> Path:
        return self.root / rel

    def read_text(self, rel: str) -> str | None:
        p = self._p(rel)
        return p.read_text(encoding="utf-8") if p.exists() else None

    def write_text(self, rel: str, text: str) -> None:
        p = self._p(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def write_bytes(self, rel: str, data: bytes) -> None:
        p = self._p(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def exists(self, rel: str) -> bool:
        return self._p(rel).exists()

    def list(self, prefix: str = "") -> list[str]:
        base = self._p(prefix)
        if not base.exists():
            return []
        if base.is_file():
            return [prefix]
        return sorted(str(p.relative_to(self.root)) for p in base.rglob("*") if p.is_file())

    def uri(self, rel: str = "") -> str:
        return str(self._p(rel))


class GcsStorage(Storage):
    """Google Cloud Storage バックエンド（google-cloud-storage を遅延 import）。"""

    def __init__(self, bucket: str, prefix: str = ""):
        self.bucket_name = bucket
        self.prefix = prefix.rstrip("/")
        self._client = None
        self._bucket = None

    def _b(self):
        if self._bucket is None:
            from google.cloud import storage  # 遅延 import（ローカルでは不要）
            self._client = storage.Client()
            self._bucket = self._client.bucket(self.bucket_name)
        return self._bucket

    def _key(self, rel: str) -> str:
        return f"{self.prefix}/{rel}" if self.prefix else rel

    def read_text(self, rel: str) -> str | None:
        blob = self._b().blob(self._key(rel))
        if not blob.exists():
            return None
        return blob.download_as_text(encoding="utf-8")

    def write_text(self, rel: str, text: str) -> None:
        self._b().blob(self._key(rel)).upload_from_string(
            text, content_type="text/plain; charset=utf-8")

    def write_bytes(self, rel: str, data: bytes) -> None:
        self._b().blob(self._key(rel)).upload_from_file(io.BytesIO(data))

    def exists(self, rel: str) -> bool:
        return self._b().blob(self._key(rel)).exists()

    def list(self, prefix: str = "") -> list[str]:
        full = self._key(prefix)
        plen = len(self.prefix) + 1 if self.prefix else 0
        return sorted(b.name[plen:] for b in self._b().list_blobs(prefix=full))

    def uri(self, rel: str = "") -> str:
        return f"gs://{self.bucket_name}/{self._key(rel)}".rstrip("/")
