"""既知ダービー表と勝ち馬照合の検証。

    python3 tests/test_derby.py
"""

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from keiba.data.derby import (derby_race_id, derby_winner, is_known_derby,  # noqa: E402
                              DERBY_RACES)
from keiba import analyze as A  # noqa: E402


def test_derby_race_id_known():
    assert derby_race_id(2024) == "202405021211"
    assert derby_race_id(2016) == "201605021211"


def test_derby_race_id_unknown_year_falls_back_to_rule():
    # 表に無い年でも規則で生成（東京・2回・12日目・11R）
    assert derby_race_id(2030) == "203005021211"


def test_derby_winner_lookup():
    assert derby_winner("202405021211") == "ダノンデサイル"
    assert derby_winner("202005021211") == "コントレイル"
    assert derby_winner("999999999999") is None


def test_all_known_have_consistent_race_id_pattern():
    # 2016〜2024 は全て 年05021211
    for rid, info in DERBY_RACES.items():
        if info["winner"] is not None:  # 確定年のみ
            assert rid == f"{info['year']}05021211"


def test_verify_known_winner_match():
    actual = pd.DataFrame({
        "horse_name": ["ダノンデサイル", "馬B", "馬C"],
        "finish_pos": [1, 2, 3],
    })
    vw = A.verify_known_winner("202405021211", actual)
    assert vw["match"] is True
    assert vw["fetched_winner"] == "ダノンデサイル"


def test_verify_known_winner_mismatch():
    actual = pd.DataFrame({
        "horse_name": ["別の馬", "馬B"],
        "finish_pos": [1, 2],
    })
    vw = A.verify_known_winner("202405021211", actual)
    assert vw["match"] is False


def test_verify_unknown_race_returns_none():
    actual = pd.DataFrame({"horse_name": ["x"], "finish_pos": [1]})
    assert A.verify_known_winner("123456789012", actual) is None


def test_verify_pending_year_match_none():
    # 2025 は winner=None(要確認) なので match は None(判定保留)
    actual = pd.DataFrame({"horse_name": ["なにか"], "finish_pos": [1]})
    vw = A.verify_known_winner("202505021211", actual)
    assert vw is not None and vw["match"] is None


if __name__ == "__main__":
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for name, fn in fns:
        fn()
        print(f"  ok: {name}")
    print(f"OK: {len(fns)} tests passed")
