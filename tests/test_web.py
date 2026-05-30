"""Storage 抽象と web/service 層のオフライン検証（ネットワーク・GCS 不要）。

LocalStorage と、フィクスチャ HTML を返すフェイククライアントを使い、
「取得 → 保存 → HTTP 参照」までを通しで確認する。

    python3 tests/test_web.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
FIXTURES = Path(__file__).resolve().parent / "fixtures"

from keiba.storage import Storage, LocalStorage  # noqa: E402
from keiba.collect.netkeiba import NetkeibaDataSource  # noqa: E402
from keiba import service  # noqa: E402


def _read(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeClient:
    """フィクスチャ HTML を返すフェイク（ネットワークを使わない）。"""

    def shutuba_html(self, race_id):
        return _read("shutuba.html")

    def horse_html(self, horse_id):
        return _read("horse.html")

    def race_result_html(self, race_id):
        return _read("race_result.html")


# --- Storage --------------------------------------------------------------

def test_local_storage_roundtrip(tmp):
    st = LocalStorage(tmp / "store")
    assert st.read_text("a/b.txt") is None
    st.write_text("a/b.txt", "ほげ")
    assert st.exists("a/b.txt")
    assert st.read_text("a/b.txt") == "ほげ"
    st.write_text("a/c.txt", "ふが")
    assert st.list("a/") == ["a/b.txt", "a/c.txt"]


def test_storage_from_uri_dispatch():
    assert isinstance(Storage.from_uri("/tmp/x"), LocalStorage)
    assert isinstance(Storage.from_uri("file:///tmp/x"), LocalStorage)
    gs = Storage.from_uri("gs://my-bucket/keiba")
    assert gs.__class__.__name__ == "GcsStorage"
    assert gs.bucket_name == "my-bucket" and gs.prefix == "keiba"
    assert gs.uri("races/x") == "gs://my-bucket/keiba/races/x"


# --- service 層（取得→保存→参照） ----------------------------------------

def test_fetch_and_store_via_fake(tmp, monkeypatch):
    store = LocalStorage(tmp / "out")
    import keiba.service as svc

    def fake_ds(race_id, client=None, max_history_per_horse=None):
        return NetkeibaDataSource(race_id, client=FakeClient(),
                                  max_history_per_horse=max_history_per_horse)

    monkeypatch.setattr(svc, "NetkeibaDataSource", fake_ds)
    summary = svc.fetch_and_store("202605021211", store, use_cache=False)

    assert summary["race_id"] == "202605021211"
    assert summary["counts"]["entries"] == 3
    assert summary["counts"]["results"] >= 3
    assert store.read_text("races/202605021211/entries.csv") is not None
    assert service.load_meta("202605021211", store)["race_id"] == "202605021211"
    assert service.list_races(store) == ["202605021211"]
    csv = service.read_csv_text("202605021211", "entries", store)
    assert "テストランナー" in csv


# --- web 層（Flask テストクライアント） -----------------------------------

def test_web_endpoints(tmp, monkeypatch):
    monkeypatch.setenv("KEIBA_STORAGE_URI", str(tmp / "web"))

    import keiba.service as svc
    from keiba.web import app as webmod

    def fake_ds(race_id, client=None, max_history_per_horse=None):
        return NetkeibaDataSource(race_id, client=FakeClient(),
                                  max_history_per_horse=max_history_per_horse)

    monkeypatch.setattr(svc, "NetkeibaDataSource", fake_ds)
    client = webmod.app.test_client()

    assert client.get("/healthz").get_json()["status"] == "ok"
    assert client.get("/fetch").status_code == 400  # race_id 無し

    r = client.get("/fetch?race_id=202605021211")
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["counts"]["entries"] == 3

    assert "202605021211" in client.get("/races").get_json()["races"]
    assert client.get("/races/202605021211").get_json()["race_id"] == "202605021211"
    csv = client.get("/races/202605021211/entries.csv")
    assert csv.status_code == 200 and "テストランナー" in csv.get_data(as_text=True)

    assert client.get("/races/NOPE").status_code == 404
    assert client.get("/races/202605021211/bogus.csv").status_code == 400


def test_web_token_protection(tmp, monkeypatch):
    monkeypatch.setenv("KEIBA_STORAGE_URI", str(tmp / "wtok"))
    monkeypatch.setenv("KEIBA_FETCH_TOKEN", "secret")
    from keiba.web import app as webmod
    client = webmod.app.test_client()
    assert client.get("/fetch?race_id=202605021211").status_code == 401
    assert client.get("/fetch?race_id=2026&token=wrong").status_code == 401


# --- 簡易テストランナー（pytest 無し環境でも動く） ------------------------

class _MonkeyPatch:
    def __init__(self):
        self._env = []
        self._attr = []

    def setenv(self, k, v):
        import os
        self._env.append((k, os.environ.get(k)))
        os.environ[k] = v

    def setattr(self, obj, name, val):
        self._attr.append((obj, name, getattr(obj, name)))
        setattr(obj, name, val)

    def undo(self):
        import os
        for k, old in reversed(self._env):
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old
        for obj, name, old in reversed(self._attr):
            setattr(obj, name, old)


if __name__ == "__main__":
    import tempfile

    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = 0
    for name, fn in fns:
        with tempfile.TemporaryDirectory() as d:
            mp = _MonkeyPatch()
            try:
                kwargs = {}
                params = fn.__code__.co_varnames[:fn.__code__.co_argcount]
                if "tmp" in params:
                    kwargs["tmp"] = Path(d)
                if "monkeypatch" in params:
                    kwargs["monkeypatch"] = mp
                fn(**kwargs)
                print(f"  ok: {name}")
                passed += 1
            finally:
                mp.undo()
    print(f"OK: {passed} tests passed")
