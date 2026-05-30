"""fetch の複数レース指定（過去5年分など）の解決ロジックを検証。

    python3 tests/test_cli_fetch.py
"""

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from keiba.cli import _resolve_race_ids, _parse_years  # noqa: E402


def test_parse_years_range():
    assert _parse_years("2021-2025") == [2021, 2022, 2023, 2024, 2025]


def test_parse_years_list():
    assert _parse_years("2021,2023,2025") == [2021, 2023, 2025]
    assert _parse_years("2021, 2022") == [2021, 2022]


def test_resolve_derby_years():
    a = SimpleNamespace(derby_years="2021-2025", race_ids=None, race_id=None)
    ids = _resolve_race_ids(a)
    assert ids == ["202105021211", "202205021211", "202305021211",
                   "202405021211", "202505021211"]


def test_resolve_race_ids_csv():
    a = SimpleNamespace(derby_years=None, race_ids="202105021211, 202205021211",
                        race_id=None)
    assert _resolve_race_ids(a) == ["202105021211", "202205021211"]


def test_resolve_single():
    a = SimpleNamespace(derby_years=None, race_ids=None, race_id="202605021211")
    assert _resolve_race_ids(a) == ["202605021211"]


def test_resolve_priority():
    """derby_years が最優先、次に race_ids、最後に race_id。"""
    a = SimpleNamespace(derby_years="2024", race_ids="X", race_id="Y")
    assert _resolve_race_ids(a) == ["202405021211"]


if __name__ == "__main__":
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for name, fn in fns:
        fn()
        print(f"  ok: {name}")
    print(f"OK: {len(fns)} tests passed")
