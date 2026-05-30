"""出走馬どうしの直接対決(head-to-head)集計の検証。

    python3 tests/test_h2h.py
"""

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from keiba.h2h import head_to_head, h2h_strength  # noqa: E402
from test_dataset import _make_context  # noqa: E402


def test_head_to_head_basic():
    ctx, strength, _ = _make_context(n_entrants=8, career=6, seed=3)
    h = head_to_head(ctx)
    assert len(h["horses"]) == 8
    assert len(h["order"]) == 8
    # 対戦ペアが取れている（同一レースで対戦している）
    assert len(h["records"]) > 0
    # 各馬の wins+losses == vs_count
    for hid, m in h["meta"].items():
        assert m["wins"] + m["losses"] == m["vs_count"]


def test_order_reflects_true_strength():
    """直接対決の序列が、合成データの真の強さとおおむね一致する。"""
    ctx, strength, _ = _make_context(n_entrants=10, career=8, seed=5)
    h = head_to_head(ctx)
    # 出走馬の真の強さ順
    ids = [hh["horse_id"] for hh in h["horses"]]
    true_top3 = set(sorted(ids, key=lambda i: -strength[i])[:3])
    pred_top3 = set(h["order"][:3])
    assert len(true_top3 & pred_top3) >= 2, "序列上位が真の強さと食い違いすぎ"


def test_h2h_strength_range_and_neutral():
    ctx, _, _ = _make_context(n_entrants=6, career=6, seed=7)
    s = h2h_strength(ctx)
    assert all(0.0 <= v <= 1.0 for v in s.values())
    # 対戦の無い馬がいれば 0.5（中立）になる設計
    # （ここでは全馬対戦ありの可能性が高いので範囲チェックのみ）


def test_matrix_shape():
    ctx, _, _ = _make_context(n_entrants=5, career=6, seed=1)
    h = head_to_head(ctx)
    mat = h["matrix"]
    assert mat.shape == (5, 5)
    # 対角は "—"
    for i in mat.index:
        assert mat.loc[i, i] == "—"


if __name__ == "__main__":
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for name, fn in fns:
        fn()
        print(f"  ok: {name}")
    print(f"OK: {len(fns)} tests passed")
