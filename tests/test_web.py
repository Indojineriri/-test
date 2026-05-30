"""Storage 抽象と web/service 層のオフライン検証（ネットワーク・GCS 不要）。

LocalStorage と、フィクスチャ HTML を返すフェイククライアントを使い、
「取得 → 保存 → HTTP 参照」までを通しで確認する。

    python3 tests/test_web.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
FIXTURES = Path(__file__).resolve().parent / "fixtures"

import json  # noqa: E402

import pandas as pd  # noqa: E402

from keiba.storage import Storage, LocalStorage  # noqa: E402
from keiba.collect.netkeiba import NetkeibaDataSource  # noqa: E402
from keiba import service  # noqa: E402


def _read(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def _seed_derby(store, race_id, seed, race_name="第90回東京優駿(GI)"):
    """合成のダービー（出走馬+結果+actual.csv）を store に投入する。"""
    from test_dataset import _make_context
    ctx, strength, _ = _make_context(n_entrants=10, career=6, seed=seed)
    pfx = f"races/{race_id}"
    store.write_text(f"{pfx}/entries.csv", ctx.entries.to_csv(index=False))
    store.write_text(f"{pfx}/results.csv", ctx.history.to_csv(index=False))
    store.write_text(f"{pfx}/races.csv", ctx.races.to_csv(index=False))
    store.write_text(f"{pfx}/meta.json", json.dumps(
        {"race_id": race_id, "race_meta": {**ctx.race, "race_id": race_id,
         "race_name": race_name, "distance": 2400, "date": race_id[:4] + "-05-31"}},
        ensure_ascii=False, default=str))
    order = sorted(ctx.entries["horse_id"], key=lambda h: -strength[h])
    actual = pd.DataFrame({"horse_id": order,
                           "horse_name": [f"馬{h}" for h in order],
                           "finish_pos": list(range(1, len(order) + 1))})
    store.write_text(f"{pfx}/actual.csv", actual.to_csv(index=False))
    return ctx


class FakeClient:
    """フィクスチャ HTML を返すフェイク（ネットワークを使わない）。"""

    def shutuba_html(self, race_id):
        return _read("shutuba.html")

    def horse_html(self, horse_id):
        return _read("horse.html")

    def horse_result_html(self, horse_id):
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

    def fake_ds(race_id, client=None, max_history_per_horse=None, past_race=False):
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

def test_reference_endpoints(tmp, monkeypatch):
    """参照系（Cloud Run の本来の役割）: 手元取得済みデータを読めること。"""
    monkeypatch.setenv("KEIBA_STORAGE_URI", str(tmp / "web"))
    # 手元取得を模して、service で直接データを作っておく（参照対象）
    import keiba.service as svc
    from keiba.web import app as webmod

    def fake_ds(race_id, client=None, max_history_per_horse=None, past_race=False):
        return NetkeibaDataSource(race_id, client=FakeClient(),
                                  max_history_per_horse=max_history_per_horse)

    monkeypatch.setattr(svc, "NetkeibaDataSource", fake_ds)
    store = LocalStorage(tmp / "web")
    svc.fetch_and_store("202605021211", store, use_cache=False)  # 事前投入（=手元取得相当）

    client = webmod.app.test_client()
    assert client.get("/healthz").get_json()["status"] == "ok"

    # 参照系は常に有効
    assert "202605021211" in client.get("/races").get_json()["races"]
    assert client.get("/races/202605021211").get_json()["race_id"] == "202605021211"
    csv = client.get("/races/202605021211/entries.csv")
    assert csv.status_code == 200 and "テストランナー" in csv.get_data(as_text=True)
    assert client.get("/races/NOPE").status_code == 404
    assert client.get("/races/202605021211/bogus.csv").status_code == 400


def test_fetch_disabled_by_default(tmp, monkeypatch):
    """既定では Cloud Run の取得系は無効（403）。参照専用デプロイの担保。"""
    monkeypatch.setenv("KEIBA_STORAGE_URI", str(tmp / "ro"))
    # KEIBA_ENABLE_FETCH は設定しない
    from keiba.web import app as webmod
    client = webmod.app.test_client()

    r = client.get("/fetch?race_id=202605021211")
    assert r.status_code == 403
    assert "cli fetch" in r.get_json()["detail"]

    r = client.get("/diag")
    assert r.status_code == 403


def test_web_endpoints_when_fetch_enabled(tmp, monkeypatch):
    """開発時に KEIBA_ENABLE_FETCH=1 にすれば /fetch が動く。"""
    monkeypatch.setenv("KEIBA_STORAGE_URI", str(tmp / "web"))
    monkeypatch.setenv("KEIBA_ENABLE_FETCH", "1")

    import keiba.service as svc
    from keiba.web import app as webmod

    def fake_ds(race_id, client=None, max_history_per_horse=None, past_race=False):
        return NetkeibaDataSource(race_id, client=FakeClient(),
                                  max_history_per_horse=max_history_per_horse)

    monkeypatch.setattr(svc, "NetkeibaDataSource", fake_ds)
    client = webmod.app.test_client()

    assert client.get("/fetch").status_code == 400  # race_id 無し
    r = client.get("/fetch?race_id=202605021211")
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["counts"]["entries"] == 3


def test_web_token_protection(tmp, monkeypatch):
    monkeypatch.setenv("KEIBA_STORAGE_URI", str(tmp / "wtok"))
    monkeypatch.setenv("KEIBA_ENABLE_FETCH", "1")
    monkeypatch.setenv("KEIBA_FETCH_TOKEN", "secret")
    from keiba.web import app as webmod
    client = webmod.app.test_client()
    assert client.get("/fetch?race_id=202605021211").status_code == 401
    assert client.get("/fetch?race_id=2026&token=wrong").status_code == 401


def test_web_diag(tmp, monkeypatch):
    """/diag は（有効時）check_connectivity を呼び、到達なら 200・ブロックなら 502。"""
    monkeypatch.setenv("KEIBA_STORAGE_URI", str(tmp / "diag"))
    monkeypatch.setenv("KEIBA_ENABLE_FETCH", "1")
    from keiba.web import app as webmod

    monkeypatch.setattr(webmod, "check_connectivity",
                        lambda proxy=None: {"ok": True, "status": 200})
    client = webmod.app.test_client()
    r = client.get("/diag")
    assert r.status_code == 200 and r.get_json()["ok"] is True

    monkeypatch.setattr(webmod, "check_connectivity",
                        lambda proxy=None: {"ok": False, "status": 403, "blocked": True})
    r = client.get("/diag")
    assert r.status_code == 502 and r.get_json()["blocked"] is True


def test_web_fetch_blocked_returns_502(tmp, monkeypatch):
    """取得（有効時）が AccessBlockedError なら 502 + hint を返す。"""
    monkeypatch.setenv("KEIBA_STORAGE_URI", str(tmp / "blk"))
    monkeypatch.setenv("KEIBA_ENABLE_FETCH", "1")
    from keiba.web import app as webmod
    from keiba.collect.netkeiba_client import AccessBlockedError

    def boom(*a, **k):
        raise AccessBlockedError("HTTP 403 ...（現在プロキシ未設定）")

    monkeypatch.setattr(webmod, "fetch_and_store", boom)
    client = webmod.app.test_client()
    r = client.get("/fetch?race_id=202605021211")
    assert r.status_code == 502
    body = r.get_json()
    assert body["blocked"] is True and "KEIBA_PROXY" in body["hint"]


# --- 予想エンドポイント -----------------------------------------------------

def test_predict_endpoint(tmp, monkeypatch):
    """/predict/<race_id>: 過去ダービーで学習し複勝確率ランキングを返す（ML・API不要）。"""
    monkeypatch.setenv("KEIBA_STORAGE_URI", str(tmp / "pred"))
    store = LocalStorage(tmp / "pred")
    # 学習用の過去ダービー3年 + 対象1レースを投入
    for i, rid in enumerate(["202105021211", "202205021211", "202305021211"]):
        _seed_derby(store, rid, seed=10 + i)
    _seed_derby(store, "202605021211", seed=99, race_name="第93回東京優駿(GI)")

    from keiba.web import app as webmod
    client = webmod.app.test_client()
    r = client.get("/predict/202605021211")
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["race_id"] == "202605021211"
    assert len(body["ranking"]) == 10
    assert "show_prob" in body["ranking"][0]
    # 対象は学習から除外されている
    assert "202605021211" not in body["trained_on"]


def test_predict_endpoint_no_training_data(tmp, monkeypatch):
    monkeypatch.setenv("KEIBA_STORAGE_URI", str(tmp / "empty"))
    store = LocalStorage(tmp / "empty")
    _seed_derby(store, "202605021211", seed=99)  # 対象のみ、過去ダービー無し
    from keiba.web import app as webmod
    # 学習候補に存在しない race_id を指定 → 読み込めず空に
    r = webmod.app.test_client().get("/predict/202605021211?train=209905021211")
    assert r.status_code == 422
    assert "skipped" in r.get_json()


def test_genai_predict_disabled_without_key(tmp, monkeypatch):
    monkeypatch.setenv("KEIBA_STORAGE_URI", str(tmp / "g"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from keiba.web import app as webmod
    r = webmod.app.test_client().get("/genai-predict/202605021211")
    assert r.status_code == 503


def test_genai_predict_endpoint(tmp, monkeypatch):
    """/genai-predict: 生成AIの2段フローを JSON で返す（anthropic をモック）。"""
    monkeypatch.setenv("KEIBA_STORAGE_URI", str(tmp / "g2"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
    store = LocalStorage(tmp / "g2")
    for i, rid in enumerate(["202105021211", "202205021211"]):
        _seed_derby(store, rid, seed=20 + i)
    _seed_derby(store, "202605021211", seed=99, race_name="第93回東京優駿(GI)")

    # genai の2段呼び出しをモック（API を叩かない）
    from keiba import genai
    from test_genai import _sample_insights, _sample_prediction
    monkeypatch.setattr(genai, "derive_insights",
                        lambda *a, **k: _sample_insights())
    monkeypatch.setattr(genai, "apply_insights",
                        lambda *a, **k: _sample_prediction())
    # anthropic.Anthropic() がキー検証で失敗しないようダミー化
    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: object())

    from keiba.web import app as webmod
    r = webmod.app.test_client().get("/genai-predict/202605021211")
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["race_id"] == "202605021211"
    assert "insights" in body and "prediction" in body
    assert body["prediction"]["honmei_horse_no"] == 1


# --- 簡易テストランナー（pytest 無し環境でも動く） ------------------------

class _MonkeyPatch:
    def __init__(self):
        self._env = []
        self._attr = []

    def setenv(self, k, v):
        import os
        self._env.append((k, os.environ.get(k)))
        os.environ[k] = v

    def delenv(self, k, raising=True):
        import os
        self._env.append((k, os.environ.get(k)))
        os.environ.pop(k, None)

    def setattr(self, obj, name, val):
        had = hasattr(obj, name)
        self._attr.append((obj, name, getattr(obj, name, None), had))
        setattr(obj, name, val)

    def undo(self):
        import os
        for k, old in reversed(self._env):
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old
        for obj, name, old, had in reversed(self._attr):
            if had:
                setattr(obj, name, old)
            else:
                try:
                    delattr(obj, name)
                except AttributeError:
                    pass


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
