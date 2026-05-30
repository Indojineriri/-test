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


def _to_item(triple):
    """(ctx, actual, strength) -> (ctx, actual)"""
    ctx, actual, _ = triple
    return ctx, actual


def test_stack_training_data():
    ctxs = [_ctx_and_actual(s)[0] for s in (1, 2, 3)]
    X, y, rows = ml.stack_training_data(ctxs, label="show")
    assert len(X) > 100          # 複数レース分の過去 run が積まれる
    assert set(np.unique(y)) <= {0, 1}
    assert list(X.columns) == ml.PIT_FEATURES
    # 一意化されている（race_id+horse_id 重複なし）
    assert not rows.duplicated(subset=["race_id", "horse_id"]).any()


def test_build_target_training_data_aligns_population():
    """学習=予想で母集団を揃える方式。各レース出走馬×実着順でサンプルを作る。"""
    items = [_to_item(_ctx_and_actual(s)) for s in (1, 2, 3, 4)]
    X, y, rows = ml.build_target_training_data(items, label="show")
    # 各レース 12 頭 × 4 レース = 48 サンプル
    assert len(X) == 48
    assert set(np.unique(y)) <= {0, 1}
    # 複勝(3着内)は 12 頭中 3 頭 ≈ 25%
    assert 0.2 <= y.mean() <= 0.3
    assert list(X.columns) == ml.PIT_FEATURES


def test_predicted_prob_correlates_with_strength():
    """母集団を揃えた学習で、予測複勝確率が真の強さと正に相関する（実用上の健全性）。

    注: 15 個の特徴量は互いに強く相関する（すべて『強さ』を別の角度で表す）ため、
    個々の係数の符号は多重共線性で不安定になりうる。重要なのは個別係数ではなく
    『予測確率が実力と相関するか』なので、それを検証する。
    """
    train = [_to_item(_ctx_and_actual(s)) for s in range(10, 22)]
    X, y, _ = ml.build_target_training_data(train)
    model = ml.ShowProbModel().fit(X, y)

    probs, strengths = [], []
    for seed in range(80, 92):
        ctx, _, strength = _ctx_and_actual(seed)
        feat = ml.build_features_for_context(ctx)
        tgt = feat[feat["is_target"]].copy()
        tgt["p"] = model.predict_proba(tgt)
        for _, r in tgt.iterrows():
            probs.append(r["p"])
            strengths.append(strength[str(r["horse_id"])])
    corr = np.corrcoef(probs, strengths)[0, 1]
    assert corr > 0.4, f"予測確率と強さの相関が低い: {corr}"


def test_predict_ranks_strong_horses_high():
    """母集団を揃えた学習で、真の最強馬を平均的に上位に予想できる。"""
    train_items = [_to_item(_ctx_and_actual(s)) for s in range(10, 20)]
    X, y, _ = ml.build_target_training_data(train_items)
    model = ml.ShowProbModel().fit(X, y)

    ranks = []
    for seed in range(90, 105):
        ctx, _, strength = _ctx_and_actual(seed)
        pred = ml.predict_context(model, ctx)
        feat = ml.build_features_for_context(ctx)
        tgt = feat[feat["is_target"]][["horse_id", "horse_name"]]
        id2name = dict(zip(tgt["horse_id"].astype(str), tgt["horse_name"]))
        strongest = max(id2name, key=lambda h: strength[h])
        rank = int(pred.loc[pred["horse_name"] == id2name[strongest],
                            "pred_rank"].iloc[0])
        ranks.append(rank)
        assert pred["show_prob"].between(0, 1).all()
    assert float(np.mean(ranks)) < 5.0, f"最強馬の平均予想順位が高すぎ: {np.mean(ranks)}"


def test_backtest_runs_and_beats_random():
    train = [_to_item(_ctx_and_actual(s)) for s in (20, 21, 22, 23, 24)]
    test_items = [_to_item(_ctx_and_actual(s)) for s in (30, 31, 32)]
    result = ml.backtest(train, test_items)
    agg = result["aggregate"]
    assert agg["n_races"] == 3
    assert agg["honmei_show_rate"] >= 0.5      # ◎複勝率 ≥ 50%（ランダム25%より上）
    assert len(result["per_race"]) == 3
    assert "winner_pred_rank" in result["per_race"].columns


def test_format_prediction():
    train = [_to_item(_ctx_and_actual(s)) for s in (40, 41)]
    X, y, _ = ml.build_target_training_data(train)
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
