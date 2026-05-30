"""データソースの抽象インターフェース。

新しい取得元を追加したいときは、このクラスを継承して 4 つのメソッドを実装する。
下流（analyze / visualize / predict）は具体的な取得元を知らず、`DataSource` 越しに
正規化済みの DataFrame（schema.py 準拠）を受け取る。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from ..schema import RaceContext


class DataSource(ABC):
    """過去レース・出走馬成績・血統を供給する取得元の共通インターフェース。"""

    @abstractmethod
    def get_past_races(self, horse_ids=None) -> tuple[pd.DataFrame, pd.DataFrame]:
        """①過去のレースデータ収集。

        Args:
            horse_ids: 指定があれば、その馬が関わった過去レースに絞ってよい
                       （②出走馬の成績収集を効率化するためのヒント）。

        Returns:
            (races_df, results_df):
                races_df   … RACE_COLUMNS 準拠の完了レース一覧
                results_df … RESULT_COLUMNS 準拠の着順付き結果
        """

    @abstractmethod
    def get_horses(self, horse_ids) -> pd.DataFrame:
        """血統マスタ（HORSE_COLUMNS 準拠）を返す。"""

    @abstractmethod
    def get_race_entries(self, race_id: str) -> tuple[dict, pd.DataFrame]:
        """対象（未来 or 任意）レースのメタ情報と出走馬一覧を返す。

        Returns:
            (race_meta, entries_df):
                race_meta  … races の 1 行相当の dict
                entries_df … ENTRY_COLUMNS 準拠の出走馬一覧
        """

    @abstractmethod
    def target_race_id(self) -> str:
        """既定で予想対象とするレースID。"""

    # --- 上記を組み合わせた便利メソッド -------------------------------------

    def build_context(self, race_id: str | None = None) -> RaceContext:
        """②出走馬の成績収集まで含めて、予想に必要な文脈を 1 つにまとめる。

        手順:
          1. 対象レースの出走表（entries）を取得 …………………… ②の対象を確定
          2. 出走各馬の過去成績（results / races）を取得 ………… ②本体
          3. 出走馬の血統マスタ（horses）を取得
        """
        race_id = race_id or self.target_race_id()
        race_meta, entries = self.get_race_entries(race_id)

        entrant_ids = entries["horse_id"].astype(str).tolist()
        races, results = self.get_past_races(horse_ids=entrant_ids)

        # 念のため出走馬の成績だけに絞る（取得元が広めに返してきても安全に）
        entrant_set = set(entrant_ids)
        hist_results = results[results["horse_id"].astype(str).isin(entrant_set)].copy()
        hist_race_ids = set(hist_results["race_id"].astype(str))
        hist_races = races[races["race_id"].astype(str).isin(hist_race_ids)].copy()

        horses = self.get_horses(entrant_ids)

        return RaceContext(
            race=race_meta,
            entries=entries.reset_index(drop=True),
            history=hist_results.reset_index(drop=True),
            races=hist_races.reset_index(drop=True),
            horses=horses.reset_index(drop=True) if horses is not None else None,
        )
