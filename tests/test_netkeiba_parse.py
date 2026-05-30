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


def test_parse_horse_results_real_header_layout():
    """実物の /horse/result/ のヘッダ並び（天気/R/水分量/馬場指数/タイム指数あり）でも
    ヘッダ文字列対応により正しい列を拾えることを固定する（回帰テスト）。

    実データのヘッダ:
      日付 開催 天気 R レース名 映像 頭数 枠番 馬番 オッズ 人気 着順 騎手 斤量
      距離 水分量 馬場 馬場指数 タイム 着差 ﾀｲﾑ指数 通過 ペース 上り 馬体重 賞金
    """
    html = """<html><body><h1>テスト馬</h1>
    <table class="db_h_race_results nk_tb_common">
      <tr>
        <th>日付</th><th>開催</th><th>天気</th><th>R</th><th>レース名</th><th>映像</th>
        <th>頭数</th><th>枠番</th><th>馬番</th><th>オッズ</th><th>人気</th><th>着順</th>
        <th>騎手</th><th>斤量</th><th>距離</th><th>水分量</th><th>馬場</th><th>馬場指数</th>
        <th>タイム</th><th>着差</th><th>ﾀｲﾑ指数</th><th>通過</th><th>ペース</th>
        <th>上り</th><th>馬体重</th><th>賞金</th>
      </tr>
      <tr>
        <td>2025/12/28</td><td>6中山9</td><td>晴</td><td>11</td>
        <td><a href="/race/202506050811/">ホープフルS(G1)</a></td><td>動画</td>
        <td>16</td><td>5</td><td>9</td><td>20.1</td><td>9</td><td>3</td>
        <td><a href="/jockey/result/recent/05339/">武豊</a></td><td>56</td>
        <td>芝2000</td><td>0.8</td><td>良</td><td>-10</td><td>2:01.5</td><td>0.2</td>
        <td>-5</td><td>10-9-9-8</td><td>36.5-12.0</td><td>33.8</td><td>480(+2)</td>
        <td>5350.1</td>
      </tr>
    </table></body></html>"""
    df = P.parse_horse_results(html, "9999")
    assert len(df) == 1
    r = df.iloc[0]
    assert r["finish_pos"] == 3
    assert r["horse_no"] == 9
    assert r["popularity"] == 9
    assert r["passing"] == "10-9-9-8"     # 脚質の元（通過順）
    assert r["last_3f"] == 33.8           # 上がり（タイム指数の列に惑わされない）
    assert r["prize"] == 5350.1           # 賞金
    assert r["race_id"] == "202506050811"


def test_grade_from_name_roman_forms():
    """netkeiba の GI/GII/GIII（ASCIIローマ数字）も G1/G2/G3 に正規化する。"""
    g = P._grade_from_name
    assert g("日本ダービー(GI)") == "G1"
    assert g("共同通信杯(GIII)") == "G3"
    assert g("弥生賞ディープインパクト記念(GII)") == "G2"
    assert g("皐月賞(G1)") == "G1"
    assert g("プリンシパルS(L)") is None
    assert g("1勝クラス") is None


def test_parse_horse_results_carries_race_meta():
    """1パスで各行に date/grade/distance/n_horses が載る（2回パースのズレ解消）。"""
    df = P.parse_horse_results(_read("horse.html"), "2021104001")
    assert len(df) == 3
    r = df.iloc[0]
    assert r["race_date"] == "2024-04-14"
    assert r["race_distance"] == 2000
    assert r["race_grade"] == "G1"
    assert r["race_n_horses"] == 18
    assert r["race_track"] == "中山"


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

    def horse_result_html(self, horse_id):
        # 戦績一覧ページ。フィクスチャは horse.html に戦績表を含めている。
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
