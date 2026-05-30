"""日本ダービー（東京優駿）の既知メタ情報。

ユーザ提供の照合表を元にした、過去ダービーの race_id と勝ち馬の一覧。
取得したデータが「正しいレースか」を機械的に検証するための正解データに使う。

race_id 体系: 年(4) + 05(東京) + 02(2回) + 12(12日目) + 11(11R)。
2016〜2024 は全て `年05021211` で一致することを確認済み。
"""

from __future__ import annotations

# race_id -> {"year", "winner"}（winner は照合用。None は要確認）
DERBY_RACES = {
    "201605021211": {"year": 2016, "winner": "マカヒキ"},
    "201705021211": {"year": 2017, "winner": "レイデオロ"},
    "201805021211": {"year": 2018, "winner": "ワグネリアン"},
    "201905021211": {"year": 2019, "winner": "ロジャーバローズ"},
    "202005021211": {"year": 2020, "winner": "コントレイル"},
    "202105021211": {"year": 2021, "winner": "シャフリヤール"},
    "202205021211": {"year": 2022, "winner": "ドウデュース"},
    "202305021211": {"year": 2023, "winner": "タスティエーラ"},
    "202405021211": {"year": 2024, "winner": "ダノンデサイル"},
    "202505021211": {"year": 2025, "winner": None},  # 要確認
}


def derby_race_id(year: int) -> str:
    """年からダービーの race_id を返す（既知表優先、無ければ規則で生成）。"""
    for rid, info in DERBY_RACES.items():
        if info["year"] == year:
            return rid
    return f"{year:04d}05021211"


def derby_winner(race_id: str) -> str | None:
    """race_id の既知の勝ち馬（照合用）。未知なら None。"""
    info = DERBY_RACES.get(str(race_id))
    return info["winner"] if info else None


def is_known_derby(race_id: str) -> bool:
    return str(race_id) in DERBY_RACES
