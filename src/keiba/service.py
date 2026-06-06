"""取得→保存→参照のサービス層（Flask 非依存）。

web 層(Flask)から呼ばれる中核ロジックをここに集約する。Flask に依存しないので
単体テストしやすく、将来 Cloud Run Job や CLI からも同じ関数を再利用できる。

保存レイアウト（Storage の base 配下）:
    races/<race_id>/entries.csv
    races/<race_id>/results.csv
    races/<race_id>/races.csv
    races/<race_id>/horses.csv
    races/<race_id>/meta.json      … レースメタ + 取得サマリ
HTML キャッシュは別 prefix:
    cache/shutuba_<race_id>.html 等
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from .collect.netkeiba import NetkeibaDataSource
from .collect.netkeiba_client import NetkeibaClient
from .collect import netkeiba_parse as P
from .storage import Storage


def _race_prefix(race_id: str) -> str:
    return f"races/{race_id}"


def fetch_and_store(race_id: str, store: Storage, *, cache: Storage | None = None,
                    wait: float = 1.5, max_history_per_horse: int | None = None,
                    use_cache: bool = True, proxy: str | None = None,
                    past_race: bool = False, race_date: str | None = None) -> dict:
    """netkeiba から1レース分を取得し、CSV/JSON を store に保存してサマリを返す。

    Args:
        race_id: 対象レースID（12桁）
        store: 出力 CSV/JSON の保存先（Cloud Run では gs://...）
        cache: HTML キャッシュの保存先（既定: store の cache/ prefix を流用）
        max_history_per_horse: 1頭あたり遡る過去レース数の上限（負荷/時間の調整）
        proxy: 外向きプロキシ URL（GCP IP ブロック回避）。未指定なら環境変数を参照。
        past_race: 過去（施行済み）レースを分析対象にする。出馬表でなく結果ページから
            出走馬を作り、各馬の履歴から当該レース自身を除外する（レース前の状態で分析）。
    """
    # HTML キャッシュ先。未指定なら出力 store と同じバケットの cache/ に置く。
    cache_storage = cache or _subprefix(store, "cache")
    client = NetkeibaClient(wait=wait, use_cache=use_cache, cache=cache_storage,
                            proxy=proxy)
    src = NetkeibaDataSource(race_id, client=client,
                             max_history_per_horse=max_history_per_horse,
                             past_race=past_race)

    ctx = src.build_context()

    # 対象レースの施行日を補完（出馬表ページには日付が無いことが多い）。
    # 間隔(日) などの計算に必要。明示指定 > 既知レース表(ダービー/安田記念等) の順で補う。
    if not ctx.race.get("date"):
        from .data.races import known_race_date
        ctx.race["date"] = race_date or known_race_date(race_id)

    prefix = _race_prefix(race_id)
    store.write_text(f"{prefix}/entries.csv", ctx.entries.to_csv(index=False))
    store.write_text(f"{prefix}/results.csv", ctx.history.to_csv(index=False))
    store.write_text(f"{prefix}/races.csv", ctx.races.to_csv(index=False))
    if ctx.horses is not None:
        store.write_text(f"{prefix}/horses.csv", ctx.horses.to_csv(index=False))

    # 過去レース分析では、対象レース自身の結果（着順）を答え合わせ用に保存する。
    if past_race:
        _, actual = P.parse_race_result(client.race_result_html(race_id), race_id)
        store.write_text(f"{prefix}/actual.csv", actual.to_csv(index=False))

    summary = {
        "race_id": race_id,
        "race_meta": ctx.race,
        "past_race": bool(past_race),
        "counts": {
            "entries": int(len(ctx.entries)),
            "results": int(len(ctx.history)),
            "races": int(len(ctx.races)),
            "horses": int(len(ctx.horses)) if ctx.horses is not None else 0,
        },
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "storage": store.uri(prefix),
    }
    store.write_text(f"{prefix}/meta.json",
                     json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return summary


def check_connectivity(proxy: str | None = None) -> dict:
    """Cloud Run から netkeiba に到達できるかを確認する（/diag 用）。

    スクレイピング本体を走らせず、トップページに 1 回だけアクセスして
    到達性・ブロック有無・プロキシ経由かを返す。
    """
    client = NetkeibaClient(use_cache=False, proxy=proxy, max_retries=1)
    return client.check_connectivity()


def list_races(store: Storage) -> list[str]:
    """保存済みレースID一覧（meta.json のあるものを抽出）。"""
    out = []
    for rel in store.list("races/"):
        if rel.endswith("/meta.json"):
            out.append(rel[len("races/"):-len("/meta.json")])
    return sorted(out)


def load_meta(race_id: str, store: Storage) -> dict | None:
    text = store.read_text(f"{_race_prefix(race_id)}/meta.json")
    return json.loads(text) if text else None


def read_csv_text(race_id: str, name: str, store: Storage) -> str | None:
    """entries/results/races/horses の CSV テキストを返す（無ければ None）。"""
    if name not in ("entries", "results", "races", "horses"):
        raise ValueError(f"未知の CSV: {name}")
    return store.read_text(f"{_race_prefix(race_id)}/{name}.csv")


def load_context(race_id: str, store: Storage):
    """保存済みの CSV/JSON を読み戻して RaceContext を再構築する。

    fetch_and_store で保存したデータを、分析・予想で使える形にロードする。
    取得（ネットワーク）は一切しないので、Cloud Run でもオフラインでも動く。
    """
    import io
    import pandas as pd
    from .schema import RaceContext

    def _csv(name):
        text = read_csv_text(race_id, name, store)
        if text is None:
            return pd.DataFrame()
        # race_id / horse_id 等は文字列として扱う（先頭ゼロや桁落ちを防ぐ）
        return pd.read_csv(io.StringIO(text),
                           dtype={"race_id": str, "horse_id": str,
                                  "jockey_id": str, "trainer_id": str})

    meta = load_meta(race_id, store)
    if meta is None:
        raise FileNotFoundError(
            f"race_id={race_id} の保存データが見つかりません（store={store.uri()}）。"
            f" 先に `keiba.cli fetch` で取得してください。")
    race_meta = meta.get("race_meta", {"race_id": race_id})

    return RaceContext(
        race=race_meta,
        entries=_csv("entries"),
        history=_csv("results"),
        races=_csv("races"),
        horses=_csv("horses") if read_csv_text(race_id, "horses", store) else None,
    )


def load_actual(race_id: str, store: Storage):
    """過去レースの実結果(actual.csv)を DataFrame で返す（無ければ None）。"""
    import io
    import pandas as pd
    text = store.read_text(f"{_race_prefix(race_id)}/actual.csv")
    if text is None:
        return None
    return pd.read_csv(io.StringIO(text), dtype={"horse_id": str})


def load_derby_items(store: Storage, race_ids, require_derby: bool = True):
    """学習/示唆用に (RaceContext, actual_df) のリストを読み込む。

    actual.csv が無い年や、レース名に『優駿/ダービー』を含まない年（間違った
    race_id で別レースを取ってしまった年）は自動的に除外する。CLI と web 層で共有。
    """
    items, skipped = [], []
    for rid in race_ids:
        try:
            ctx = load_context(rid, store)
        except Exception:
            skipped.append((rid, "読み込み不可"))
            continue
        name = str(ctx.race_name)
        if require_derby and ("優駿" not in name and "ダービー" not in name):
            skipped.append((rid, f"ダービーでない({name})"))
            continue
        actual = load_actual(rid, store)
        if actual is None:
            skipped.append((rid, "actual.csv 無し"))
            continue
        items.append((ctx, actual))
    return items, skipped


def load_race_items(store: Storage, race_ids, match_name: str | None = None):
    """学習/示唆用に (RaceContext, actual_df) のリストを読み込む（レース非依存）。

    actual.csv が無い年は除外する。match_name を渡すと、レース名の“核”
    （normalize_race_name）が一致する年だけを採用する（別レースを誤取得した年を弾く）。
    load_derby_items のダービー固定版を一般化したもの。

    Returns: (items, skipped)
    """
    from .data.races import normalize_race_name
    want = normalize_race_name(match_name) if match_name else None
    items, skipped = [], []
    for rid in race_ids:
        try:
            ctx = load_context(rid, store)
        except Exception:
            skipped.append((rid, "読み込み不可"))
            continue
        name = str(ctx.race_name)
        if want and normalize_race_name(name) != want:
            skipped.append((rid, f"別レース({name})"))
            continue
        actual = load_actual(rid, store)
        if actual is None:
            skipped.append((rid, "actual.csv 無し"))
            continue
        items.append((ctx, actual))
    return items, skipped


def default_train_ids_for(target_race_id: str, store: Storage):
    """対象レースと“同じレースの過去開催”の race_id を自動選択する（学習対象）。

    手順:
      1. 保存済みレースのうち actual.csv を持つものを走査し、対象とレース名の核が
         一致する年（=同じレースの別年度）を集める。対象自身は除く。
      2. まだ何も取れていない場合は、既知レース表(KNOWN_RACES)の同レース候補を返す。
    これにより、安田記念でもダービーでも「自分の過去開催で学習」できる。
    """
    from .data.races import normalize_race_name, registry_family_ids
    target_id = str(target_race_id)
    # 対象レース名（メタ）を取得
    meta = load_meta(target_id, store) or {}
    target_name = (meta.get("race_meta") or {}).get("race_name")
    want = normalize_race_name(target_name) if target_name else None

    found = []
    for rid in list_races(store):
        if rid == target_id:
            continue
        if load_actual(rid, store) is None:
            continue
        if want:
            m = load_meta(rid, store) or {}
            nm = (m.get("race_meta") or {}).get("race_name")
            if normalize_race_name(nm) != want:
                continue
        found.append(rid)
    if found:
        return sorted(found)
    # フォールバック: 既知表の同レース候補（未取得年を含む）
    return sorted(r for r in registry_family_ids(target_id) if r != target_id)


def default_derby_train_ids(exclude=None):
    """学習に使う既知ダービーの race_id（exclude を除く）。

    verified=True の年（=実データでダービーと確認済み）のみ。2016-2018 は
    `年05021211` が別レース（薫風ステークス）だったため verified=False で除外する。
    """
    from .data.derby import DERBY_RACES
    return [rid for rid, info in DERBY_RACES.items()
            if info.get("verified") and rid != str(exclude)]


def _subprefix(store: Storage, sub: str) -> Storage:
    """同じバックエンドで prefix を1段掘った Storage を作る。"""
    base = store.uri(sub)
    return Storage.from_uri(base)
