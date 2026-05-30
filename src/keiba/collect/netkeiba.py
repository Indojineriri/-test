"""netkeiba をデータ取得元とする DataSource 実装。

NetkeibaClient（HTTP）と netkeiba_parse（パース）を束ねて、schema.py 準拠の
DataFrame を供給する。フローは概ね次の通り:

  ②対象レースの出走表を取得 (shutuba)
    -> 出走各馬の horse_id を得る
    -> ①各馬の競走馬ページ (horse) から「過去出走レースID」を集める
    -> 各レースの結果ページ (race_result) を取得・パースして履歴を作る
    -> 各馬の血統 (horse) を抽出

注: この環境は外部アクセスが遮断されているため、ここでの実取得は動かない。
    パース部分は tests/ でオフライン検証している。実取得はネットのある手元環境で。
"""

from __future__ import annotations

import re

import pandas as pd
from bs4 import BeautifulSoup

from ..schema import HORSE_COLUMNS, RACE_COLUMNS, RESULT_COLUMNS
from .base import DataSource
from .netkeiba_client import NetkeibaClient
from . import netkeiba_parse as P


class NetkeibaDataSource(DataSource):
    def __init__(self, target_race_id: str, client: NetkeibaClient | None = None,
                 max_history_per_horse: int | None = None, past_race: bool = False):
        """
        Args:
            target_race_id: 予想対象のレースID（例: 出馬表のある未来/当該レース）
            client: NetkeibaClient（DI 可能。テストではフェイクを差し込める）
            max_history_per_horse: 1 頭あたり遡る過去レース数の上限（None で全件）
            past_race: 過去の（既に施行済みの）レースを分析対象にする場合 True。
                出馬表(shutuba)の代わりに結果ページから出走馬を取り出し、各馬の履歴から
                その対象レース自身を除外する（＝レース前時点の状態で分析できる）。
        """
        self._target = str(target_race_id)
        self.client = client or NetkeibaClient()
        self.max_history_per_horse = max_history_per_horse
        self.past_race = past_race

    # --- DataSource インターフェース ----------------------------------------

    def target_race_id(self) -> str:
        return self._target

    def get_race_entries(self, race_id: str):
        if self.past_race:
            # 過去レースは出馬表が無いことが多いので、結果ページから出走馬を作る。
            # 着順等は付くが、entries としては identity（馬・枠・馬番）だけ使う。
            html = self.client.race_result_html(race_id)
            meta, res = P.parse_race_result(html, str(race_id))
            entries = _result_to_entries(res)
            return meta, entries
        html = self.client.shutuba_html(race_id)
        return P.parse_shutuba(html, str(race_id))

    def get_past_races(self, horse_ids=None):
        """出走各馬の過去レース結果を集約して (races_df, results_df) を返す。"""
        if horse_ids is None:
            raise ValueError("netkeiba では horse_ids（出走馬ID）の指定が必要です。")

        race_ids = set()
        for hid in horse_ids:
            html = self.client.horse_html(str(hid))
            ids = self._race_ids_from_horse_page(html)
            if self.max_history_per_horse:
                ids = ids[: self.max_history_per_horse]
            race_ids.update(ids)

        # 過去レース分析では、対象レース自身は「履歴」に含めない。
        # （point-in-time 特徴量がレース当日の結果を使わないようにするため。
        #   対象レースの着順は別途 entries 側＝予想の答え合わせに使う）
        if self.past_race:
            race_ids.discard(self._target)

        races_rows, results_frames = [], []
        for rid in sorted(race_ids):
            html = self.client.race_result_html(rid)
            meta, res = P.parse_race_result(html, rid)
            races_rows.append(meta)
            results_frames.append(res)

        races_df = (pd.DataFrame(races_rows)[RACE_COLUMNS]
                    if races_rows else pd.DataFrame(columns=RACE_COLUMNS))
        results_df = (pd.concat(results_frames, ignore_index=True)
                      if results_frames else pd.DataFrame(columns=RESULT_COLUMNS))
        return races_df, results_df

    def get_horses(self, horse_ids):
        rows = []
        for hid in horse_ids:
            html = self.client.horse_html(str(hid))
            rows.append(P.parse_horse_profile(html, str(hid)))
        return (pd.DataFrame(rows)[HORSE_COLUMNS]
                if rows else pd.DataFrame(columns=HORSE_COLUMNS))

    # --- 補助 ---------------------------------------------------------------

    @staticmethod
    def _race_ids_from_horse_page(html: str) -> list[str]:
        """競走馬ページの戦績表から、過去レースの race_id を新しい順で抽出。"""
        soup = BeautifulSoup(html, "lxml")
        ids = []
        seen = set()
        for a in soup.select("a[href*='/race/']"):
            m = re.search(r"/race/(\d{10,})", a.get("href", ""))
            if m and m.group(1) not in seen:
                seen.add(m.group(1))
                ids.append(m.group(1))
        return ids


def _result_to_entries(res: pd.DataFrame) -> pd.DataFrame:
    """結果 DataFrame から entries 相当（identity 列）を作る。

    過去レース分析で、結果ページの出走馬を「出馬表」として扱うためのもの。
    着順などの結果系は entries には載せない（予想の答え合わせは別途行う）。
    """
    from ..schema import ENTRY_COLUMNS
    if res.empty:
        return pd.DataFrame(columns=ENTRY_COLUMNS)
    ent = pd.DataFrame()
    for col in ENTRY_COLUMNS:
        ent[col] = res[col] if col in res.columns else pd.NA
    return ent.reset_index(drop=True)


# --- race_id 組み立てヘルパ --------------------------------------------------
# netkeiba の race_id は 12 桁: 年(4) + 競馬場(2) + 開催回(2) + 開催日(2) + R(2)
_TRACK_CODES = {
    "札幌": "01", "函館": "02", "福島": "03", "新潟": "04", "東京": "05",
    "中山": "06", "中京": "07", "京都": "08", "阪神": "09", "小倉": "10",
}


def build_race_id(year: int, track: str, kai: int, day: int, race_no: int) -> str:
    """人間が分かる条件から netkeiba の race_id（12桁）を組み立てる。

    例: build_race_id(2024, "東京", 2, 12, 11) -> "202405021211"
        （2024年・東京・2回・12日目・11R = 日本ダービー）
    """
    code = _TRACK_CODES.get(track)
    if code is None:
        raise ValueError(f"未知の競馬場: {track}（{list(_TRACK_CODES)}）")
    return f"{year:04d}{code}{kai:02d}{day:02d}{race_no:02d}"
