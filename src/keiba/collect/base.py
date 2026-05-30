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
          2. 出走各馬が走った過去レースを取得 ………………………… ②本体
          3. 出走馬の血統マスタ（horses）を取得

        重要: history には取得した過去レースの **全出走馬（同走馬を含む）** を残す。
        同走馬の行も「着順つきの run」であり、ML の学習サンプルになるため捨てない。
        （出走馬18頭のキャリアを辿ると、同走馬込みで数千 run が貯まる。これが
        「1レースを面のデータセットに広げる」収集の本質。詳細 docs/data-model.md）
        対象18頭の特徴量はこの history の部分集合（各馬の自分のキャリア）から作られる。
        """
        race_id = race_id or self.target_race_id()
        race_meta, entries = self.get_race_entries(race_id)

        entrant_ids = entries["horse_id"].astype(str).tolist()
        # get_past_races は「entrant が走ったレース」を集める。各レースには同走馬も
        # 載っているので、results には entrant 以外の馬の行も含まれる（=学習データ）。
        races, results = self.get_past_races(horse_ids=entrant_ids)

        horses = self.get_horses(entrant_ids)

        return RaceContext(
            race=race_meta,
            entries=entries.reset_index(drop=True),
            history=results.reset_index(drop=True),   # 全出走馬を保持（同走馬含む）
            races=races.reset_index(drop=True),
            horses=horses.reset_index(drop=True) if horses is not None else None,
        )
