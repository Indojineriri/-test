"""runs テーブル構築と point-in-time 特徴量の検証（ネットワーク不要）。

ここでは「出馬表(entries)を起点に、出走馬のキャリアを集めた」状況を合成データで
再現し、以下を確認する：
  - entries と results が runs に統合される（縦持ち・grain が一致）
  - point-in-time 特徴量が「基準日より前」だけを使う（リークが無い）
  - 対象レース(is_target)の18頭が推論用に正しく切り出される
  - 1レースだけ(=ダービー結果のみ)では学習データが作れないことの対比

    python3 tests/test_dataset.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from keiba.schema import RaceContext  # noqa: E402
from keiba.dataset import (  # noqa: E402
    build_runs_table, add_pointwise_features, make_training_frame,
    time_series_split, PIT_FEATURES,
)


# --- 合成 RaceContext を作る -------------------------------------------------

def _make_context(n_entrants=8, career=5, seed=0, pool_size=60):
    """現実に近い「共有の馬集団」を合成する。

    実際の競馬と同じく、1 つの馬プール(pool_size 頭)から毎レース field 頭が選ばれ、
    馬たちは互いに繰り返し対戦する。各馬には「真の強さ」があり着順に反映される。
    こうすると：
      - 対象レースの出走馬(entrants)は自分のキャリア(career 走)を持つ
      - 同走馬(co-runner)も別レースで何度も走り、自分のキャリアを蓄積する
        → 同走馬の run も“着順つきの学習サンプル”になる（base.build_context が
           history に全出走馬を残すのはこのため）

    返り値の history は base.build_context と同じく「entrant が走ったレースの
    全出走馬」を含む（同走馬を捨てない）。
    """
    rng = np.random.default_rng(seed)
    # 馬プール: 先頭 n_entrants 頭を「対象レースの出走馬」にする
    pool = [f"H{i:03d}" for i in range(pool_size)]
    entrant_ids = pool[:n_entrants]
    strength = {h: rng.normal() for h in pool}

    # 各馬の過去走数。entrant は career 固定、その他はばらつかせる
    starts = {h: (career if h in entrant_ids else int(rng.integers(2, 9))) for h in pool}
    remaining = dict(starts)

    race_rows, result_rows = [], []
    rc = 0
    base = pd.Timestamp("2023-01-01")
    # まだ走り残しのある馬からフィールドを組んでレースを生成
    while sum(remaining.values()) >= 8:
        rc += 1
        rid = f"R{rc:04d}"
        avail = [h for h in pool if remaining[h] > 0]
        field = min(len(avail), int(rng.integers(8, 16)))
        runners = list(rng.choice(avail, size=field, replace=False))
        dist = int(rng.choice([1800, 2000, 2400]))
        date = base + pd.Timedelta(days=rc * 3)
        race_rows.append({
            "race_id": rid, "date": date.strftime("%Y-%m-%d"),
            "race_name": f"race{rc}", "track": "東京", "surface": "芝",
            "distance": dist, "direction": "左", "going": "良",
            "weather": "晴", "grade": "OP", "n_horses": field,
        })
        # 強さ + ノイズ で着順を決める（強いほど上位）
        scored = sorted(runners, key=lambda h: -(strength[h] + rng.normal(0, .7)))
        for pos, hid in enumerate(scored, start=1):
            result_rows.append(_res_row(rid, hid, pos, field, dist, rng))
            remaining[hid] -= 1

    races = pd.DataFrame(race_rows)
    results = pd.DataFrame(result_rows)

    # 対象レース（ダービー）= entries（finish_pos なし）
    target_id = "DERBY2024"
    entries = pd.DataFrame({
        "race_id": target_id, "horse_id": entrant_ids,
        "horse_name": [f"馬{h}" for h in entrant_ids],
        "frame_no": [(i % 8) + 1 for i in range(n_entrants)],
        "horse_no": list(range(1, n_entrants + 1)),
        "sex": "牡", "age": 3, "impost": 57.0,
        "jockey": "騎手A", "jockey_id": "J01", "trainer": "厩舎A",
        "odds": np.nan, "popularity": np.nan, "horse_weight": 480,
    })
    race_meta = {"race_id": target_id, "date": "2024-05-26",
                 "race_name": "日本ダービー", "track": "東京", "surface": "芝",
                 "distance": 2400, "going": "良", "grade": "G1",
                 "n_horses": n_entrants}

    # base.build_context と同じ: 「entrant が走ったレース」の全出走馬を history に残す
    entrant_races = set(results[results["horse_id"].isin(entrant_ids)]["race_id"])
    hist = results[results["race_id"].isin(entrant_races)].copy()
    hist_races = races[races["race_id"].isin(entrant_races)].copy()

    return RaceContext(race=race_meta, entries=entries,
                       history=hist, races=hist_races, horses=None), strength, target_id


def _res_row(rid, hid, rank, field, dist, rng):
    # 通過順は着順の近傍にして脚質をそれらしく（先行〜差し）
    corner = int(np.clip(rank + rng.integers(-1, 2), 1, field))
    return {
        "race_id": rid, "horse_id": hid, "horse_name": f"馬{hid}",
        "finish_pos": rank, "frame_no": int(rng.integers(1, 9)),
        "horse_no": int(rng.integers(1, field + 1)), "sex": "牡", "age": 3,
        "impost": 56.0, "jockey": "J", "jockey_id": "J01",
        "time_sec": dist / 16 + rank * 0.15, "margin": "",
        "passing": f"{corner}-{corner}-{rank}",
        "last_3f": 34.0 + rank * 0.1, "odds": float(rank), "popularity": rank,
        "horse_weight": 480, "weight_diff": 0, "trainer": "T", "trainer_id": "T01",
        "prize": max(0.0, (field - rank) * 100.0),  # 上位ほど賞金が多い
    }


def _sig(x):
    return 1 / (1 + np.exp(-x))


# --- テスト ------------------------------------------------------------------

def test_runs_unifies_results_and_entries():
    ctx, _, target_id = _make_context()
    runs = build_runs_table(ctx)

    # entries の馬は is_target=True で finish_pos が NaN
    tgt = runs[runs["is_target"]]
    assert len(tgt) == len(ctx.entries)
    assert tgt["finish_pos"].isna().all()
    assert (tgt["race_id"] == target_id).all()

    # 過去結果は is_target=False で finish_pos が確定
    past = runs[~runs["is_target"]]
    assert past["finish_pos"].notna().all()

    # grain（1行=1出走）が一致：列が同じ語彙
    assert "horse_id" in runs and "race_id" in runs and "date" in runs


def test_pointwise_features_have_no_leakage():
    """各 run の特徴量が『基準日より前』だけから作られていることを検証。"""
    ctx, _, _ = _make_context(seed=1)
    runs = build_runs_table(ctx)
    feat = add_pointwise_features(runs, target_distance=2400)

    # 馬ごとに date 昇順で、pit_starts は 0,1,2,... と増える（過去の数）
    for hid, sub in feat.groupby("horse_id"):
        sub = sub.sort_values(["date", "race_id"])
        starts = sub["pit_starts"].tolist()
        assert starts == list(range(len(starts))), f"{hid}: {starts}"
        # デビュー戦(最初の run)は過去ゼロなので率系が NaN
        first = sub.iloc[0]
        assert first["pit_starts"] == 0
        assert pd.isna(first["pit_win_rate"])

    # リーク検証の核心：手計算と一致するか
    # ある馬の k 番目の run の pit_show_rate は、それ以前 k 走の (着順<=3) 率
    hid = ctx.entries["horse_id"].iloc[0]
    sub = feat[feat["horse_id"] == hid].sort_values(["date", "race_id"])
    fps = sub["finish_pos"].tolist()  # 末尾は target(NaN)
    for k in range(1, len(sub)):
        prior = [f for f in fps[:k] if pd.notna(f)]
        if prior:
            expected = np.mean([1.0 if f <= 3 else 0.0 for f in prior])
            got = sub.iloc[k]["pit_show_rate"]
            assert abs(got - expected) < 1e-9, f"leak? k={k} got={got} exp={expected}"


def test_target_horses_get_features_from_their_career():
    """対象レース18頭が、自分のキャリアから特徴量を得ているか。"""
    ctx, strength, _ = _make_context(n_entrants=8, career=6, seed=2)
    runs = build_runs_table(ctx)
    feat = add_pointwise_features(runs, target_distance=2400)

    tgt = feat[feat["is_target"]]
    # 全頭、過去のキャリア(複数走)が特徴量に乗っている（経験ゼロではない）
    assert (tgt["pit_starts"] >= 2).all()
    assert tgt["pit_show_rate"].notna().all()
    # 強い馬ほど複勝率が高い傾向（健全性チェック）
    tgt2 = tgt.assign(strength=tgt["horse_id"].map(strength))
    corr = tgt2[["pit_show_rate", "strength"]].corr().iloc[0, 1]
    assert corr > 0.3, f"強さと複勝率の相関が低い: {corr}"


def test_training_frame_split():
    ctx, _, _ = _make_context(n_entrants=8, career=5, seed=3)
    runs = build_runs_table(ctx)
    feat = add_pointwise_features(runs, target_distance=2400)
    parts = make_training_frame(feat, label="show")

    # 学習データは「対象レース以外で着順確定」の run（同走馬込みで数百行）
    assert len(parts["X_train"]) > 100, "出走馬のキャリアを辿ると学習データが貯まるはず"
    assert set(np.unique(parts["y_train"])) <= {0, 1}
    # 推論データは対象18(=8)頭ぴったり
    assert len(parts["X_infer"]) == 8
    # 特徴量カラムが契約どおり
    assert list(parts["X_train"].columns) == PIT_FEATURES


def test_one_race_only_is_not_trainable():
    """『1年分のダービー結果だけ』では学習データが作れないことを対比で示す。

    ダービーは3歳戦なので、2024年の出走馬は前年2023年のダービーには居ない（別世代）。
    つまり「去年のダービー結果」をいくら持っていても、今年の出走馬の過去走には
    1走もマッチせず、実力を表す特徴量が一切作れない。これがキャリア収集の必要性。
    """
    # 去年(2023)ダービーの結果18頭（=別世代の馬）
    past_horses = [f"OLD{i:02d}" for i in range(18)]
    only = pd.DataFrame({
        "race_id": "DERBY2023", "horse_id": past_horses,
        "horse_name": past_horses, "finish_pos": list(range(1, 19)),
        "n_horses": 18, "distance": 2400, "last_3f": 34.0,
    })
    races = pd.DataFrame([{"race_id": "DERBY2023", "date": "2023-05-28",
                           "distance": 2400, "n_horses": 18, "grade": "G1",
                           "track": "東京", "surface": "芝", "going": "良"}])
    # 今年(2024)ダービーの出走馬18頭（別世代＝過去結果に登場しない）
    new_horses = [f"NEW{i:02d}" for i in range(18)]
    entries = pd.DataFrame({
        "race_id": "DERBY2024", "horse_id": new_horses, "horse_name": new_horses,
        "frame_no": 1, "horse_no": range(1, 19), "sex": "牡", "age": 3,
        "impost": 57.0, "jockey": "J", "jockey_id": "J", "trainer": "T",
        "odds": np.nan, "popularity": np.nan, "horse_weight": 480,
    })
    meta = {"race_id": "DERBY2024", "date": "2024-05-26", "distance": 2400,
            "n_horses": 18, "grade": "G1", "track": "東京", "surface": "芝"}
    # 本来 build_context は出走馬の成績だけ history に入れるので、別世代の去年ダービーは
    # そもそも history に入らない（=空）。ここでは「ダービー結果だけ持ってきた」極端な
    # 想定として、あえて別世代の結果を history に置いてみる。
    ctx = RaceContext(race=meta, entries=entries, history=only, races=races)

    runs = build_runs_table(ctx)
    feat = add_pointwise_features(runs, target_distance=2400)
    parts = make_training_frame(feat)

    # 対象馬(2024)は過去(2023)に居ないので pit_starts=0 → 特徴量が空
    tgt = feat[feat["is_target"]]
    assert (tgt["pit_starts"] == 0).all()
    assert tgt["pit_show_rate"].isna().all(), \
        "1レースだけでは出走馬の実力を表す特徴量が作れない（過去走が無い）"
    # 学習サンプルも去年ダービー18行しか無い = 実質学習不能
    assert len(parts["X_train"]) == 18


def test_distance_and_time_features():
    """距離適性(延長/短縮)と持ちタイムの特徴量が計算される。"""
    ctx, _, _ = _make_context(n_entrants=8, career=6, seed=7)
    # 対象を芝1600m に見立てて距離増減を見る
    ctx.race["distance"] = 1600
    runs = build_runs_table(ctx)
    feat = add_pointwise_features(runs, target_distance=1600)
    for c in ["pit_dist_show_rate", "pit_best_time_dist", "pit_best_speed",
              "pit_dist_delta_last", "pit_ext_avg_finish", "pit_short_avg_finish"]:
        assert c in feat.columns, c
    tgt = feat[feat["is_target"]]
    # 履歴ありの対象馬は持ちタイム/速度が有限値になる
    assert tgt["pit_best_speed"].notna().any()
    # 過去走(1800/2000/2400)から1600へは短縮なので、距離増減は負になりやすい
    assert (tgt["pit_dist_delta_last"].dropna() <= 0).any()


def test_time_series_split_no_future_in_train():
    ctx, _, _ = _make_context(seed=5)
    runs = build_runs_table(ctx)
    feat = add_pointwise_features(runs, target_distance=2400)
    train, valid = time_series_split(feat, cutoff_date="2023-06-01")
    if len(train) and len(valid):
        assert pd.to_datetime(train["date"]).max() < pd.to_datetime("2023-06-01")
        assert pd.to_datetime(valid["date"]).min() >= pd.to_datetime("2023-06-01")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok: {fn.__name__}")
    print(f"OK: {len(fns)} tests passed")
