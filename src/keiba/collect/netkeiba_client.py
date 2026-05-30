"""netkeiba への HTTP アクセスを担う薄いクライアント。

責務はネットワーク I/O のみ（パースは netkeiba_parse.py が担当）。
礼儀正しく、かつ Cloud Run のようなデータセンタ環境からでも動かしやすいよう、
以下を内蔵する:

  - リクエスト間ウェイト（既定 1.5 秒）でサーバ負荷を抑える
  - 取得済み HTML を Storage（ローカル/GCS）にキャッシュし、再取得を避ける
  - ブラウザ相当のリクエストヘッダ（UA/Accept/Referer 等）でブロックを受けにくくする
  - HTTP ステータスごとの扱い分け:
      * 403/451 … アクセス拒否（IP ブロックの可能性大）。リトライせず明確に通知。
      * 429     … レート超過。Retry-After を尊重して待つ。
      * 5xx/接続エラー … 一時障害として指数バックオフでリトライ。
  - プロキシ対応（KEIBA_PROXY / HTTPS_PROXY）。GCP の IP がブロックされる場合の回避策。
  - netkeiba のページごとの文字コード差（db 系は EUC-JP）への対応

【重要】robots.txt と利用規約を必ず確認し、私的・研究目的の範囲で、
        アクセス頻度を抑えて利用すること。

【Cloud Run での現実】
  netkeiba は anti-bot を入れており、GCP/データセンタの IP からのアクセスは
  403 で弾かれることがある（このサンドボックスでも 403 が確認されている）。
  その場合は KEIBA_PROXY に外向きプロキシ（住宅 IP 等）を設定して egress を
  そこ経由にする。詳細は deploy/keiba/README.md の「IP ブロックと対策」を参照。
"""

from __future__ import annotations

import os
import time

from ..storage import Storage

# netkeiba の URL テンプレート
URL_RACE_RESULT = "https://db.netkeiba.com/race/{race_id}/"
URL_HORSE = "https://db.netkeiba.com/horse/{horse_id}/"
URL_SHUTUBA = "https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"

# db.netkeiba.com は EUC-JP、race.netkeiba.com は UTF-8 が基本
EUC_HOSTS = ("db.netkeiba.com",)

# ブラウザ相当のヘッダ。素朴な bot UA は anti-bot に弾かれやすいので、実在の
# Chrome 相当にする。Referer は netkeiba 内からの遷移に見せるため付与。
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Referer": "https://www.netkeiba.com/",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


class AccessBlockedError(RuntimeError):
    """403/451 など、サーバにアクセスを拒否されたとき（IP ブロックの可能性）。"""


class FetchError(RuntimeError):
    """リトライしても取得できなかったとき（接続エラー/5xx など）。"""


class NetkeibaClient:
    def __init__(self, cache_dir: str = "data/cache", wait: float = 1.5,
                 max_retries: int = 4, timeout: int = 20, use_cache: bool = True,
                 cache: Storage | None = None, proxy: str | None = None):
        """
        Args:
            cache_dir: ローカルキャッシュのディレクトリ（cache 未指定時に使用）
            cache: HTML キャッシュの保存先 Storage。GCS を渡せば永続キャッシュになる。
                   未指定なら Storage.from_uri(cache_dir)。Cloud Run では gs:// を渡す。
            proxy: 外向きプロキシ URL（例 http://user:pass@host:port）。未指定なら
                   環境変数 KEIBA_PROXY → HTTPS_PROXY の順で参照。GCP IP が
                   ブロックされる場合の回避に使う。
        """
        self.cache = cache if cache is not None else Storage.from_uri(cache_dir)
        self.wait = wait
        self.max_retries = max_retries
        self.timeout = timeout
        self.use_cache = use_cache
        self.proxy = proxy or os.environ.get("KEIBA_PROXY") or os.environ.get("HTTPS_PROXY")
        self._session = None
        self._last_request_ts = 0.0

    # --- 公開メソッド: 各ページの HTML を返す（キャッシュ優先） --------------

    def race_result_html(self, race_id: str) -> str:
        return self._get_cached(URL_RACE_RESULT.format(race_id=race_id),
                                f"race_{race_id}.html")

    def shutuba_html(self, race_id: str) -> str:
        return self._get_cached(URL_SHUTUBA.format(race_id=race_id),
                                f"shutuba_{race_id}.html")

    def horse_html(self, horse_id: str) -> str:
        return self._get_cached(URL_HORSE.format(horse_id=horse_id),
                                f"horse_{horse_id}.html")

    # --- 診断: Cloud Run から netkeiba に到達できるか確かめる ----------------

    def check_connectivity(self, url: str = "https://www.netkeiba.com/") -> dict:
        """到達性チェック。例外を投げず、状況を dict で返す（/diag 用）。

        Returns 例:
            {"ok": True,  "status": 200, "via_proxy": False, "bytes": 12345}
            {"ok": False, "status": 403, "blocked": True, "reason": "..."}
        """
        try:
            import requests
        except ImportError:
            return {"ok": False, "reason": "requests 未インストール"}
        try:
            resp = self._session_obj().get(url, timeout=self.timeout,
                                           proxies=self._proxies())
            blocked = resp.status_code in (403, 451)
            return {
                "ok": resp.status_code == 200,
                "status": resp.status_code,
                "blocked": blocked,
                "via_proxy": bool(self.proxy),
                "bytes": len(resp.content),
                "reason": ("IP ブロックの可能性（403/451）" if blocked else ""),
            }
        except Exception as e:
            return {"ok": False, "via_proxy": bool(self.proxy),
                    "reason": f"{type(e).__name__}: {e}"}

    # --- 内部実装 -----------------------------------------------------------

    def _session_obj(self):
        if self._session is None:
            import requests  # 遅延 import（スクレイピングを使うときだけ必要）
            self._session = requests.Session()
            self._session.headers.update(DEFAULT_HEADERS)
        return self._session

    def _proxies(self):
        return {"http": self.proxy, "https": self.proxy} if self.proxy else None

    def _get_cached(self, url: str, cache_name: str) -> str:
        if self.use_cache:
            cached = self.cache.read_text(cache_name)
            if cached is not None:
                return cached
        html = self._fetch(url)
        self.cache.write_text(cache_name, html)  # 常に UTF-8 で保存
        return html

    def _fetch(self, url: str) -> str:
        last_err = None
        for attempt in range(self.max_retries):
            self._respect_rate_limit()
            try:
                resp = self._session_obj().get(url, timeout=self.timeout,
                                               proxies=self._proxies())
            except Exception as e:  # 接続エラー（DNS/タイムアウト等）→ リトライ
                last_err = e
                self._last_request_ts = time.monotonic()
                time.sleep(2 ** attempt)
                continue
            self._last_request_ts = time.monotonic()

            status = resp.status_code
            if status == 200:
                resp.encoding = self._guess_encoding(url, resp)
                return resp.text

            # アクセス拒否はリトライしても無駄。即座に明確なエラーを上げる。
            if status in (403, 451):
                raise AccessBlockedError(
                    f"netkeiba にアクセス拒否されました (HTTP {status}). "
                    f"Cloud Run/GCP の IP がブロックされている可能性があります。"
                    f"KEIBA_PROXY に外向きプロキシを設定してください。 URL={url}"
                    + ("" if self.proxy else "（現在プロキシ未設定）"))

            # レート超過は Retry-After を尊重して待ってからリトライ
            if status == 429:
                wait_s = self._retry_after(resp, default=2 ** attempt)
                last_err = FetchError(f"HTTP 429 (rate limited): {url}")
                time.sleep(wait_s)
                continue

            # 5xx などは一時障害としてリトライ
            if 500 <= status < 600:
                last_err = FetchError(f"HTTP {status}: {url}")
                time.sleep(2 ** attempt)
                continue

            # それ以外（404 など）はリトライ不要
            raise FetchError(f"HTTP {status}: {url}")

        raise FetchError(f"取得失敗（{self.max_retries}回試行）: {url}") from last_err

    @staticmethod
    def _retry_after(resp, default: float) -> float:
        val = resp.headers.get("Retry-After")
        if val and val.isdigit():
            return min(float(val), 60.0)  # 上限 60 秒
        return default

    def _respect_rate_limit(self):
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self.wait:
            time.sleep(self.wait - elapsed)

    @staticmethod
    def _guess_encoding(url: str, resp) -> str:
        if any(host in url for host in EUC_HOSTS):
            return "euc-jp"
        return resp.apparent_encoding or "utf-8"
