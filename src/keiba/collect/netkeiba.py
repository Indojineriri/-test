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
        # 同走馬も集めて学習データを増やすか（重いので既定オフ）
        self.expand_co_runners = False

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
        """出走各馬の過去レース結果を集約して (races_df, results_df) を返す。

        各馬の戦績一覧ページ (/horse/result/{id}/) の戦績表を *直接* パースする。
        トップ (/horse/{id}/) は戦績表を JS 描画するため requests では取れないので、
        戦績がサーバHTMLに含まれる result ページを使う。race_id を抽出して別ページに
        飛ばないので取りこぼしが無く、その馬の全成績が 1 ページで取れる。
        """
        if horse_ids is None:
            raise ValueError("netkeiba では horse_ids（出走馬ID）の指定が必要です。")

        results_frames, race_meta = [], {}
        for hid in horse_ids:
            html = self.client.horse_result_html(str(hid))
            df = P.parse_horse_results(html, str(hid))
            if self.max_history_per_horse and len(df) > self.max_history_per_horse:
                df = df.head(self.max_history_per_horse)
            # 戦績表の行から races メタを拾う（_ 付き列）
            self._collect_race_meta(html, str(hid), race_meta)
            results_frames.append(df)

        results_df = (pd.concat(results_frames, ignore_index=True)
                      if results_frames else pd.DataFrame(columns=RESULT_COLUMNS))

        # 過去レース分析では、対象レース自身は履歴に含めない（リーク防止）
        if self.past_race and not results_df.empty:
            results_df = results_df[results_df["race_id"].astype(str) != self._target]

        # オプション: 同走馬も集めて学習データを増やす
        if self.expand_co_runners and not results_df.empty:
            self._expand_with_result_pages(results_df, race_meta)

        races_df = self._build_races_df(results_df, race_meta)
        return races_df, results_df

    def _collect_race_meta(self, horse_html: str, horse_id: str, race_meta: dict):
        """競走馬ページの戦績表から各レースのメタ（日付・距離等）を race_meta に蓄積。"""
        rows = P._horse_results_meta_rows(horse_html, horse_id)
        for r in rows:
            rid = r.get("race_id")
            if rid and rid not in race_meta:
                race_meta[rid] = r

    def _expand_with_result_pages(self, results_df, race_meta: dict):
        """各レースの結果ページを取得し、同走馬の行を results に追加（学習データ増強）。"""
        # 実装は重い（ページ数増）ので既定オフ。必要時のみ。
        pass

    @staticmethod
    def _build_races_df(results_df, race_meta: dict):
        if results_df.empty:
            return pd.DataFrame(columns=RACE_COLUMNS)
        rows = []
        for rid in results_df["race_id"].dropna().astype(str).unique():
            m = race_meta.get(rid, {})
            rows.append({
                "race_id": rid,
                "date": m.get("date"),
                "race_name": m.get("race_name"),
                "track": m.get("track"),
                "surface": m.get("surface"),
                "distance": m.get("distance"),
                "direction": None,
                "going": m.get("going"),
                "weather": None,
                "grade": m.get("grade"),
                "n_horses": m.get("n_horses"),
            })
        return pd.DataFrame(rows)[RACE_COLUMNS]

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
        """競走馬ページの戦績表から、過去レースの race_id を新しい順で抽出。

        netkeiba は race_id を複数の URL 形式で出す:
          - https://db.netkeiba.com/race/202405021211/        … パス形式
          - https://race.netkeiba.com/race/result.html?race_id=202405021211 … クエリ形式
          - /race/result.html?race_id=...&rf=...               … 相対+追加パラメータ
        いずれも race_id(11〜12桁) を取りこぼさないよう、複数パターンで拾う。
        """
        soup = BeautifulSoup(html, "lxml")
        return _extract_race_ids_from_links(soup)


# race_id を含みうる URL から 11〜12 桁の数字を抽出する複合パターン。
# 競馬の race_id は 12 桁が基本だが、地方・特殊開催で 11 桁等もありうるので緩めに。
_RACE_ID_PATTERNS = [
    re.compile(r"/race/(\d{11,12})\b"),            # /race/202405021211/
    re.compile(r"[?&]race_id=(\d{11,12})\b"),      # ?race_id=202405021211
]


def _extract_race_ids_from_links(soup) -> list[str]:
    """ページ内の全 <a href> から race_id を新しい順（出現順）で重複なく抽出。"""
    ids, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/race/" not in href and "race_id=" not in href:
            continue
        for pat in _RACE_ID_PATTERNS:
            m = pat.search(href)
            if m:
                rid = m.group(1)
                if rid not in seen:
                    seen.add(rid)
                    ids.append(rid)
                break
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
