"""コマンドラインエントリポイント（このフェーズは①②データ取得が中心）。

  # race_id を条件から組み立てる（オフラインで動く）
  python3 -m keiba.cli race-id --year 2024 --track 東京 --kai 2 --day 12 --race 11

  # ローカル保存した HTML をパースして確認（オフラインで動く）
  python3 -m keiba.cli parse-file --kind result --race-id 202405021211 \
      --file tests/fixtures/race_result.html

  # 合成データで runs テーブル/特徴量の作られ方を見る（オフラインで動く）
  python3 -m keiba.cli demo-dataset --entrants 18 --career 7

  # netkeiba から取得して CSV 化（※ネットワークのある環境で実行）
  python3 -m keiba.cli fetch --race-id 202405021211 --out data/derby2024
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from .collect import netkeiba_parse as P
from .collect.netkeiba import NetkeibaDataSource, build_race_id
from .collect.netkeiba_client import NetkeibaClient


def cmd_race_id(args):
    rid = build_race_id(args.year, args.track, args.kai, args.day, args.race)
    print(rid)


def cmd_parse_file(args):
    html = Path(args.file).read_text(encoding=args.encoding)
    if args.kind == "result":
        meta, df = P.parse_race_result(html, args.race_id or "UNKNOWN")
    elif args.kind == "shutuba":
        meta, df = P.parse_shutuba(html, args.race_id or "UNKNOWN")
    elif args.kind == "horse":
        prof = P.parse_horse_profile(html, args.race_id or "UNKNOWN")
        for k, v in prof.items():
            print(f"  {k}: {v}")
        return
    else:
        sys.exit(f"未知の kind: {args.kind}")

    print("--- race_meta ---")
    for k, v in meta.items():
        print(f"  {k}: {v}")
    print(f"--- rows: {len(df)} ---")
    with_cols = [c for c in ["finish_pos", "horse_no", "horse_name", "horse_id",
                             "jockey", "odds", "popularity"] if c in df.columns]
    print(df[with_cols].to_string(index=False))


def cmd_demo_dataset(args):
    """合成データで runs テーブルと point-in-time 特徴量を実体化して見せる（オフライン）。

    「出馬表(entries)が過去結果(results)と統合され、各馬のキャリアから
    リーク無しの特徴量が作られる」流れを、この環境でも確認できる。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
    from test_dataset import _make_context  # 合成 RaceContext を再利用
    from .dataset import (build_runs_table, add_pointwise_features,
                          make_training_frame)

    ctx, strength, target_id = _make_context(n_entrants=args.entrants,
                                             career=args.career, seed=args.seed)
    print(f"■ 出馬表(entries): {len(ctx.entries)} 頭 / 対象レース={target_id}")
    print(f"■ 収集した過去結果(results): {len(ctx.history)} 行 "
          f"(出走馬のキャリアを辿って集めた全出走馬=同走馬込み)")
    print(f"   ユニーク馬: {ctx.history['horse_id'].nunique()} 頭 / "
          f"ユニークレース: {ctx.history['race_id'].nunique()} 件")

    runs = build_runs_table(ctx)
    print(f"\n■ runs テーブル(results+entries 統合): {len(runs)} 行")
    print(f"   - 過去の run(is_target=False): {(~runs['is_target']).sum()} 行（着順あり=ラベル）")
    print(f"   - 対象の run(is_target=True) : {runs['is_target'].sum()} 行（着順なし=予想対象）")

    feat = add_pointwise_features(runs, target_distance=int(ctx.race["distance"]))
    cols = ["horse_id", "date", "is_target", "finish_pos", "pit_starts",
            "pit_show_rate", "pit_avg_finish_last3", "pit_dist_starts"]
    one = feat[feat["horse_id"] == ctx.entries["horse_id"].iloc[0]][cols]
    print("\n■ ある1頭の time line（特徴量は必ず“その行より前”だけから算出 = リーク無し）")
    with pd.option_context("display.max_columns", None, "display.width", 200):
        print(one.to_string(index=False))

    parts = make_training_frame(feat, label="show")
    print(f"\n■ 学習用 X_train: {parts['X_train'].shape}  / 推論用 X_infer: {parts['X_infer'].shape}")
    print(f"   複勝(3着内)ラベルの正例率: {parts['y_train'].mean():.1%}")
    print("   → 出馬表の出走馬を起点にキャリアを辿ると、1レースが数百〜数千行の学習データになる。")


def cmd_fetch(args):
    client = NetkeibaClient(cache_dir=args.cache_dir, wait=args.wait,
                            use_cache=not args.no_cache)
    src = NetkeibaDataSource(args.race_id, client=client,
                             max_history_per_horse=args.max_history)
    print(f"[fetch] 出馬表とコンテキストを構築します: race_id={args.race_id}")
    ctx = src.build_context()
    print(f"  出走 {len(ctx.entries)} 頭 / 過去レース {len(ctx.races)} 件 / "
          f"成績 {len(ctx.history)} 行")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ctx.entries.to_csv(out / "entries.csv", index=False)
    ctx.history.to_csv(out / "results.csv", index=False)
    ctx.races.to_csv(out / "races.csv", index=False)
    if ctx.horses is not None:
        ctx.horses.to_csv(out / "horses.csv", index=False)
    import json
    (out / "race_meta.json").write_text(
        json.dumps(ctx.race, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    print(f"  -> {out}/ に entries/results/races/horses.csv と race_meta.json を保存")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="keiba", description="競馬データ収集・予想ツール")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("race-id", help="条件から netkeiba race_id を組み立てる")
    pr.add_argument("--year", type=int, required=True)
    pr.add_argument("--track", required=True, help="例: 東京")
    pr.add_argument("--kai", type=int, required=True, help="開催回")
    pr.add_argument("--day", type=int, required=True, help="開催日目")
    pr.add_argument("--race", type=int, required=True, help="レース番号(R)")
    pr.set_defaults(func=cmd_race_id)

    pp = sub.add_parser("parse-file", help="ローカル HTML をパースして表示（オフライン）")
    pp.add_argument("--kind", choices=["result", "shutuba", "horse"], required=True)
    pp.add_argument("--file", required=True)
    pp.add_argument("--race-id", help="race_id / horse_id")
    pp.add_argument("--encoding", default="utf-8",
                    help="db系の生HTMLは euc-jp の場合あり")
    pp.set_defaults(func=cmd_parse_file)

    pd_ = sub.add_parser("demo-dataset",
                         help="合成データで runs/特徴量の作られ方を見る（オフライン）")
    pd_.add_argument("--entrants", type=int, default=18, help="対象レースの出走頭数")
    pd_.add_argument("--career", type=int, default=7, help="各出走馬のキャリア(過去走数)")
    pd_.add_argument("--seed", type=int, default=2)
    pd_.set_defaults(func=cmd_demo_dataset)

    pf = sub.add_parser("fetch", help="netkeiba から取得し CSV 化（要ネットワーク）")
    pf.add_argument("--race-id", required=True)
    pf.add_argument("--out", default="data/fetched")
    pf.add_argument("--cache-dir", default="data/cache")
    pf.add_argument("--wait", type=float, default=1.5, help="リクエスト間ウェイト秒")
    pf.add_argument("--max-history", type=int, default=None,
                    help="1頭あたり遡る過去レース数の上限")
    pf.add_argument("--no-cache", action="store_true")
    pf.set_defaults(func=cmd_fetch)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
