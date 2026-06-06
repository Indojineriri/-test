"""レース横断のメタ情報レジストリ（ダービー以外にも対応）。

このツールは当初ダービー専用だったが、安田記念など任意のレースを
「同じレースの過去開催で学習 → 今年を予想」する形に一般化するためのレジストリ。

中核は2つ:
  - normalize_race_name(): 「第76回安田記念(GI)」→「安田記念」のように、回次・グレード・
    括弧書きを落とした“レース名の核”を返す。同一レースの年度違いを名前で束ねるのに使う。
  - KNOWN_RACES: 既知レースの race_id → 施行日/勝ち馬（任意）。出馬表ページに日付が
    無いときの補完と、取得データが正しいレースかの検証に使う。winner は照合用で、
    不明なら None（実際の結果は actual.csv＝スクレイピングで持つ）。

ダービーの既知表は data/derby.py（DERBY_RACES）にあり、ここから取り込む。
"""

from __future__ import annotations

import re

from .derby import DERBY_RACES

# --- 安田記念（東京・芝1600m・GI。race_id 体系: 年 + 05030211） --------------
# 日付は概ね6月第1日曜。winner は分かる範囲で記入（照合用）。
YASUDA_KINEN = {
    "201805030211": {"year": 2018, "winner": "モズアスコット", "date": "2018-06-03"},
    "201905030211": {"year": 2019, "winner": "インディチャンプ", "date": "2019-06-02"},
    "202005030211": {"year": 2020, "winner": "グランアレグリア", "date": "2020-06-07"},
    "202105030211": {"year": 2021, "winner": "ダノンキングリー", "date": "2021-06-06"},
    "202205030211": {"year": 2022, "winner": "ソングライン", "date": "2022-06-05"},
    "202305030211": {"year": 2023, "winner": "ソングライン", "date": "2023-06-04"},
    "202405030211": {"year": 2024, "winner": "ロマンチックウォリアー", "date": "2024-06-02"},
    "202505030211": {"year": 2025, "winner": None, "date": "2025-06-08"},
    "202605030211": {"year": 2026, "winner": None, "date": "2026-06-07"},
}

# race_id -> {year, winner, date}。複数レース分を統合した既知表。
KNOWN_RACES: dict[str, dict] = {}
KNOWN_RACES.update({rid: dict(info, race_key="東京優駿")
                    for rid, info in DERBY_RACES.items()})
KNOWN_RACES.update({rid: dict(info, race_key="安田記念")
                    for rid, info in YASUDA_KINEN.items()})


# 正規化のときに落とす装飾（グレード表記・回次・括弧書き・空白）
_GRADE_RE = re.compile(r"[\(（]?\s*G\s*[ⅠⅡⅢIVX0-9]{1,3}\s*[\)）]?", re.IGNORECASE)
_KAI_RE = re.compile(r"第?\s*\d+\s*回")
_PAREN_RE = re.compile(r"[\(（][^\)）]*[\)）]")
_SPACE_RE = re.compile(r"\s+")


def normalize_race_name(name) -> str:
    """レース名の“核”を返す。回次・グレード・括弧書き・空白を除去する。

    例: 「第76回 安田記念(GⅠ)」→「安田記念」
        「東京優駿(日本ダービー)」→「東京優駿」（括弧書きは落とす）
    同一レースの年度違いを束ねる（学習対象の自動選択）ために使う。
    """
    if name is None:
        return ""
    s = str(name)
    s = _GRADE_RE.sub("", s)
    s = _KAI_RE.sub("", s)
    s = _PAREN_RE.sub("", s)
    s = _SPACE_RE.sub("", s)
    return s.strip()


def known_race_date(race_id: str) -> str | None:
    """既知レースの施行日（YYYY-MM-DD）。未知なら None。"""
    info = KNOWN_RACES.get(str(race_id))
    return info.get("date") if info else None


def known_race_winner(race_id: str) -> str | None:
    """既知レースの勝ち馬（照合用）。未知なら None。"""
    info = KNOWN_RACES.get(str(race_id))
    return info.get("winner") if info else None


def registry_family_ids(race_id: str) -> list[str]:
    """同じレース（race_key が同じ）の既知 race_id を返す（自身も含む）。

    まだ取得していない年も含めて「同レースの候補」を示すのに使う。
    """
    info = KNOWN_RACES.get(str(race_id))
    if not info:
        return []
    key = info.get("race_key")
    return [rid for rid, i in KNOWN_RACES.items() if i.get("race_key") == key]
