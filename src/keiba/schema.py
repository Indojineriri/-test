"""データモデルと列定義。

収集（collect）・分析（analyze）・可視化（visualize）・予想（predict）の各層が
同じ語彙でデータをやり取りできるよう、「正規化された列名」をここに集約する。
取得元（netkeiba / JRA-VAN / CSV）が変わっても、この正規化スキーマに合わせて
変換すれば下流のコードは一切変更不要、という設計上の要。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- races : 1 行 = 1 レース -------------------------------------------------
RACE_COLUMNS = [
    "race_id",     # レースID（netkeiba の race_id をそのまま使う）
    "date",        # 開催日 (YYYY-MM-DD)
    "race_name",   # レース名
    "track",       # 競馬場 (例: 東京)
    "surface",     # 芝 / ダート / 障害
    "distance",    # 距離 (m)
    "direction",   # 右 / 左 / 直線
    "going",       # 馬場状態 (良/稍重/重/不良)
    "weather",     # 天候
    "grade",       # G1/G2/G3/OP/条件 等
    "n_horses",    # 出走頭数
]

# --- results : 1 行 = 完了レース × 出走馬（着順あり） ------------------------
RESULT_COLUMNS = [
    "race_id",
    "horse_id",
    "horse_name",
    "finish_pos",    # 着順（取消・中止は NaN）
    "frame_no",      # 枠番
    "horse_no",      # 馬番
    "sex",           # 性別 (牡/牝/セ)
    "age",           # 馬齢
    "impost",        # 斤量 (kg)
    "jockey",        # 騎手
    "jockey_id",     # 騎手ID
    "time_sec",      # 走破タイム (秒)
    "margin",        # 着差
    "passing",       # 通過順 (例: 5-5-3-2)
    "last_3f",       # 上がり 3F (秒)
    "odds",          # 単勝オッズ
    "popularity",    # 人気
    "horse_weight",  # 馬体重 (kg)
    "weight_diff",   # 馬体重増減 (kg)
    "trainer",       # 調教師
    "trainer_id",    # 調教師ID
    "prize",         # 獲得賞金 (万円)
]

# --- entries : 1 行 = 対象（未来）レース × 出走馬（着順なし） ----------------
ENTRY_COLUMNS = [
    "race_id",
    "horse_id",
    "horse_name",
    "frame_no",
    "horse_no",
    "sex",
    "age",
    "impost",
    "jockey",
    "jockey_id",
    "trainer",
    "odds",        # 想定/前日オッズ（無ければ NaN）
    "popularity",  # 想定人気（無ければ NaN）
    "horse_weight",
]

# --- horses : 1 行 = 1 頭（血統マスタ） -------------------------------------
HORSE_COLUMNS = [
    "horse_id",
    "horse_name",
    "sex",
    "birth_year",
    "sire",       # 父
    "dam",        # 母
    "dam_sire",   # 母父
    "trainer",
]

# --- runs : results と entries を縦に積んだ統合テーブル ----------------------
# 粒度(grain) = 1 行 = 1 頭が 1 レースに出走する1回（= 1 つの run）。
# results(過去) と entries(未来) は「同じ run のライフサイクル違い」なので統合する。
# 詳細は keiba/docs/data-model.md を参照。
RUN_COLUMNS = [
    # --- 識別・時間 ---
    "race_id",
    "date",          # races から結合。point-in-time の時間基準
    "horse_id",
    "horse_name",
    "is_target",     # この run が予想対象レースか（entries 由来なら True）
    # --- レース条件（races から結合） ---
    "track",
    "surface",
    "distance",
    "going",
    "grade",
    "n_horses",
    # --- 出走条件（run 時点で既知＝特徴量に使ってよい） ---
    "frame_no",
    "horse_no",
    "sex",
    "age",
    "impost",
    "jockey",
    "jockey_id",
    # --- 結果系（entries では未確定＝NaN。ラベルや実績特徴量の元） ---
    "finish_pos",
    "time_sec",
    "last_3f",
    "passing",
    "odds",
    "popularity",
    "horse_weight",
    "weight_diff",
    "prize",
]


@dataclass
class RaceContext:
    """対象レース（予想したいレース）の情報をまとめたもの。"""

    race: dict                       # races の 1 行相当
    entries: object                  # entries DataFrame（出走馬一覧）
    history: object                  # results DataFrame（出走馬の過去成績）
    races: object                    # races DataFrame（history に対応するメタ）
    horses: object = field(default=None)  # horses DataFrame（血統マスタ・任意）

    @property
    def race_id(self) -> str:
        return str(self.race["race_id"])

    @property
    def race_name(self) -> str:
        return str(self.race.get("race_name", self.race_id))
