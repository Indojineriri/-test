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
from .storage import Storage


def _race_prefix(race_id: str) -> str:
    return f"races/{race_id}"


def fetch_and_store(race_id: str, store: Storage, *, cache: Storage | None = None,
                    wait: float = 1.5, max_history_per_horse: int | None = None,
                    use_cache: bool = True, proxy: str | None = None) -> dict:
    """netkeiba から1レース分を取得し、CSV/JSON を store に保存してサマリを返す。

    Args:
        race_id: 対象レースID（12桁）
        store: 出力 CSV/JSON の保存先（Cloud Run では gs://...）
        cache: HTML キャッシュの保存先（既定: store の cache/ prefix を流用）
        max_history_per_horse: 1頭あたり遡る過去レース数の上限（負荷/時間の調整）
        proxy: 外向きプロキシ URL（GCP IP ブロック回避）。未指定なら環境変数を参照。
    """
    # HTML キャッシュ先。未指定なら出力 store と同じバケットの cache/ に置く。
    cache_storage = cache or _subprefix(store, "cache")
    client = NetkeibaClient(wait=wait, use_cache=use_cache, cache=cache_storage,
                            proxy=proxy)
    src = NetkeibaDataSource(race_id, client=client,
                             max_history_per_horse=max_history_per_horse)

    ctx = src.build_context()

    prefix = _race_prefix(race_id)
    store.write_text(f"{prefix}/entries.csv", ctx.entries.to_csv(index=False))
    store.write_text(f"{prefix}/results.csv", ctx.history.to_csv(index=False))
    store.write_text(f"{prefix}/races.csv", ctx.races.to_csv(index=False))
    if ctx.horses is not None:
        store.write_text(f"{prefix}/horses.csv", ctx.horses.to_csv(index=False))

    summary = {
        "race_id": race_id,
        "race_meta": ctx.race,
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


def _subprefix(store: Storage, sub: str) -> Storage:
    """同じバックエンドで prefix を1段掘った Storage を作る。"""
    base = store.uri(sub)
    return Storage.from_uri(base)
