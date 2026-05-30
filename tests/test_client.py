"""NetkeibaClient の HTTP 挙動を、フェイク session で検証（ネットワーク不要）。

実際に netkeiba へは行かず、requests.Session を差し替えたフェイクで
ステータス別の振る舞い（200/403/429/5xx）・プロキシ・到達性チェック・
キャッシュ・レート制限を確認する。

    python3 tests/test_client.py
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from keiba.storage import LocalStorage  # noqa: E402
from keiba.collect.netkeiba_client import (  # noqa: E402
    NetkeibaClient, AccessBlockedError, FetchError, DEFAULT_HEADERS,
)


class FakeResp:
    def __init__(self, status, text="<html>ok</html>", headers=None):
        self.status_code = status
        self.text = text
        self.content = text.encode("utf-8")
        self.headers = headers or {}
        self.encoding = None
        self.apparent_encoding = "utf-8"


class FakeSession:
    """指定したステータス列を順番に返すフェイク session。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.headers = {}
        self.calls = []  # (url, proxies) を記録

    def get(self, url, timeout=None, proxies=None):
        self.calls.append((url, proxies))
        r = self._responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _client(tmp, responses, **kw):
    c = NetkeibaClient(cache=LocalStorage(tmp), use_cache=False, wait=0, **kw)
    c._session = FakeSession(responses)  # session を直接差し替え
    return c


# --- 200 正常系 -------------------------------------------------------------

def test_success_returns_text(tmp):
    c = _client(tmp, [FakeResp(200, "<html>レース</html>")])
    assert c.race_result_html("202605021211") == "<html>レース</html>"


def test_db_host_uses_euc_jp(tmp):
    # db.netkeiba.com は euc-jp 指定になる
    c = _client(tmp, [FakeResp(200)])
    c.race_result_html("202605021211")
    # _guess_encoding が euc-jp を返すことを直接確認
    assert NetkeibaClient._guess_encoding("https://db.netkeiba.com/race/x/", FakeResp(200)) == "euc-jp"
    assert NetkeibaClient._guess_encoding("https://race.netkeiba.com/x", FakeResp(200)) == "utf-8"


# --- 403 ブロック -----------------------------------------------------------

def test_403_raises_access_blocked_without_retry(tmp):
    c = _client(tmp, [FakeResp(403), FakeResp(200)], max_retries=4)
    try:
        c.shutuba_html("202605021211")
        assert False, "AccessBlockedError が出るべき"
    except AccessBlockedError as e:
        assert "プロキシ未設定" in str(e)
    # リトライせず 1 回で諦める（403 を投げた時点で次は呼ばれない）
    assert len(c._session.calls) == 1


def test_403_message_mentions_proxy_when_set(tmp):
    c = _client(tmp, [FakeResp(403)], proxy="http://proxy:8080")
    try:
        c.horse_html("123")
        assert False
    except AccessBlockedError as e:
        assert "プロキシ未設定" not in str(e)  # プロキシ設定済みなら注記しない


# --- 429 レート超過 ---------------------------------------------------------

def test_429_then_success_with_retry_after(tmp):
    c = _client(tmp, [FakeResp(429, headers={"Retry-After": "0"}), FakeResp(200, "ok")])
    assert c.horse_html("123") == "ok"
    assert len(c._session.calls) == 2


# --- 5xx / 接続エラーはリトライ -------------------------------------------

def test_5xx_retries_then_succeeds(tmp):
    c = _client(tmp, [FakeResp(503), FakeResp(200, "ok")], max_retries=3)
    assert c.shutuba_html("x") == "ok"
    assert len(c._session.calls) == 2


def test_connection_error_retries_then_fails(tmp):
    c = _client(tmp, [ConnectionError("boom"), ConnectionError("boom")], max_retries=2)
    try:
        c.shutuba_html("x")
        assert False
    except FetchError:
        pass
    assert len(c._session.calls) == 2


def test_404_no_retry(tmp):
    c = _client(tmp, [FakeResp(404), FakeResp(200)], max_retries=4)
    try:
        c.horse_html("x")
        assert False
    except FetchError as e:
        assert "404" in str(e)
    assert len(c._session.calls) == 1


# --- プロキシが get に渡る --------------------------------------------------

def test_proxy_passed_to_get(tmp):
    c = _client(tmp, [FakeResp(200)], proxy="http://user:pass@host:3128")
    c.race_result_html("x")
    _, proxies = c._session.calls[0]
    assert proxies == {"http": "http://user:pass@host:3128",
                       "https": "http://user:pass@host:3128"}


def test_proxy_from_env(tmp, monkeypatch):
    monkeypatch.setenv("KEIBA_PROXY", "http://envproxy:9000")
    c = NetkeibaClient(cache=LocalStorage(tmp), use_cache=False, wait=0)
    assert c.proxy == "http://envproxy:9000"


# --- ブラウザ相当ヘッダ -----------------------------------------------------

def test_browser_like_headers():
    ua = DEFAULT_HEADERS["User-Agent"]
    assert "Mozilla/5.0" in ua and "Chrome" in ua
    assert "Accept-Language" in DEFAULT_HEADERS


# --- 到達性チェック ---------------------------------------------------------

def test_check_connectivity_ok(tmp):
    c = _client(tmp, [FakeResp(200, "x" * 100)])
    r = c.check_connectivity()
    assert r["ok"] and r["status"] == 200 and r["bytes"] == 100


def test_check_connectivity_blocked(tmp):
    c = _client(tmp, [FakeResp(403)])
    r = c.check_connectivity()
    assert not r["ok"] and r["status"] == 403 and r["blocked"] is True


# --- キャッシュは再取得しない ----------------------------------------------

def test_cache_avoids_refetch(tmp):
    store = LocalStorage(tmp)
    c = NetkeibaClient(cache=store, use_cache=True, wait=0)
    c._session = FakeSession([FakeResp(200, "first")])
    assert c.race_result_html("202605021211") == "first"
    # 2 回目はキャッシュから（session は使い切っているので呼ばれたら IndexError）
    assert c.race_result_html("202605021211") == "first"
    assert len(c._session.calls) == 1


# --- レート制限のウェイト --------------------------------------------------

def test_rate_limit_waits(tmp):
    c = NetkeibaClient(cache=LocalStorage(tmp), use_cache=False, wait=0.2)
    c._session = FakeSession([FakeResp(200), FakeResp(200)])
    t0 = time.monotonic()
    c.race_result_html("a")
    c.horse_html("b")
    assert time.monotonic() - t0 >= 0.2  # 2 回目で wait 分待つ


# --- 簡易テストランナー（pytest 無し環境でも動く） ------------------------

class _MP:
    def __init__(self):
        self._env = []

    def setenv(self, k, v):
        import os
        self._env.append((k, os.environ.get(k)))
        os.environ[k] = v

    def undo(self):
        import os
        for k, old in reversed(self._env):
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old


if __name__ == "__main__":
    import tempfile

    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = 0
    for name, fn in fns:
        with tempfile.TemporaryDirectory() as d:
            mp = _MP()
            try:
                params = fn.__code__.co_varnames[:fn.__code__.co_argcount]
                kwargs = {}
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
