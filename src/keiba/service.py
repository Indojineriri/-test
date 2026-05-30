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
                    past_race: bool = False) -> dict:
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


def _subprefix(store: Storage, sub: str) -> Storage:
    """同じバックエンドで prefix を1段掘った Storage を作る。"""
    base = store.uri(sub)
    return Storage.from_uri(base)
