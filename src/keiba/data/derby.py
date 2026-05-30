"""日本ダービー（東京優駿）の既知メタ情報。

ユーザ提供の照合表を元にした、過去ダービーの race_id と勝ち馬の一覧。
取得したデータが「正しいレースか」を機械的に検証するための正解データに使う。

race_id 体系: 年(4) + 05(東京) + 02(2回) + 12(12日目) + 11(11R)。

【重要】実データ取得で判明: `年05021211` がダービーなのは **2019 年以降**。
2016〜2018 年は同 race_id が別レース（薫風ステークス等）だった＝開催日目が
当時は異なる。各 race_id の race_name に『優駿/ダービー』が含まれるかで
正しさを検証すること（analyze/predict/backtest は自動でこの検証を行う）。
2016-2018 の正しい race_id は要再調査（暫定で verified=False）。
"""

from __future__ import annotations

# race_id -> {"year", "winner", "date"}
#   winner は照合用（None は要確認）。date は施行日（間隔(日)の計算等に使う）。
#   ダービーは概ね 5 月最終日曜。date は分かる範囲で記入（未確定は None）。
# verified: race_name に「東京優駿/ダービー」が含まれることを実データで確認済みか。
# 2016-2018 は別レースだったため False（race_id 要再調査）。
DERBY_RACES = {
    "201605021211": {"year": 2016, "winner": "マカヒキ", "date": "2016-05-29", "verified": False},
    "201705021211": {"year": 2017, "winner": "レイデオロ", "date": "2017-05-28", "verified": False},
    "201805021211": {"year": 2018, "winner": "ワグネリアン", "date": "2018-05-27", "verified": False},
    "201905021211": {"year": 2019, "winner": "ロジャーバローズ", "date": "2019-05-26", "verified": True},
    "202005021211": {"year": 2020, "winner": "コントレイル", "date": "2020-05-31", "verified": True},
    "202105021211": {"year": 2021, "winner": "シャフリヤール", "date": "2021-05-30", "verified": True},
    "202205021211": {"year": 2022, "winner": "ドウデュース", "date": "2022-05-29", "verified": True},
    "202305021211": {"year": 2023, "winner": "タスティエーラ", "date": "2023-05-28", "verified": True},
    "202405021211": {"year": 2024, "winner": "ダノンデサイル", "date": "2024-05-26", "verified": True},
    "202505021211": {"year": 2025, "winner": None, "date": "2025-06-01", "verified": False},
    "202605021211": {"year": 2026, "winner": None, "date": "2026-05-31", "verified": False},
}


def derby_date(race_id: str) -> str | None:
    """race_id の施行日（YYYY-MM-DD）。未知なら None。"""
    info = DERBY_RACES.get(str(race_id))
    return info.get("date") if info else None


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
