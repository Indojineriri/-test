"""コマンドラインエントリポイント（このフェーズは①②データ取得が中心）。

  # race_id を条件から組み立てる（オフラインで動く）
  python3 -m keiba.cli race-id --year 2024 --track 東京 --kai 2 --day 12 --race 11

  # ローカル保存した HTML をパースして確認（オフラインで動く）
  python3 -m keiba.cli parse-file --kind result --race-id 202405021211 \
      --file tests/fixtures/race_result.html

  # 合成データで runs テーブル/特徴量の作られ方を見る（オフラインで動く）
  python3 -m keiba.cli demo-dataset --entrants 18 --career 7

  # netkeiba から取得して保存（※手元のネット回線で実行。GCS にも直接書ける）
  #   ローカルへ:  python3 -m keiba.cli fetch --race-id 202605021211 --out data/derby
  #   GCS へ直接:  python3 -m keiba.cli fetch --race-id 202605021211 \
  #                    --out gs://<bucket>/keiba
  #
  # 運用方針: 取得は手元回線で実行（netkeiba は GCP/データセンタ IP を 403 で弾く
  #           ため Cloud Run からは取得しない）。Cloud Run は GCS のデータを
  #           参照・予想・可視化する専用。詳細は deploy/keiba/README.md。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

from .collect import netkeiba_parse as P
from .collect.netkeiba import build_race_id
from .storage import Storage
from . import service


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


def cmd_diagnose_horse(args):
    """競走馬の戦績一覧ページHTMLの中身を点検する（戦績パース不能の原因特定）。

    既定では戦績一覧ページ (/horse/result/{id}/) を取得して点検する。
    /horse/{id}/ トップは戦績表を JS 描画するため requests では戦績が取れない。
    (1) HTMLサイズ、(2) ページ内の table 一覧（class・行数・ヘッダ）、
    (3) パース行数 を表示する。
    """
    import io
    import pandas as pd
    from bs4 import BeautifulSoup
    from .collect import netkeiba_parse as P
    from .collect.netkeiba_client import NetkeibaClient

    store = Storage.from_uri(args.store)
    ent_text = store.read_text(f"races/{args.race_id}/entries.csv")
    if not ent_text:
        sys.exit(f"[diagnose-horse] entries.csv が見つかりません: {args.race_id}")
    entries = pd.read_csv(io.StringIO(ent_text), dtype={"horse_id": str})

    # 戦績は /horse/result/ を取得して点検（手元回線で実取得）
    client = NetkeibaClient(cache=Storage.from_uri(store.uri("cache")),
                            wait=args.wait, proxy=args.proxy)
    targets = entries if args.all else entries.head(1)

    for _, e in targets.iterrows():
        hid = str(e["horse_id"])
        name = str(e.get("horse_name", ""))
        print(f"\n===== {name} (horse_id={hid}) =====")
        try:
            html = client.horse_result_html(hid)
        except Exception as ex:
            print(f"  ⚠ 取得失敗: {ex}")
            continue
        print(f"  URL: db.netkeiba.com/horse/result/{hid}/")
        print(f"  HTMLサイズ: {len(html):,} 文字")
        if "アクセスができません" in html[:4000] or "Forbidden" in html[:2000]:
            print("  ⚠ ブロック/エラーページの可能性")
        soup = BeautifulSoup(html, "lxml")
        title = soup.find("h1")
        print(f"  <h1>: {P._text(title)[:40] if title else '(なし)'}")

        tables = soup.find_all("table")
        print(f"  table 数: {len(tables)}")
        for i, tbl in enumerate(tables[:12]):
            cls = ".".join(tbl.get("class", []) or [])
            head = tbl.find("tr")
            htxt = re.sub(r"\s+", " ", P._text(head))[:80] if head else ""
            nrows = len(tbl.find_all("tr"))
            mark = ""
            if head and "日付" in P._text(head):
                mark = "  ← 戦績表っぽい"
            print(f"    [{i}] class='{cls}' 行数={nrows} ヘッダ='{htxt}'{mark}")

        df = P.parse_horse_results(html, hid)
        print(f"  parse_horse_results → {len(df)} 行")
        if len(df) and not args.all:
            # 取れた値を確認（脚質・賞金・上りが NaN でないか）
            cols = [c for c in ["finish_pos", "horse_no", "distance",
                                "passing", "last_3f", "prize", "popularity"]
                    if c in df.columns]
            print("  --- パース結果（各列が埋まっているか確認）---")
            with pd.option_context("display.max_columns", None, "display.width", 200):
                print(df[cols].to_string(index=False))
            na_cols = [c for c in cols if df[c].isna().all()]
            if na_cols:
                print(f"  ⚠ 全行 NaN の列: {na_cols} "
                      "（この表に該当データが無い＝別の列/ページが必要かも）")
        if not args.all and len(df) == 0:
            print("\n  【対処】上の table 一覧で『戦績表っぽい』表の class とヘッダを確認し、")
            print("         その class/ヘッダに合わせて netkeiba_parse を調整します。")
            print("         この出力をそのまま共有してください。")


def cmd_analyze(args):
    """保存済みデータを読み戻し、健全性診断と出走馬の成績分析を表示する。"""
    from . import analyze as A

    store = Storage.from_uri(args.store)
    try:
        ctx = service.load_context(args.race_id, store)
    except Exception as e:
        sys.exit(f"[analyze] データ読み込み失敗: {e}")

    # 1) データ健全性の診断（「関連レースが少ない？」への回答）
    summary = A.diagnose_coverage(ctx)
    print(A.format_coverage(summary))

    # 2) 出走馬の成績分析（レース時点の実力、リーク無し）
    print()
    view = A.analyze_entrants(ctx)
    print(f"=== 出走馬の成績分析: {ctx.race_name} ===")
    print(A.format_entrants(view))

    # 3) 過去レースなら答え合わせ（actual.csv があれば）
    actual_text = store.read_text(f"races/{args.race_id}/actual.csv")
    if actual_text:
        import io
        import pandas as pd
        actual = pd.read_csv(io.StringIO(actual_text), dtype={"horse_id": str})

        # 正しいレースを取れているか（既知の勝ち馬と照合）
        vw = A.verify_known_winner(args.race_id, actual)
        if vw is not None:
            if vw["match"] is True:
                print(f"\n✅ 勝ち馬照合OK: {vw['fetched_winner']}（既知の正解と一致）")
            elif vw["match"] is False:
                print(f"\n⚠ 勝ち馬不一致: 取得={vw['fetched_winner']} / "
                      f"既知={vw['known_winner']} → race_id かパースを確認してください。")
            else:
                print(f"\nℹ 勝ち馬: {vw['fetched_winner']}（既知表は要確認）")

        print()
        ev = A.evaluate_past_race(ctx, actual, rank_by=args.rank_by)
        print(A.format_evaluation(ev))

    # 4) 特定馬の戦績深掘り（任意）
    if args.horse:
        print()
        form = A.horse_form(ctx, args.horse)
        if form.empty:
            print(f"[analyze] horse_id={args.horse} の戦績が見つかりません。")
        else:
            print(f"=== 戦績: horse_id={args.horse} ===")
            print(form.to_string(index=False))

    # 5) CSV 書き出し（任意）
    if args.out_csv:
        view.to_csv(args.out_csv, index=False)
        print(f"\n出走馬分析を保存しました -> {args.out_csv}")


def _load_contexts(store, race_ids):
    """保存済み race_id 群を RaceContext のリストにロード（読めたものだけ）。"""
    ctxs = []
    for rid in race_ids:
        try:
            ctxs.append(service.load_context(rid, store))
        except Exception as e:
            print(f"  ⚠ {rid}: 読み込みスキップ ({e})")
    return ctxs


def _load_derby_train_items(store, race_ids, require_derby=True):
    """学習用 (ctx, actual) を読み込む。actual.csv が無い/ダービーでない年は除外。

    ダービー判定はレース名に『優駿』or『ダービー』を含むかで行い、間違った race_id
    （別レース）を学習から自動的に弾く。
    """
    import io
    import pandas as pd
    items = []
    for rid in race_ids:
        try:
            ctx = service.load_context(rid, store)
        except Exception as e:
            print(f"  ⚠ {rid}: 読み込みスキップ ({e})")
            continue
        name = str(ctx.race_name)
        if require_derby and ("優駿" not in name and "ダービー" not in name):
            print(f"  ⚠ {rid}: レース名『{name}』はダービーでないため学習から除外")
            continue
        atext = store.read_text(f"races/{rid}/actual.csv")
        if atext is None:
            print(f"  ⚠ {rid}: actual.csv 無し（fetch --past が必要）→ 学習から除外")
            continue
        actual = pd.read_csv(io.StringIO(atext), dtype={"horse_id": str})
        items.append((ctx, actual))
    return items


def cmd_predict(args):
    """過去ダービーで学習し、対象レースの複勝確率を予想する（ML）。"""
    from . import ml

    store = Storage.from_uri(args.store)
    train_ids = [r.strip() for r in args.train.split(",") if r.strip()] if args.train \
        else _default_derby_train_ids(exclude=args.race_id)
    print(f"[predict] 学習候補 {len(train_ids)} 件 / 対象 {args.race_id}")
    train_items = _load_derby_train_items(store, train_ids)
    if not train_items:
        sys.exit("[predict] 学習データがありません。fetch --past で過去ダービーを"
                 "取得してください（actual.csv が必要）。")

    X, y, _ = ml.build_target_training_data(train_items, label=args.label)
    print(f"  学習サンプル {len(X)} 頭（過去{len(train_items)}年のダービー出走馬 / "
          f"{args.label}率 {y.mean():.1%}）")
    model = ml.ShowProbModel(label=args.label).fit(X, y)

    try:
        ctx = service.load_context(args.race_id, store)
    except Exception as e:
        sys.exit(f"[predict] 対象レース読み込み失敗: {e}")
    pred = ml.predict_context(model, ctx)
    print()
    print(ml.format_prediction(pred, race_name=ctx.race_name))

    if args.show_coef:
        print("\n--- モデル係数（複勝にプラス/マイナスに効く特徴）---")
        print(model.coef_table().to_string(index=False))


def cmd_backtest(args):
    """過去ダービーで学習→各年テストし、的中率を集計する（ML のバックテスト）。"""
    import io
    import pandas as pd
    from . import ml
    from .data.derby import derby_race_id

    store = Storage.from_uri(args.store)
    years = _parse_years(args.years) if args.years else list(range(2016, 2025))
    test_ids = [derby_race_id(y) for y in years]

    # actual.csv 有り & 実際にダービーの年だけを使う（間違った race_id を自動除外）
    test_items = _load_derby_train_items(store, test_ids)
    if not test_items:
        sys.exit("[backtest] テスト可能な過去ダービーがありません"
                 "（fetch --past で actual.csv を保存してください）。")

    print(f"[backtest] テスト {len(test_items)} 年 / 各年それ以外で学習（leave-one-out）")
    rows = []
    for ctx, actual in test_items:
        others = [(c, a) for (c, a) in test_items if c.race_id != ctx.race_id]
        res = ml.backtest(others, [(ctx, actual)], label=args.label)
        pr = res["per_race"].iloc[0]
        rows.append(pr)
    per = pd.DataFrame(rows)
    print("\n=== 各年の結果（◎=複勝確率1位） ===")
    print(per[["race_id", "race_name", "honmei_finish",
               "winner_pred_rank", "top3_hits"]].to_string(index=False))
    print(f"\n◎の複勝率: {(per['honmei_finish'] <= 3).mean():.1%} / "
          f"◎の勝率: {(per['honmei_finish'] == 1).mean():.1%} / "
          f"予想上位3頭の平均的中: {per['top3_hits'].mean():.2f}/3")


def _default_derby_train_ids(exclude=None):
    from .data.derby import DERBY_RACES
    return [rid for rid in DERBY_RACES if rid != str(exclude)]


def _resolve_race_ids(args) -> list[str]:
    """fetch の対象 race_id 群を決める。

    優先順:
      --derby-years 2021-2025  : ダービー(東京・2回・12日目・11R)を年で展開
      --race-ids a,b,c         : カンマ区切りで複数指定
      --race-id  x             : 単一
    """
    if getattr(args, "derby_years", None):
        from .data.derby import derby_race_id
        years = _parse_years(args.derby_years)
        # 既知表があればそれを使い、無い年は規則(東京・2回・12日目・11R)で生成。
        return [derby_race_id(y) for y in years]
    if getattr(args, "race_ids", None):
        return [r.strip() for r in args.race_ids.split(",") if r.strip()]
    if args.race_id:
        return [args.race_id]
    sys.exit("--race-id / --race-ids / --derby-years のいずれかを指定してください。")


def _parse_years(spec: str) -> list[int]:
    """'2021-2025' or '2021,2022,2025' を年のリストに。"""
    spec = spec.strip()
    if "-" in spec and "," not in spec:
        a, b = spec.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in spec.replace(" ", "").split(",") if x]


def cmd_fetch(args):
    """手元の回線で netkeiba から取得し、ローカル or GCS に保存する。

    単一でも複数（過去5年分など）でも取得できる。保存レイアウトは Cloud Run の
    参照 API と同一（races/<race_id>/*.csv + meta.json）なので、--out に
    gs://<bucket>/keiba を渡せば、そのまま Cloud Run が読める。
    """
    store = Storage.from_uri(args.out)
    race_ids = _resolve_race_ids(args)
    print(f"[fetch] {len(race_ids)} レースを取得します（保存先: {store.uri()}）")
    print("        ※ netkeiba は GCP/DC IP を 403 で弾くため、手元回線で実行してください。")
    if args.past:
        print("        （過去レースモード: 結果ページから出走馬を作り、レース前時点で分析）")

    ok, empty, failed = [], [], []
    for rid in race_ids:
        try:
            summary = service.fetch_and_store(
                rid, store, wait=args.wait,
                max_history_per_horse=args.max_history,
                use_cache=not args.no_cache, proxy=args.proxy, past_race=args.past,
                race_date=args.race_date)
        except Exception as e:
            print(f"  ✗ {rid}: 失敗 ({e})")
            failed.append(rid)
            continue

        c = summary["counts"]
        if c["entries"] == 0:
            # race_id が間違っている（その年は開催日目がずれている等）と空になる
            print(f"  ⚠ {rid}: 出走0頭。race_id が間違っている可能性"
                  "（その年のダービーは開催日目が違うかも）。")
            empty.append(rid)
        else:
            print(f"  ✓ {rid}: 出走{c['entries']}頭 / 過去レース{c['races']}件 / "
                  f"成績{c['results']}行")
            ok.append(rid)

    print(f"\n[fetch] 完了: 成功 {len(ok)} / 空 {len(empty)} / 失敗 {len(failed)}")
    if empty:
        print(f"  空だった race_id: {', '.join(empty)}")
        print("  → 正しい race_id は netkeiba のレースページ URL の数字で確認できます。"
              "判明したら --race-ids で個別指定してください。")
    if ok and str(args.out).startswith("gs://"):
        print(f"  Cloud Run / analyze からは race_id で参照できます: {', '.join(ok)}")


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

    pf = sub.add_parser("fetch",
                        help="netkeiba から取得し保存（手元回線で実行。--out に gs:// 可）")
    pf.add_argument("--race-id", default=None, help="単一レースID")
    pf.add_argument("--race-ids", default=None,
                    help="複数レースID（カンマ区切り）。例: 202105021211,202205021211")
    pf.add_argument("--derby-years", default=None,
                    help="ダービーを年で一括指定。例: 2021-2025 / 2021,2022,2025 "
                         "（東京・2回・12日目・11R を仮定。開催日目がずれる年は空になる）")
    pf.add_argument("--out", default="data/fetched",
                    help="保存先。ローカルパス or gs://<bucket>/keiba")
    pf.add_argument("--race-date", default=None,
                    help="対象レースの施行日 YYYY-MM-DD（出馬表に日付が無い未来レース"
                         "の間隔計算用。ダービーは既知表から自動補完）")
    pf.add_argument("--wait", type=float, default=1.5, help="リクエスト間ウェイト秒")
    pf.add_argument("--max-history", type=int, default=None,
                    help="1頭あたり遡る過去レース数の上限")
    pf.add_argument("--proxy", default=None,
                    help="外向きプロキシ URL（通常は手元回線なので不要）")
    pf.add_argument("--no-cache", action="store_true",
                    help="HTML キャッシュを使わず必ず再取得")
    pf.add_argument("--past", action="store_true",
                    help="過去（施行済み）レースを分析対象にする（出馬表でなく結果"
                         "ページから出走馬を作り、答え合わせ用に actual.csv も保存）")
    pf.set_defaults(func=cmd_fetch)

    pa = sub.add_parser("analyze",
                        help="保存済みデータを診断・分析（オフライン。--store に gs:// 可）")
    pa.add_argument("--race-id", required=True)
    pa.add_argument("--store", default="data/fetched",
                    help="参照先。fetch の --out と同じ場所（ローカル or gs://）")
    pa.add_argument("--horse", default=None, help="深掘りする horse_id（任意）")
    pa.add_argument("--rank-by", default="pit_show_rate",
                    help="過去レース答え合わせの並べ替え指標（既定: pit_show_rate）")
    pa.add_argument("--out-csv", default=None, help="出走馬分析を CSV 保存（任意）")
    pa.set_defaults(func=cmd_analyze)

    pp_ = sub.add_parser("predict",
                         help="過去レースで学習し対象レースの複勝確率を予想（ML）")
    pp_.add_argument("--race-id", required=True, help="予想対象レースID")
    pp_.add_argument("--store", default="data/fetched",
                     help="参照先（ローカル or gs://）")
    pp_.add_argument("--train", default=None,
                     help="学習レースID（カンマ区切り）。未指定なら既知ダービー全年"
                          "（対象は除外）")
    pp_.add_argument("--label", default="show", choices=["show", "win"],
                     help="予測ラベル: show=複勝(3着内) / win=勝ち(1着)")
    pp_.add_argument("--show-coef", action="store_true",
                     help="モデル係数（特徴の効き方）も表示")
    pp_.set_defaults(func=cmd_predict)

    pb = sub.add_parser("backtest",
                        help="過去ダービーで学習→各年テストし的中率を集計（ML）")
    pb.add_argument("--store", default="data/fetched",
                    help="参照先（ローカル or gs://）")
    pb.add_argument("--years", default=None,
                    help="対象年。例 2016-2024 / 2018,2020,2022（既定 2016-2024）")
    pb.add_argument("--label", default="show", choices=["show", "win"])
    pb.set_defaults(func=cmd_backtest)

    pdh = sub.add_parser("diagnose-horse",
                         help="競走馬ページHTMLの中身を点検（戦績パース不能の原因特定）")
    pdh.add_argument("--race-id", required=True)
    pdh.add_argument("--store", default="data/fetched",
                     help="参照先。fetch の --out と同じ場所（ローカル or gs://）")
    pdh.add_argument("--all", action="store_true",
                     help="全出走馬を点検（既定は先頭1頭を詳しく）")
    pdh.add_argument("--wait", type=float, default=1.5, help="リクエスト間ウェイト秒")
    pdh.add_argument("--proxy", default=None, help="外向きプロキシ URL（任意）")
    pdh.set_defaults(func=cmd_diagnose_horse)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
