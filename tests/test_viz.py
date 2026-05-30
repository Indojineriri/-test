"""③可視化（matplotlib → PNG）の検証。ネットワーク不要。

    python3 tests/test_viz.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from keiba import viz  # noqa: E402
from test_dataset import _make_context  # noqa: E402

_PNG_SIG = bytes.fromhex("89504e470d0a1a0a")


def _ctx():
    ctx, _, _ = _make_context(n_entrants=10, career=6, seed=3)
    return ctx


def test_all_chart_kinds_return_png():
    ctx = _ctx()
    for kind in viz.CHART_KINDS:
        png = viz.render_chart(ctx, kind=kind)
        assert isinstance(png, bytes) and len(png) > 1000
        assert png.startswith(_PNG_SIG), f"{kind} は PNG ではない"


def test_unknown_kind_raises():
    try:
        viz.render_chart(_ctx(), kind="bogus")
        assert False, "未知種別は ValueError になるべき"
    except ValueError:
        pass


def test_past_result_trait_plot():
    """過去レースを指標でプロットし、3着内を色分けする（着順棒でなく傾向プロット）。"""
    import pandas as pd
    ctx, strength, _ = _make_context(n_entrants=10, career=6, seed=4)
    order = sorted(ctx.entries["horse_id"].astype(str), key=lambda h: -strength[h])
    actual = pd.DataFrame({"horse_id": order,
                           "horse_name": [f"馬{h}" for h in order],
                           "finish_pos": range(1, len(order) + 1)})
    for ind in viz.PAST_INDICATORS:
        png = viz.render_past_result(ctx, actual, indicator=ind)
        assert png.startswith(_PNG_SIG), f"{ind} が PNG でない"


def test_no_history_still_returns_png():
    """履歴が無い（新馬ばかり）出走表でも、欠損は placeholder で PNG を返す。"""
    ctx = _ctx()
    ctx.history = ctx.history.iloc[0:0]  # 過去成績ゼロにする
    png = viz.render_chart(ctx, kind="prize")
    assert png.startswith(_PNG_SIG)


def test_past_trend_grid_eight_kinds():
    """過去傾向は対象レースと同じ8種を全年まとめで描く（年別に分けない）。"""
    import pandas as pd
    assert len(viz.PAST_TREND_KINDS) == 8
    items = []
    for seed in (4, 5):
        ctx, strength, _ = _make_context(n_entrants=10, career=6, seed=seed)
        order = sorted(ctx.entries["horse_id"].astype(str),
                       key=lambda h: -strength[h])
        actual = pd.DataFrame({"horse_id": order,
                               "horse_name": [f"馬{h}" for h in order],
                               "finish_pos": range(1, len(order) + 1)})
        items.append((ctx, actual))
    for kind in viz.PAST_TREND_KINDS:
        png = viz.render_past_trend(items, kind=kind)
        assert png.startswith(_PNG_SIG), f"{kind} が PNG でない"
    # 空入力でも placeholder PNG
    assert viz.render_past_trend([], "prize").startswith(_PNG_SIG)


if __name__ == "__main__":
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for name, fn in fns:
        fn()
        print(f"  ok: {name}")
    print(f"OK: {len(fns)} tests passed")
