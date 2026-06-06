"""runs テーブルの構築と point-in-time 特徴量の生成。

ここが「出馬表(entries)」と「過去結果(results)」を 1 本の縦長テーブル(runs)に
統合し、各 run について **その run の日付より前の情報だけ** から特徴量を作る、
本ツールのデータ設計の中核。設計の背景は keiba/docs/data-model.md を参照。

なぜ重要か:
  - entries は「結果未確定の results」。同じ grain なので縦に積める。
  - 各 run の特徴量を「その馬の全成績」から作ると未来情報のリーク。
    必ず「基準日(run の date)より前」だけを使う = point-in-time。
  - 学習(results)と推論(entries)で同一の特徴量生成器を通し、train/serve skew を防ぐ。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .schema import RUN_COLUMNS, RaceContext

# グレードを数値化（過去最高格などの集計に使う）。大きいほど格上。
_GRADE_RANK = {"G1": 5, "G2": 4, "G3": 3, "OP": 2, "L": 2}


def _grade_to_rank(g) -> float:
    if not isinstance(g, str):
        return 1.0
    for key, val in _GRADE_RANK.items():
        if key in g:
            return float(val)
    return 1.0


def first_corner_pos(passing) -> float:
    """通過順 '5-5-3-2' の最初の数字（1コーナーの位置取り）を返す。"""
    if not isinstance(passing, str) or not passing.strip():
        return np.nan
    head = passing.replace(" ", "").split("-")[0]
    try:
        return float(head)
    except ValueError:
        return np.nan


def position_ratio(passing, n_horses) -> float:
    """1コーナー位置を頭数で割った相対位置（0=先頭〜1=最後方）。

    頭数が違うレースを比較できるよう正規化する。脚質の指標になる。
    """
    p = first_corner_pos(passing)
    if np.isnan(p) or not n_horses or pd.isna(n_horses) or float(n_horses) <= 1:
        return np.nan
    return (p - 1.0) / (float(n_horses) - 1.0)


# 脚質ラベル（相対位置の平均から分類）
def running_style_label(avg_ratio) -> str:
    """平均相対位置から脚質を日本語ラベルにする。"""
    if avg_ratio is None or (isinstance(avg_ratio, float) and np.isnan(avg_ratio)):
        return "不明"
    if avg_ratio <= 0.20:
        return "逃げ"
    if avg_ratio <= 0.45:
        return "先行"
    if avg_ratio <= 0.70:
        return "差し"
    return "追込"


# ---------------------------------------------------------------------------
# 1) runs テーブルの構築（results + entries を縦持ち）
# ---------------------------------------------------------------------------

def build_runs_table(ctx: RaceContext) -> pd.DataFrame:
    """RaceContext から runs（results + 対象 entries の縦結合）を構築する。

    - results: 過去の確定済み run（is_target=False, finish_pos あり）
    - entries: 対象レースの未確定 run（is_target=True, finish_pos=NaN）
    両者にレース条件(races)と対象レースのメタを date 付きで結合する。
    """
    races = ctx.races.copy()
    race_meta_cols = ["race_id", "date", "track", "surface", "distance",
                      "going", "grade", "n_horses"]
    for c in race_meta_cols:
        if c not in races.columns:
            races[c] = np.nan
    races_idx = races[race_meta_cols].drop_duplicates("race_id").set_index("race_id")

    # 過去結果 → run 行
    past = ctx.history.copy()
    past["is_target"] = False
    past = _attach_race_meta(past, races_idx)

    # 対象レース(entries) → run 行
    ent = ctx.entries.copy()
    ent["is_target"] = True
    # entries のレースメタは ctx.race（対象レースのメタ dict）から付与
    rm = ctx.race
    for c in ["date", "track", "surface", "distance", "going", "grade", "n_horses"]:
        ent[c] = rm.get(c, np.nan)
    # 結果系は未確定
    for c in ["finish_pos", "time_sec", "last_3f", "passing", "weight_diff"]:
        if c not in ent.columns:
            ent[c] = np.nan

    runs = pd.concat([past, ent], ignore_index=True, sort=False)
    runs = _finalize_runs(runs)
    return runs


def _attach_race_meta(df: pd.DataFrame, races_idx: pd.DataFrame) -> pd.DataFrame:
    join_cols = ["date", "track", "surface", "distance", "going", "grade", "n_horses"]
    # 結果テーブルに既に持っている列は上書きしない（馬体重増減など）
    for c in join_cols:
        mapped = df["race_id"].astype(str).map(
            races_idx[c] if c in races_idx.columns else {})
        if c in df.columns:
            df[c] = df[c].where(df[c].notna(), mapped)
        else:
            df[c] = mapped
    return df


def _finalize_runs(runs: pd.DataFrame) -> pd.DataFrame:
    for c in RUN_COLUMNS:
        if c not in runs.columns:
            runs[c] = np.nan
    runs = runs[RUN_COLUMNS].copy()
    runs["date"] = pd.to_datetime(runs["date"], errors="coerce")
    # 並び順: 馬ごとに時系列。point-in-time 計算の前提。
    runs = runs.sort_values(["horse_id", "date", "race_id"]).reset_index(drop=True)
    return runs


# ---------------------------------------------------------------------------
# 2) point-in-time 特徴量（基準日より前だけを使う）
# ---------------------------------------------------------------------------

# 生成される特徴量カラム（下流の学習/推論が参照する契約）
PIT_FEATURES = [
    "pit_starts",
    "pit_win_rate",
    "pit_place_rate",
    "pit_show_rate",
    "pit_avg_finish",
    "pit_avg_finish_last3",
    "pit_best_last3f",
    "pit_avg_last3f",
    "pit_days_since_last",
    "pit_dist_starts",
    "pit_dist_avg_finish",
    "pit_dist_show_rate",      # 同距離帯での複勝率（距離適性）
    "pit_best_time_dist",      # 持ちタイム＝対象距離ぴったりの最速タイム(秒, 小=速)
    "pit_best_speed",          # 全過去走の最高平均速度(m/s, 大=速。距離をまたいで比較)
    "pit_dist_delta_last",     # 目標距離−前走距離(+延長/−短縮)
    "pit_ext_avg_finish",      # 過去に距離延長した時の平均着順
    "pit_short_avg_finish",    # 過去に距離短縮した時の平均着順
    "pit_max_grade_win",
    "pit_avg_field_ratio",
    "pit_total_prize",        # 累計獲得賞金（万円）
    "pit_avg_corner_pos",     # 平均1コーナー位置（生の順位）
    "pit_avg_position_ratio", # 平均相対位置（0=前〜1=後。脚質の数値）
]

# 脚質ラベルは数値特徴とは別に付与する補助列（学習には position_ratio を使う）
RUNNING_STYLE_COL = "pit_running_style"


def add_pointwise_features(runs: pd.DataFrame, target_distance: int | None = None,
                           dist_tolerance: int = 300) -> pd.DataFrame:
    """各 run に、その run の date より前だけを使った特徴量を付与して返す。

    Args:
        runs: build_runs_table() の出力
        target_distance: 距離適性を測る基準距離。None なら各 run 自身の距離を使う。
        dist_tolerance: 「同距離帯」とみなす許容差(m)
    """
    runs = runs.sort_values(["horse_id", "date", "race_id"]).reset_index(drop=True)
    feats = {col: np.full(len(runs), np.nan) for col in PIT_FEATURES}

    for hid, idx in runs.groupby("horse_id", sort=False).groups.items():
        idx = list(idx)
        sub = runs.loc[idx]
        # 各 run i について、「同じ馬の i より前の run」だけを集計
        prior_fp = []        # 過去の着順
        prior_last3f = []    # 過去の上がり3F
        prior_dates = []     # 過去のレース日
        prior_dist = []      # 過去の距離
        prior_time = []      # 過去の走破タイム(秒)
        prior_field_ratio = []
        prior_grade_wins = []  # 勝ったレースの格ランク
        prior_prize = []       # 過去の獲得賞金
        prior_corner = []      # 過去の1コーナー位置（生）
        prior_pos_ratio = []   # 過去の相対位置（脚質）
        for pos, ridx in enumerate(idx):
            row = runs.loc[ridx]
            base_dist = target_distance if target_distance is not None else row["distance"]

            n = len(prior_fp)
            feats["pit_starts"][ridx] = n
            if n > 0:
                fp = np.array(prior_fp, dtype=float)
                valid = ~np.isnan(fp)
                if valid.any():
                    fpv = fp[valid]
                    feats["pit_win_rate"][ridx] = float((fpv == 1).mean())
                    feats["pit_place_rate"][ridx] = float((fpv <= 2).mean())
                    feats["pit_show_rate"][ridx] = float((fpv <= 3).mean())
                    feats["pit_avg_finish"][ridx] = float(fpv.mean())
                    feats["pit_avg_finish_last3"][ridx] = float(fpv[-3:].mean())
                l3 = np.array(prior_last3f, dtype=float)
                if (~np.isnan(l3)).any():
                    feats["pit_best_last3f"][ridx] = float(np.nanmin(l3))
                    feats["pit_avg_last3f"][ridx] = float(np.nanmean(l3))
                if prior_dates and pd.notna(row["date"]):
                    last_date = prior_dates[-1]
                    if pd.notna(last_date):
                        feats["pit_days_since_last"][ridx] = float(
                            (row["date"] - last_date).days)
                # 距離適性（基準距離 ±tolerance の過去走）
                time_arr = np.array(prior_time, dtype=float)
                if pd.notna(base_dist):
                    dist_arr = np.array(prior_dist, dtype=float)
                    near = np.abs(dist_arr - float(base_dist)) <= dist_tolerance
                    feats["pit_dist_starts"][ridx] = int(near.sum())
                    if near.any():
                        nf = fp[near]
                        if (~np.isnan(nf)).any():
                            nfv = nf[~np.isnan(nf)]
                            feats["pit_dist_avg_finish"][ridx] = float(nfv.mean())
                            # 同距離帯での複勝率（距離適性）
                            feats["pit_dist_show_rate"][ridx] = float((nfv <= 3).mean())
                    # 持ちタイム＝同一距離(ぴったり)での最速タイム。距離が違うタイムは
                    # 比較できないため距離帯ではなく厳密一致のみ採用（小さいほど速い）。
                    exact = dist_arr == float(base_dist)
                    texact = time_arr[exact]
                    texact = texact[(~np.isnan(texact)) & (texact > 0)]
                    if len(texact):
                        feats["pit_best_time_dist"][ridx] = float(texact.min())
                    # 距離増減（直近過去走との差）。+延長／−短縮。
                    last_d = dist_arr[-1]
                    if not np.isnan(last_d):
                        feats["pit_dist_delta_last"][ridx] = float(base_dist) - float(last_d)
                # 距離延長/短縮の歴史的成績（隣接する過去走の距離差で判定）
                if len(prior_dist) >= 2:
                    d_arr = np.array(prior_dist, dtype=float)
                    deltas = np.diff(d_arr)          # runs[1:] に対応
                    fp_next = fp[1:]
                    ext = (deltas > 50) & (~np.isnan(fp_next))
                    sht = (deltas < -50) & (~np.isnan(fp_next))
                    if ext.any():
                        feats["pit_ext_avg_finish"][ridx] = float(fp_next[ext].mean())
                    if sht.any():
                        feats["pit_short_avg_finish"][ridx] = float(fp_next[sht].mean())
                # 最高平均速度(m/s)。距離をまたいで比較できる速度指標（持ちタイムの補完）
                d_all = np.array(prior_dist, dtype=float)
                spd_mask = ((~np.isnan(time_arr)) & (time_arr > 0)
                            & (~np.isnan(d_all)) & (d_all > 0))
                if spd_mask.any():
                    feats["pit_best_speed"][ridx] = float(
                        np.max(d_all[spd_mask] / time_arr[spd_mask]))
                if prior_grade_wins:
                    feats["pit_max_grade_win"][ridx] = float(max(prior_grade_wins))
                if prior_field_ratio and not np.isnan(prior_field_ratio).all():
                    feats["pit_avg_field_ratio"][ridx] = float(np.nanmean(prior_field_ratio))
                # 累計賞金（実績の総量。NaN を 0 扱いで合算）
                feats["pit_total_prize"][ridx] = float(np.nansum(prior_prize))
                # 脚質（1コーナー位置・相対位置の平均）
                corner_arr = np.array(prior_corner, dtype=float)
                if np.any(~np.isnan(corner_arr)):
                    feats["pit_avg_corner_pos"][ridx] = float(np.nanmean(corner_arr))
                ratio_arr = np.array(prior_pos_ratio, dtype=float)
                if np.any(~np.isnan(ratio_arr)):
                    feats["pit_avg_position_ratio"][ridx] = float(np.nanmean(ratio_arr))

            # この run を「過去」に追加して次へ（＝自分自身は含めない）
            fp_i = row["finish_pos"]
            prior_fp.append(fp_i)
            prior_last3f.append(row["last_3f"])
            prior_dates.append(row["date"])
            prior_dist.append(row["distance"])
            prior_time.append(row.get("time_sec") if "time_sec" in row else np.nan)
            nh = row["n_horses"]
            if pd.notna(fp_i) and pd.notna(nh) and nh:
                prior_field_ratio.append(float(fp_i) / float(nh))
            else:
                prior_field_ratio.append(np.nan)
            if pd.notna(fp_i) and float(fp_i) == 1.0:
                prior_grade_wins.append(_grade_to_rank(row["grade"]))
            prior_prize.append(row["prize"] if "prize" in row else np.nan)
            prior_corner.append(first_corner_pos(row.get("passing")))
            prior_pos_ratio.append(position_ratio(row.get("passing"), nh))

    out = runs.copy()
    for col in PIT_FEATURES:
        out[col] = feats[col]
    # pit_starts は欠損ではなく 0 が自然
    out["pit_starts"] = out["pit_starts"].fillna(0).astype(int)
    out["pit_dist_starts"] = out["pit_dist_starts"].fillna(0).astype(int)
    # 脚質ラベル（相対位置の平均から分類）。学習には数値の position_ratio を使う。
    out[RUNNING_STYLE_COL] = out["pit_avg_position_ratio"].map(running_style_label)
    return out


# ---------------------------------------------------------------------------
# 3) 学習用 (X, y) と推論用 X の切り出し
# ---------------------------------------------------------------------------

def make_training_frame(runs_feat: pd.DataFrame, label: str = "show"):
    """point-in-time 特徴量付き runs を、学習用と推論用に分ける。

    Args:
        runs_feat: add_pointwise_features() の出力
        label: "show"(3着内) / "win"(1着) のいずれか。学習ラベルの定義。

    Returns:
        dict:
            X_train, y_train : is_target=False かつ finish_pos 確定の run
            X_infer          : is_target=True の run（対象レースの出走馬）
            infer_index      : X_infer に対応する runs_feat 上の行（馬名等の参照用）
    """
    df = runs_feat
    is_target = df["is_target"].fillna(False).astype(bool)

    train_mask = (~is_target) & df["finish_pos"].notna()
    train = df[train_mask].copy()
    infer = df[is_target].copy()

    if label == "win":
        y = (train["finish_pos"].astype(float) == 1).astype(int)
    else:  # show = 複勝(3着内)
        y = (train["finish_pos"].astype(float) <= 3).astype(int)

    return {
        "X_train": train[PIT_FEATURES].astype(float),
        "y_train": y.to_numpy(),
        "X_infer": infer[PIT_FEATURES].astype(float),
        "infer_index": infer.reset_index(drop=True),
    }


def time_series_split(runs_feat: pd.DataFrame, cutoff_date: str):
    """バックテスト用の時系列分割。

    cutoff_date より前を学習、以後を検証にする（ランダム分割は未来→過去の
    リークを生むため使わない）。
    """
    d = pd.to_datetime(runs_feat["date"], errors="coerce")
    cut = pd.to_datetime(cutoff_date)
    train = runs_feat[(d < cut) & runs_feat["finish_pos"].notna()].copy()
    valid = runs_feat[(d >= cut) & runs_feat["finish_pos"].notna()].copy()
    return train, valid
