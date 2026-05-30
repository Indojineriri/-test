"""ML 予想（複勝確率モデル + バックテスト）のオフライン検証。

合成の複数レースで学習し、強い馬を上位に予想できるか・バックテストが回るかを確認。

    python3 tests/test_ml.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from keiba import ml  # noqa: E402
from test_dataset import _make_context  # 合成 RaceContext  # noqa: E402


def _ctx_and_actual(seed):
    """合成 RaceContext と、その対象レースの『真の強さ順』実結果を作る。"""
    ctx, strength, target = _make_context(n_entrants=12, career=6, seed=seed)
    order = sorted(ctx.entries["horse_id"], key=lambda h: -strength[h])
    actual = pd.DataFrame({"horse_id": order,
                           "finish_pos": list(range(1, len(order) + 1))})
    return ctx, actual, strength


def test_stack_training_data():
    ctxs = [_ctx_and_actual(s)[0] for s in (1, 2, 3)]
    X, y, rows = ml.stack_training_data(ctxs, label="show")
    assert len(X) > 100          # 複数レース分の過去 run が積まれる
    assert set(np.unique(y)) <= {0, 1}
    assert list(X.columns) == ml.PIT_FEATURES
    # 一意化されている（race_id+horse_id 重複なし）
    assert not rows.duplicated(subset=["race_id", "horse_id"]).any()


def test_fit_predict_ranks_strong_horses_high():
    """学習したモデルが、平均的に強い馬を上位に予想できる（ランダムより良い）。

    6 戦キャリアの合成データは強さ推定にノイズが乗るため、1 レースの厳密な
    top-5 一致ではなく、多数レースで『真の最強馬の予想順位がランダム期待値より
    上位か』を統計的に確認する。
    """
    train = [_ctx_and_actual(s)[0] for s in range(10, 18)]
    X, y, _ = ml.stack_training_data(train)
    model = ml.ShowProbModel().fit(X, y)

    ranks = []
    for seed in range(90, 105):
        ctx, _, strength = _ctx_and_actual(seed)
        pred = ml.predict_context(model, ctx)
        feat = ml.build_features_for_context(ctx)
        tgt = feat[feat["is_target"]][["horse_id", "horse_name"]]
        id2name = dict(zip(tgt["horse_id"].astype(str), tgt["horse_name"]))
        entrant_ids = list(id2name)
        # 出走馬の中で真の強さが最大の馬
        strongest = max(entrant_ids, key=lambda h: strength[h])
        strongest_name = id2name[strongest]
        rank = int(pred.loc[pred["horse_name"] == strongest_name, "pred_rank"].iloc[0])
        ranks.append(rank)
        assert pred["show_prob"].between(0, 1).all()

    # 真の最強馬の平均予想順位。ランダムなら 12 頭で約 6.5 位。明確に上位のはず。
    avg_rank = float(np.mean(ranks))
    assert avg_rank < 5.0, f"最強馬の平均予想順位が高すぎ: {avg_rank}"


def test_coef_table():
    train = [_ctx_and_actual(s)[0] for s in (5, 6)]
    X, y, _ = ml.stack_training_data(train)
    model = ml.ShowProbModel().fit(X, y)
    ct = model.coef_table()
    assert set(ct["feature"]) == set(ml.PIT_FEATURES)
    # 複勝率・勝率は複勝に正に効くはず
    pos = ct.set_index("feature")["coef"]
    assert pos["pit_show_rate"] > 0 or pos["pit_win_rate"] > 0


def test_backtest_runs_and_beats_random():
    train = [_ctx_and_actual(s)[0] for s in (20, 21, 22, 23, 24)]
    test_items = [(c, a) for c, a, _ in (_ctx_and_actual(s) for s in (30, 31, 32))]
    result = ml.backtest(train, test_items)
    agg = result["aggregate"]
    assert agg["n_races"] == 3
    # ◎の複勝率はランダム(3/12=25%)よりは高いことを期待（強さ信号があるので）
    assert agg["honmei_show_rate"] >= 0.5
    # per_race 表が出る
    assert len(result["per_race"]) == 3
    assert "winner_pred_rank" in result["per_race"].columns


def test_format_prediction():
    train = [_ctx_and_actual(s)[0] for s in (40, 41)]
    X, y, _ = ml.stack_training_data(train)
    model = ml.ShowProbModel().fit(X, y)
    ctx, _, _ = _ctx_and_actual(42)
    pred = ml.predict_context(model, ctx)
    txt = ml.format_prediction(pred, race_name="テストD")
    assert "複勝確率ランキング" in txt and "◎" in txt


if __name__ == "__main__":
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for name, fn in fns:
        fn()
        print(f"  ok: {name}")
    print(f"OK: {len(fns)} tests passed")
