"""netkeiba への HTTP アクセスを担う薄いクライアント。

責務はネットワーク I/O のみ（パースは netkeiba_parse.py が担当）。
礼儀正しいスクレイピングのため、以下を内蔵する:

  - リクエスト間ウェイト（既定 1.5 秒）でサーバ負荷を抑える
  - 取得済み HTML をディスクにキャッシュし、再取得を避ける
  - 一時的なネットワークエラーに対する指数バックオフ・リトライ
  - netkeiba のページごとの文字コード差（db 系は EUC-JP）への対応
  - User-Agent の明示

【重要】robots.txt と利用規約を必ず確認し、私的・研究目的の範囲で、
        アクセス頻度を抑えて利用すること。
"""

from __future__ import annotations

import time

from ..storage import Storage

# netkeiba の URL テンプレート
URL_RACE_RESULT = "https://db.netkeiba.com/race/{race_id}/"
URL_HORSE = "https://db.netkeiba.com/horse/{horse_id}/"
URL_SHUTUBA = "https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"

# db.netkeiba.com は EUC-JP、race.netkeiba.com は UTF-8 が基本
EUC_HOSTS = ("db.netkeiba.com",)

DEFAULT_HEADERS = {
    "User-Agent": "keiba-research-tool/0.1 (personal use; +https://example.com)",
    "Accept-Language": "ja,en;q=0.8",
}


class NetkeibaClient:
    def __init__(self, cache_dir: str = "data/cache", wait: float = 1.5,
                 max_retries: int = 4, timeout: int = 20, use_cache: bool = True,
                 cache: Storage | None = None):
        """
        Args:
            cache_dir: ローカルキャッシュのディレクトリ（cache 未指定時に使用）
            cache: HTML キャッシュの保存先 Storage。GCS を渡せば永続キャッシュになる。
                   未指定なら LocalStorage(cache_dir)。Cloud Run では gs:// を渡す。
        """
        # cache 優先。無ければローカルディレクトリをキャッシュにする（従来互換）。
        self.cache = cache if cache is not None else Storage.from_uri(cache_dir)
        self.wait = wait
        self.max_retries = max_retries
        self.timeout = timeout
        self.use_cache = use_cache
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

    # --- 内部実装 -----------------------------------------------------------

    def _session_obj(self):
        if self._session is None:
            import requests  # 遅延 import（スクレイピングを使うときだけ必要）
            self._session = requests.Session()
            self._session.headers.update(DEFAULT_HEADERS)
        return self._session

    def _get_cached(self, url: str, cache_name: str) -> str:
        if self.use_cache:
            cached = self.cache.read_text(cache_name)
            if cached is not None:
                return cached
        html = self._fetch(url)
        self.cache.write_text(cache_name, html)  # 常に UTF-8 で保存
        return html

    def _fetch(self, url: str) -> str:
        self._respect_rate_limit()
        last_err = None
        for attempt in range(self.max_retries):
            try:
                resp = self._session_obj().get(url, timeout=self.timeout)
                resp.raise_for_status()
                resp.encoding = self._guess_encoding(url, resp)
                return resp.text
            except Exception as e:  # ネットワーク/HTTP エラー
                last_err = e
                sleep_s = 2 ** attempt  # 1, 2, 4, 8 秒
                time.sleep(sleep_s)
            finally:
                self._last_request_ts = time.monotonic()
        raise RuntimeError(f"取得失敗（{self.max_retries}回試行）: {url}") from last_err

    def _respect_rate_limit(self):
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self.wait:
            time.sleep(self.wait - elapsed)

    @staticmethod
    def _guess_encoding(url: str, resp) -> str:
        if any(host in url for host in EUC_HOSTS):
            return "euc-jp"
        return resp.apparent_encoding or "utf-8"
