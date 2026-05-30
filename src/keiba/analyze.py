"""④分析機能：取得データの健全性診断 + 出走馬の成績分析。

設計の考え方:
  - まず「データが分析に足りているか」を診断する（diagnose_coverage）。
    ダービー馬は3歳でキャリアが短く、有力馬は同じレースに集中するため、
    races の件数が少なく見えるのは正常。判断すべきは「各馬の過去出走数」。
  - その上で、出走各馬の成績テーブル（analyze_entrants）を作る。
    ここは dataset.py の runs + point-in-time 特徴量を土台にし、
    「対象レース時点での各馬の実力」を一覧化する（リーク無し）。

すべて取得済み CSV（service.load_context）から計算でき、ネットワーク不要。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .dataset import build_runs_table, add_pointwise_features, PIT_FEATURES
from .schema import RaceContext


# ---------------------------------------------------------------------------
# 1) データ健全性の診断
# ---------------------------------------------------------------------------

def diagnose_coverage(ctx: RaceContext) -> dict:
    """取得データが分析に十分かを診断し、サマリ dict を返す。

    返り値の主なキー:
        n_entrants            出走頭数
        n_unique_past_races   関連レース数（races.csv の行数）
        n_result_rows         成績の総行数（results.csv）
        per_horse             各出走馬の過去出走数（DataFrame）
        starts_min/median/max 各馬の過去出走数の代表値
        warnings              注意メッセージのリスト（取りこぼしの疑いなど）
    """
    entries = ctx.entries
    history = ctx.history
    races = ctx.races
    entrant_ids = set(entries["horse_id"].astype(str))

    # 各出走馬が「自分自身として」過去に何戦しているか
    own = history[history["horse_id"].astype(str).isin(entrant_ids)]
    per_horse = (own.groupby(history["horse_id"].astype(str))
                 .size().reindex(sorted(entrant_ids)).fillna(0).astype(int))
    per_horse = per_horse.rename("past_starts").rename_axis("horse_id").reset_index()
    # 馬名を添える
    name_map = (entries.drop_duplicates("horse_id")
                .set_index(entries["horse_id"].astype(str))["horse_name"].to_dict())
    per_horse["horse_name"] = per_horse["horse_id"].map(name_map)

    starts = per_horse["past_starts"]
    summary = {
        "race_id": ctx.race_id,
        "race_name": ctx.race_name,
        "n_entrants": int(len(entries)),
        "n_unique_past_races": int(len(races)),
        "n_result_rows": int(len(history)),
        "starts_min": int(starts.min()) if len(starts) else 0,
        "starts_median": float(starts.median()) if len(starts) else 0.0,
        "starts_max": int(starts.max()) if len(starts) else 0,
        "horses_with_no_history": int((starts == 0).sum()),
        "per_horse": per_horse[["horse_id", "horse_name", "past_starts"]],
        "warnings": [],
    }

    # 取りこぼし/不足の疑いを言語化
    w = summary["warnings"]
    if summary["n_entrants"] == 0:
        w.append("出馬表(entries)が空です。fetch が正しく完了したか確認してください。")
    if summary["horses_with_no_history"] > 0:
        w.append(
            f"過去成績が1走も取れていない馬が {summary['horses_with_no_history']} 頭います"
            "（新馬・取得失敗の可能性）。")
    if len(starts) and starts.median() < 3:
        w.append(
            f"過去出走数の中央値が {starts.median():.1f} と少なめです。"
            "3歳戦なら自然ですが、取りこぼしの可能性もあるので per_horse を確認してください。")
    # 関連レース数が少ないことの正常な理由を補足（取りこぼしと誤解されやすい）
    if summary["n_unique_past_races"] and summary["n_entrants"]:
        ratio = summary["n_unique_past_races"] / summary["n_entrants"]
        if ratio < 2.0:
            w.append(
                "関連レース数が出走頭数の割に少なめですが、これは正常な場合が多いです: "
                "①3歳戦でキャリアが短い ②有力馬は共同通信杯・弥生賞・皐月賞など同じ"
                "ステップレースに集中して対戦が重複する ③新馬戦は同条件でも複数に"
                "分割され同居しにくい。判断は races 件数ではなく上の per_horse（各馬の"
                "過去出走数）で行ってください。")
    if summary["n_result_rows"] and summary["n_unique_past_races"]:
        avg_field = summary["n_result_rows"] / summary["n_unique_past_races"]
        summary["avg_field_size"] = round(avg_field, 1)
        if avg_field < 5:
            w.append(
                f"1レースあたりの平均出走頭数が {avg_field:.1f} と不自然に小さいです"
                "（結果ページのパース漏れの可能性）。")
    return summary


def format_coverage(summary: dict) -> str:
    """diagnose_coverage の結果を読みやすいテキストにする。"""
    lines = []
    lines.append(f"=== データ健全性診断: {summary['race_name']} ({summary['race_id']}) ===")
    lines.append(f"出走頭数            : {summary['n_entrants']}")
    lines.append(f"関連レース数(races) : {summary['n_unique_past_races']}")
    lines.append(f"成績行数(results)   : {summary['n_result_rows']}"
                 + (f"  / 平均出走頭数 {summary['avg_field_size']}"
                    if "avg_field_size" in summary else ""))
    lines.append(f"各馬の過去出走数    : 最小 {summary['starts_min']} / "
                 f"中央 {summary['starts_median']:.1f} / 最大 {summary['starts_max']}")
    lines.append("")
    lines.append("【各出走馬の過去出走数】")
    ph = summary["per_horse"].sort_values("past_starts", ascending=False)
    for _, r in ph.iterrows():
        bar = "■" * int(r["past_starts"])
        lines.append(f"  {str(r['horse_name'] or r['horse_id']):<16} "
                     f"{int(r['past_starts']):>2}戦 {bar}")
    if summary["warnings"]:
        lines.append("")
        lines.append("【注意】")
        for msg in summary["warnings"]:
            lines.append(f"  ⚠ {msg}")
    else:
        lines.append("")
        lines.append("✅ 大きな問題は見当たりません。分析・予想に進めます。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 2) 出走馬の成績分析
# ---------------------------------------------------------------------------

# 一覧に出す、人が読んで意味の分かる指標
_ENTRANT_VIEW = [
    "horse_no", "horse_name", "pit_starts", "pit_win_rate", "pit_show_rate",
    "pit_avg_finish", "pit_avg_finish_last3", "pit_best_last3f",
    "pit_dist_starts", "pit_dist_avg_finish", "pit_max_grade_win",
    "pit_total_prize", "pit_running_style", "pit_avg_corner_pos",
    "pit_days_since_last",
]


def analyze_entrants(ctx: RaceContext) -> pd.DataFrame:
    """対象レースの出走馬ごとに、レース時点での実力指標を一覧化する。

    dataset.py の runs + point-in-time 特徴量を使うので、各馬の指標は
    「その馬の前走まで」だけから計算される（リーク無し）。
    """
    target_dist = _safe_int(ctx.race.get("distance"), default=None)
    runs = build_runs_table(ctx)
    feat = add_pointwise_features(runs, target_distance=target_dist)

    tgt = feat[feat["is_target"]].copy()
    # 表示順は馬番
    if "horse_no" in tgt:
        tgt = tgt.sort_values("horse_no")

    cols = [c for c in _ENTRANT_VIEW if c in tgt.columns]
    view = tgt[cols].reset_index(drop=True)

    # 率系は % に、着順系は丸めて見やすく
    for c in ["pit_win_rate", "pit_show_rate"]:
        if c in view:
            view[c] = (view[c] * 100).round(1)
    for c in ["pit_avg_finish", "pit_avg_finish_last3", "pit_dist_avg_finish",
              "pit_best_last3f", "pit_days_since_last", "pit_avg_corner_pos"]:
        if c in view:
            view[c] = view[c].round(1)
    if "pit_total_prize" in view:
        view["pit_total_prize"] = view["pit_total_prize"].round(0)
    return view


# 日本語の見出し（表示用）
COLUMN_LABELS = {
    "horse_no": "馬番",
    "horse_name": "馬名",
    "pit_starts": "出走数",
    "pit_win_rate": "勝率%",
    "pit_show_rate": "複勝率%",
    "pit_avg_finish": "平均着順",
    "pit_avg_finish_last3": "近3走平均",
    "pit_best_last3f": "最速上り",
    "pit_dist_starts": "同距離数",
    "pit_dist_avg_finish": "同距離平均着",
    "pit_max_grade_win": "最高勝鞍格",
    "pit_total_prize": "総賞金(万)",
    "pit_running_style": "脚質",
    "pit_avg_corner_pos": "平均1角",
    "pit_days_since_last": "間隔(日)",
}


def format_entrants(view: pd.DataFrame) -> str:
    """analyze_entrants の結果を日本語見出しのテキスト表にする。"""
    disp = view.rename(columns=COLUMN_LABELS).copy()
    # NaN は人が見て分かるよう "-" に（未経験＝該当データ無しの意味）
    disp = disp.fillna("-")
    return disp.to_string(index=False)


def horse_form(ctx: RaceContext, horse_id: str) -> pd.DataFrame:
    """1頭の戦績（時系列）を、レース条件つきで返す（深掘り用）。"""
    hid = str(horse_id)
    h = ctx.history[ctx.history["horse_id"].astype(str) == hid].copy()
    if h.empty:
        return h
    races = ctx.races.set_index(ctx.races["race_id"].astype(str))
    for col in ["date", "race_name", "distance", "surface", "going", "grade"]:
        if col in races.columns:
            h[col] = h["race_id"].astype(str).map(races[col])
    if "date" in h:
        h = h.sort_values("date")
    view_cols = [c for c in ["date", "race_name", "grade", "distance", "going",
                             "finish_pos", "horse_no", "popularity", "passing",
                             "last_3f", "time_sec", "prize"] if c in h.columns]
    return h[view_cols].reset_index(drop=True)


def verify_known_winner(race_id: str, actual: pd.DataFrame) -> dict | None:
    """取得した実結果の勝ち馬が、既知のダービー正解表と一致するか検証する。

    「正しいレースを取れているか」を機械的にチェックする。未知レースは None。

    Returns:
        {"known_winner", "fetched_winner", "match": bool} or None
    """
    from .data.derby import is_known_derby, derby_winner
    if not is_known_derby(race_id):
        return None
    known = derby_winner(race_id)
    win_row = actual[actual["finish_pos"] == 1]
    fetched = (str(win_row["horse_name"].iloc[0])
               if len(win_row) and "horse_name" in win_row else None)
    return {
        "known_winner": known,
        "fetched_winner": fetched,
        # known が None(要確認)のときは判定保留 → match=None
        "match": (None if known is None else (fetched == known)),
    }


def evaluate_past_race(ctx: RaceContext, actual: pd.DataFrame,
                       rank_by: str = "pit_show_rate") -> dict:
    """過去レースで、分析指標が実際の結果をどれだけ当てたかを評価する。

    レース前時点の point-in-time 指標で出走馬を並べ、実際の着順と突き合わせる。
    バックテスト（予想ロジックの妥当性確認）の最小版。

    Args:
        ctx: 過去レースの RaceContext（履歴に対象レース自身は含まない＝レース前状態）
        actual: 対象レースの実結果（actual.csv 由来。horse_id と finish_pos を使う）
        rank_by: 並べ替えに使う指標（既定: 複勝率。pit_total_prize 等も可）

    Returns:
        dict: 突き合わせ表(merged) と的中サマリ（◎の着順、上位3頭の的中数 など）
    """
    view = analyze_entrants(ctx)
    feat = view.copy()
    # 実着順を結合
    act = actual[["horse_id", "finish_pos"]].copy()
    act["horse_id"] = act["horse_id"].astype(str)
    # analyze_entrants は horse_id を落としているので runs から取り直す
    runs = build_runs_table(ctx)
    f2 = add_pointwise_features(runs, target_distance=_safe_int(ctx.race.get("distance")))
    tgt = f2[f2["is_target"]][["horse_id", "horse_name", "horse_no", rank_by]].copy()
    tgt["horse_id"] = tgt["horse_id"].astype(str)
    merged = tgt.merge(act, on="horse_id", how="left")

    # 指標で予想順位をつける（大きいほど上位。着順系なら別途反転が要るが
    # 既定の show_rate / prize は「大きいほど良い」のでそのまま降順）
    merged = merged.sort_values(rank_by, ascending=False).reset_index(drop=True)
    merged["pred_rank"] = np.arange(1, len(merged) + 1)

    actual_top3 = set(merged[merged["finish_pos"] <= 3]["horse_id"])
    pred_top3 = set(merged[merged["pred_rank"] <= 3]["horse_id"])
    honmei = merged.iloc[0] if len(merged) else None

    return {
        "merged": merged,
        "rank_by": rank_by,
        "honmei_horse": (honmei["horse_name"] if honmei is not None else None),
        "honmei_actual_finish": (_safe_int(honmei["finish_pos"]) if honmei is not None
                                 else None),
        "top3_hits": len(actual_top3 & pred_top3),
        "winner_predicted_rank": _winner_pred_rank(merged),
    }


def _winner_pred_rank(merged: pd.DataFrame):
    w = merged[merged["finish_pos"] == 1]
    return int(w["pred_rank"].iloc[0]) if len(w) else None


def format_evaluation(ev: dict) -> str:
    lines = []
    lines.append("=== 過去レースの答え合わせ（バックテスト最小版） ===")
    lines.append(f"並べ替え指標: {ev['rank_by']}")
    lines.append(f"◎（指標1位）: {ev['honmei_horse']} → 実際の着順 "
                 f"{ev['honmei_actual_finish']}")
    if ev["winner_predicted_rank"]:
        lines.append(f"実勝ち馬の指標順位: {ev['winner_predicted_rank']} 位")
    lines.append(f"予想上位3頭のうち実際に3着内: {ev['top3_hits']} / 3")
    lines.append("")
    m = ev["merged"]
    disp = m[["pred_rank", "horse_no", "horse_name", ev["rank_by"], "finish_pos"]].copy()
    disp = disp.rename(columns={"pred_rank": "予想順", "horse_no": "馬番",
                                "horse_name": "馬名", ev["rank_by"]: "指標",
                                "finish_pos": "実着順"}).fillna("-")
    lines.append(disp.to_string(index=False))
    return "\n".join(lines)


def _safe_int(v, default=None):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default
