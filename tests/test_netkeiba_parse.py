"""netkeiba パーサとデータソースのオフライン検証。

実ネットワークは使わず、tests/fixtures/ の HTML とフェイククライアントで
①②データ取得の正しさを確認する。

    python3 -m pytest tests/test_netkeiba_parse.py   # pytest があれば
    python3 tests/test_netkeiba_parse.py             # 直接実行でも可
"""

import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
FIXTURES = Path(__file__).resolve().parent / "fixtures"

from keiba.collect import netkeiba_parse as P  # noqa: E402
from keiba.collect.netkeiba import NetkeibaDataSource, build_race_id  # noqa: E402


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --- 値パースの単体テスト ----------------------------------------------------

def test_parse_time_to_sec():
    assert P.parse_time_to_sec("2:24.1") == 144.1
    assert P.parse_time_to_sec("59.8") == 59.8
    assert math.isnan(P.parse_time_to_sec(""))


def test_parse_sex_age():
    assert P.parse_sex_age("牡3") == ("牡", 3)
    assert P.parse_sex_age("牝3") == ("牝", 3)
    assert P.parse_sex_age("セ5") == ("セ", 5)


def test_parse_horse_weight():
    assert P.parse_horse_weight("480(+2)") == (480, 2)
    assert P.parse_horse_weight("448(0)") == (448, 0)
    val, diff = P.parse_horse_weight("計不")
    assert math.isnan(val) and math.isnan(diff)


# --- レース結果ページ --------------------------------------------------------

def test_parse_race_result():
    meta, df = P.parse_race_result(_read("race_result.html"), "202405021211")

    # メタ情報
    assert meta["race_id"] == "202405021211"
    assert "テスト記念" in meta["race_name"]
    assert meta["surface"] == "芝"
    assert meta["distance"] == 2400
    assert meta["direction"] == "右"
    assert meta["weather"] == "晴"
    assert meta["going"] == "良"
    assert meta["date"] == "2024-05-26"
    assert meta["track"] == "東京"
    assert meta["grade"] == "G1"

    # 結果テーブル
    assert len(df) == 3
    top = df.iloc[0]
    assert top["finish_pos"] == 1
    assert top["horse_id"] == "2021104001"
    assert top["horse_name"] == "テストランナー"
    assert top["frame_no"] == 5 and top["horse_no"] == 9
    assert top["sex"] == "牡" and top["age"] == 3
    assert top["impost"] == 57.0
    assert top["jockey"] == "山田太郎"
    assert top["jockey_id"] == "05339"
    assert top["time_sec"] == 144.1
    assert top["last_3f"] == 33.5
    assert top["odds"] == 2.1
    assert top["popularity"] == 1
    assert top["horse_weight"] == 480 and top["weight_diff"] == 2
    assert top["trainer_id"] == "01075"
    assert top["passing"] == "5-5-3-2"
    assert top["prize"] == 20000.0


# --- 出馬表ページ ------------------------------------------------------------

def test_parse_shutuba():
    meta, df = P.parse_shutuba(_read("shutuba.html"), "202405021211")
    assert "テスト記念" in meta["race_name"]
    assert meta["distance"] == 2400
    assert meta["track"] == "東京"
    assert len(df) == 3

    r = df.iloc[0]
    assert r["horse_id"] == "2021104001"
    assert r["horse_name"] == "テストランナー"
    assert r["frame_no"] == 5 and r["horse_no"] == 9
    assert r["impost"] == 57.0
    assert r["odds"] == 2.0 and r["popularity"] == 1
    assert r["horse_weight"] == 482


# --- 競走馬ページ ------------------------------------------------------------

def test_parse_horse_profile():
    prof = P.parse_horse_profile(_read("horse.html"), "2021104001")
    assert prof["horse_name"] == "テストランナー"
    assert prof["birth_year"] == 2021
    assert prof["sire"] == "父サンプルサイアー"
    assert prof["dam"] == "母サンプルダム"
    assert prof["dam_sire"] == "母父ブルードメアサイアー"
    assert "鈴木一郎" in prof["trainer"]


def test_race_ids_from_horse_page():
    ids = NetkeibaDataSource._race_ids_from_horse_page(_read("horse.html"))
    assert ids == ["202405020811", "202406010512", "202306050810"]


def test_race_ids_handles_both_url_forms():
    """パス形式 /race/ID/ とクエリ形式 ?race_id=ID の両方を取りこぼさない。

    （これが取りこぼしバグの原因: 旧実装は /race/(\\d+) しか見ず、
      race.netkeiba.com/race/result.html?race_id=... 形式を落としていた）
    """
    html = """<html><body>
      <a href="/race/202405020811/">A</a>
      <a href="https://race.netkeiba.com/race/result.html?race_id=202406010512&rf=x">B</a>
      <a href="/race/result.html?race_id=202306050810">C</a>
      <a href="/horse/2021104001/">無関係</a>
    </body></html>"""
    ids = NetkeibaDataSource._race_ids_from_horse_page(html)
    assert ids == ["202405020811", "202406010512", "202306050810"]


# --- race_id ビルダ ----------------------------------------------------------

def test_build_race_id():
    # 2024 日本ダービー（東京・2回・12日目・11R）
    assert build_race_id(2024, "東京", 2, 12, 11) == "202405021211"


# --- データソース end-to-end（フェイククライアント） ------------------------

class FakeClient:
    """フィクスチャ HTML を返すフェイク。ネットワークを使わない。"""

    def shutuba_html(self, race_id):
        return _read("shutuba.html")

    def horse_html(self, horse_id):
        return _read("horse.html")

    def race_result_html(self, race_id):
        # どの race_id でも同じ結果ページ構造を返す（race_id は引数で渡る）
        return _read("race_result.html")


def test_parse_horse_results_full_career():
    """競走馬ページの戦績表を直接パースし、全成績を取りこぼし無く取得する。"""
    df = P.parse_horse_results(_read("horse.html"), "2021104001")
    assert len(df) == 3  # 戦績表の全 3 走
    # race_id がパス形式・クエリ形式の両方から取れている
    assert list(df["race_id"]) == ["202405020811", "202406010512", "202306050810"]
    # 各レースの成績が埋まっている（別ページに飛ばず1ページで取得）
    top = df.iloc[0]
    assert top["finish_pos"] == 1 and top["horse_no"] == 9
    assert top["passing"] == "5-5-3" and top["last_3f"] == 34.1
    assert top["prize"] == 6500.0
    assert top["popularity"] == 2


def test_horse_results_meta_rows():
    """戦績表から races メタ（距離・グレード・頭数等）が取れる。"""
    rows = P._horse_results_meta_rows(_read("horse.html"), "2021104001")
    assert len(rows) == 3
    r0 = rows[0]
    assert r0["race_id"] == "202405020811"
    assert r0["date"] == "2024-04-14"
    assert r0["distance"] == 2000 and r0["surface"] == "芝"
    assert r0["grade"] == "G1" and r0["track"] == "中山"
    assert r0["n_horses"] == 18


def test_datasource_build_context():
    src = NetkeibaDataSource("202405021211", client=FakeClient())
    ctx = src.build_context()

    # ②対象レースの出走表
    assert len(ctx.entries) == 3
    assert set(ctx.entries["horse_id"]) == {"2021104001", "2021104002", "2021104003"}

    # ①各馬の過去成績（horse ページの 3 レース分が集約される）
    assert not ctx.history.empty
    assert not ctx.races.empty
    # 履歴は出走馬の horse_id のみ
    assert set(ctx.history["horse_id"]).issubset(set(ctx.entries["horse_id"]))

    # 血統マスタ
    assert len(ctx.horses) == 3
    assert ctx.horses.iloc[0]["sire"] == "父サンプルサイアー"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok: {fn.__name__}")
    print(f"OK: {len(fns)} tests passed")
