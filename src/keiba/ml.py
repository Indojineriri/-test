"""⑤予想（機械学習）。

設計の核:
  - 過去の複数ダービー（2016-2024 など）を束ねて学習データを作る。
  - 各 run の特徴量は point-in-time（その馬の前走まで）なのでリークしない。
  - ラベルは「複勝（3着内）に入ったか」の二値。scikit-learn のロジスティック回帰
    （+標準化・欠損補完）をベースラインに使う（軽量・確率が解釈しやすい）。
  - 学習は「対象レース以外の全 run」（同走馬の履歴も含む豊富なサンプル）で行い、
    予測は対象レースの出走馬に対して複勝確率を出す。
  - バックテスト: 既知の勝ち馬・実着順と突き合わせて的中率を測る。

すべて保存済み CSV（service.load_context）から計算でき、ネットワーク不要。
学習に scikit-learn を使うので requirements-keiba.txt の scikit-learn が必要。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .dataset import (build_runs_table, add_pointwise_features,
                      make_training_frame, PIT_FEATURES)
from .schema import RaceContext


class ShowProbModel:
    """複勝（3着内）確率を予測するモデル（ロジスティック回帰ベースライン）。"""

    def __init__(self, label: str = "show"):
        self.label = label
        self.pipeline = None
        self.feature_names = list(PIT_FEATURES)
        self._fitted = False

    def fit(self, X: pd.DataFrame, y: np.ndarray) -> "ShowProbModel":
        from sklearn.compose import ColumnTransformer  # noqa: F401  (将来用)
        from sklearn.impute import SimpleImputer
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler

        self.pipeline = Pipeline([
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("clf", LogisticRegression(max_iter=1000, class_weight="balanced")),
        ])
        Xv = X[self.feature_names].astype(float).values
        self.pipeline.fit(Xv, y)
        self._fitted = True
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        if not self._fitted:
            raise RuntimeError("モデル未学習です。先に fit() してください。")
        Xv = X[self.feature_names].astype(float).values
        return self.pipeline.predict_proba(Xv)[:, 1]

    def coef_table(self) -> pd.DataFrame:
        """学習した係数（どの特徴がプラス/マイナスに効くか）を返す。説明用。"""
        if not self._fitted:
            raise RuntimeError("モデル未学習です。")
        clf = self.pipeline.named_steps["clf"]
        return (pd.DataFrame({"feature": self.feature_names,
                              "coef": clf.coef_[0]})
                .sort_values("coef", ascending=False).reset_index(drop=True))


# ---------------------------------------------------------------------------
# 複数レースを束ねた学習データの構築
# ---------------------------------------------------------------------------

def build_features_for_context(ctx: RaceContext) -> pd.DataFrame:
    """1 つの RaceContext を runs + point-in-time 特徴量に変換する。"""
    runs = build_runs_table(ctx)
    target_dist = _safe_int(ctx.race.get("distance"))
    feat = add_pointwise_features(runs, target_distance=target_dist)
    feat["source_race_id"] = ctx.race_id
    return feat


def stack_training_data(contexts: list[RaceContext], label: str = "show"):
    """複数レースの「対象レース以外の run」を縦に積んで学習データ (X, y) を作る。

    ※注意: この方式は「各馬のキャリア全 run（楽な条件戦も強敵 G1 も混在）」で
    学習するため、予想したい母集団（ダービーの 18 頭）とズレる。係数が直感と逆に
    なることがある。母集団を揃えたいときは build_target_training_data を使う。
    """
    frames = []
    for ctx in contexts:
        feat = build_features_for_context(ctx)
        is_target = feat["is_target"].fillna(False).astype(bool)
        train_part = feat[(~is_target) & feat["finish_pos"].notna()].copy()
        frames.append(train_part)

    if not frames:
        return pd.DataFrame(columns=PIT_FEATURES), np.array([]), pd.DataFrame()

    allrows = pd.concat(frames, ignore_index=True)
    allrows = allrows.drop_duplicates(subset=["race_id", "horse_id"])

    fp = allrows["finish_pos"].astype(float)
    if label == "win":
        y = (fp == 1).astype(int).values
    else:
        y = (fp <= 3).astype(int).values
    X = allrows[PIT_FEATURES].astype(float)
    return X, y, allrows


def build_target_training_data(items: list[tuple[RaceContext, pd.DataFrame]],
                               label: str = "show"):
    """学習と予想の母集団を揃えた学習データを作る（推奨）。

    各過去レースについて、その出走馬（is_target の 18 頭）の **レース前時点の特徴量**
    と、そのレースでの **実際の着順**（actual.csv）を結びつける。
    こうすると「ダービーに出るレベルの馬の中で、誰が複勝するか」を学習でき、
    予想（同じくダービー出走馬）と母集団が一致する。リークはしない
    （特徴量はレース前まで、ラベルは当該レース結果）。

    Args:
        items: (ctx, actual_df) のリスト。actual_df は horse_id, finish_pos を持つ。
    Returns:
        (X, y, rows)
    """
    frames = []
    for ctx, actual in items:
        feat = build_features_for_context(ctx)
        tgt = feat[feat["is_target"].fillna(False).astype(bool)].copy()
        tgt["horse_id"] = tgt["horse_id"].astype(str)
        act = actual[["horse_id", "finish_pos"]].copy()
        act["horse_id"] = act["horse_id"].astype(str)
        act = act.rename(columns={"finish_pos": "_actual_finish"})
        merged = tgt.merge(act, on="horse_id", how="inner")
        merged["source_race_id"] = ctx.race_id
        frames.append(merged)

    if not frames:
        return pd.DataFrame(columns=PIT_FEATURES), np.array([]), pd.DataFrame()

    rows = pd.concat(frames, ignore_index=True)
    rows = rows[rows["_actual_finish"].notna()]
    fp = rows["_actual_finish"].astype(float)
    if label == "win":
        y = (fp == 1).astype(int).values
    else:
        y = (fp <= 3).astype(int).values
    X = rows[PIT_FEATURES].astype(float)
    return X, y, rows


# ---------------------------------------------------------------------------
# 予測（対象レースの出走馬へ適用）
# ---------------------------------------------------------------------------

def predict_context(model: ShowProbModel, ctx: RaceContext,
                    h2h_weight: float = 0.0) -> pd.DataFrame:
    """学習済みモデルで、対象レースの出走馬の複勝確率を出してランキングする。

    Args:
        h2h_weight: 0〜1。出走馬どうしの直接対決(同一レースでの優劣)を予想に混ぜる比率。
            0 で従来どおり。>0 にすると show_prob を
            (1-w)*モデル確率 + w*直接対決の相対強度 でブレンドする。
            ダービー出走馬は同じステップレースで対戦済みなので、その上下関係を反映できる。
    """
    feat = build_features_for_context(ctx)
    tgt = feat[feat["is_target"].fillna(False).astype(bool)].copy()
    tgt["model_prob"] = model.predict_proba(tgt)

    if h2h_weight and h2h_weight > 0:
        from .h2h import h2h_strength
        strength = h2h_strength(ctx)
        tgt["h2h_strength"] = tgt["horse_id"].astype(str).map(strength).fillna(0.5)
        w = float(h2h_weight)
        tgt["show_prob"] = (1 - w) * tgt["model_prob"] + w * tgt["h2h_strength"]
    else:
        tgt["show_prob"] = tgt["model_prob"]

    tgt = tgt.sort_values("show_prob", ascending=False).reset_index(drop=True)
    tgt["pred_rank"] = np.arange(1, len(tgt) + 1)
    cols = ["pred_rank", "horse_no", "horse_name", "show_prob", "model_prob",
            "h2h_strength", "pit_show_rate", "pit_total_prize", "pit_running_style"]
    cols = [c for c in cols if c in tgt.columns]
    return tgt[cols]


def format_prediction(pred: pd.DataFrame, race_name: str = "対象レース",
                      marks=("◎", "○", "▲", "△", "△")) -> str:
    lines = [f"=== 【⑤予想（ML）】{race_name} 複勝確率ランキング ==="]
    disp = pred.copy()
    if "show_prob" in disp:
        disp["show_prob"] = (disp["show_prob"] * 100).round(1)
    if "pit_show_rate" in disp:
        disp["pit_show_rate"] = disp["pit_show_rate"].round(1)
    if "pit_total_prize" in disp:
        disp["pit_total_prize"] = disp["pit_total_prize"].round(0)
    disp = disp.rename(columns={
        "pred_rank": "予想順", "horse_no": "馬番", "horse_name": "馬名",
        "show_prob": "複勝確率%", "pit_show_rate": "実複勝率%",
        "pit_total_prize": "総賞金(万)", "pit_running_style": "脚質"})
    lines.append(disp.fillna("-").to_string(index=False))
    # 印
    lines.append("\n--- 買い目メモ ---")
    for mark, (_, r) in zip(marks, pred.iterrows()):
        lines.append(f"  {mark} {r['horse_name']}（複勝確率 {r['show_prob']*100:.0f}%）")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# バックテスト（過去レースで的中率を測る）
# ---------------------------------------------------------------------------

def backtest(train_items: list[tuple[RaceContext, pd.DataFrame]],
             test_items: list[tuple[RaceContext, pd.DataFrame]],
             label: str = "show") -> dict:
    """train_items で学習し、各テストレースで予想 vs 実結果を評価する。

    学習は build_target_training_data（学習=予想で母集団を揃える方式）を使う。
    各過去レースの「出走馬のレース前特徴量 → そのレースの実着順」で学習するので、
    予想（同じくダービー出走馬）と母集団が一致し、係数が直感に沿う。

    Args:
        train_items: (ctx, actual_df) のリスト。学習用の過去レース。
        test_items: (ctx, actual_df) のリスト。評価用。
    Returns:
        dict: per_race（各レースの的中サマリ DataFrame）と aggregate（集計）。
    """
    X, y, _ = build_target_training_data(train_items, label=label)
    model = ShowProbModel(label=label).fit(X, y)

    rows = []
    for ctx, actual in test_items:
        pred = predict_context(model, ctx)
        act = actual[["horse_id", "finish_pos"]].copy()
        act["horse_id"] = act["horse_id"].astype(str)
        # 予想に horse_id を持たせ直す
        feat = build_features_for_context(ctx)
        tgt = feat[feat["is_target"].fillna(False).astype(bool)][
            ["horse_id", "horse_name"]].copy()
        tgt["horse_id"] = tgt["horse_id"].astype(str)
        merged = pred.merge(tgt, on="horse_name", how="left").merge(
            act, on="horse_id", how="left")

        winner_rank = merged.loc[merged["finish_pos"] == 1, "pred_rank"]
        top3_pred = set(merged.loc[merged["pred_rank"] <= 3, "horse_id"])
        top3_act = set(merged.loc[merged["finish_pos"] <= 3, "horse_id"])
        rows.append({
            "race_id": ctx.race_id,
            "race_name": ctx.race_name,
            "honmei_finish": _safe_int(merged.iloc[0]["finish_pos"]) if len(merged) else None,
            "winner_pred_rank": int(winner_rank.iloc[0]) if len(winner_rank) else None,
            "top3_hits": len(top3_pred & top3_act),
        })
    per_race = pd.DataFrame(rows)
    agg = {
        "n_races": len(per_race),
        "honmei_win_rate": float((per_race["honmei_finish"] == 1).mean())
        if len(per_race) else 0.0,
        "honmei_show_rate": float((per_race["honmei_finish"] <= 3).mean())
        if len(per_race) else 0.0,
        "avg_top3_hits": float(per_race["top3_hits"].mean()) if len(per_race) else 0.0,
    }
    return {"per_race": per_race, "aggregate": agg, "model": model}


def _safe_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None
