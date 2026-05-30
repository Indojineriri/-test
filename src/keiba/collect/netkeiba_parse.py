"""netkeiba の HTML を正規化済み DataFrame に変換する「純粋関数」群。

ここには **ネットワーク I/O を一切置かない**。入力は HTML 文字列、出力は
schema.py 準拠の dict / DataFrame。こうすることで、保存済み HTML（フィクスチャ）に
対してオフラインで単体テストできる。実サイトの HTML 構造が変わった場合も、
修正対象はこのモジュールに局所化される。

対象ページ:
  - レース結果ページ  db.netkeiba.com/race/{race_id}/        -> parse_race_result
  - 出馬表ページ      race.netkeiba.com/race/shutuba.html      -> parse_shutuba
  - 競走馬ページ      db.netkeiba.com/horse/{horse_id}/        -> parse_horse_profile

注意: netkeiba の HTML はマイナーチェンジが入る。セレクタは複数候補を試す
      フォールバック方式にし、ヘッダ文字列（「着順」「馬番」など）で列を対応付ける
      ことで、列順の変更にも比較的強くしている。
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd
from bs4 import BeautifulSoup

# --- ID 抽出ヘルパ -----------------------------------------------------------

_ID_PATTERNS = {
    "horse": re.compile(r"/horse/(\w+)"),
    "jockey": re.compile(r"/jockey/(?:result/recent/|result/|profile/)?(\w+)"),
    "trainer": re.compile(r"/trainer/(?:result/recent/|result/|profile/)?(\w+)"),
}


def _extract_id(href: str | None, kind: str) -> str | None:
    """href（例 "/horse/2021104215/"）から ID を取り出す。"""
    if not href:
        return None
    m = _ID_PATTERNS[kind].search(href)
    return m.group(1) if m else None


def _first_link_id(cell, kind: str) -> str | None:
    """セル内の最初の該当リンクから ID を抽出。"""
    if cell is None:
        return None
    for a in cell.find_all("a", href=True):
        _id = _extract_id(a["href"], kind)
        if _id:
            return _id
    return None


def _text(cell) -> str:
    return cell.get_text(strip=True) if cell is not None else ""


# --- 値パースヘルパ ----------------------------------------------------------

def parse_time_to_sec(text: str):
    """'2:24.0' や '1:34.5' を秒に変換。'59.8' のような分なし表記も可。"""
    text = (text or "").strip()
    if not text:
        return np.nan
    m = re.match(r"(?:(\d+):)?(\d+(?:\.\d+)?)$", text)
    if not m:
        return np.nan
    minutes = int(m.group(1)) if m.group(1) else 0
    seconds = float(m.group(2))
    return round(minutes * 60 + seconds, 2)


def parse_sex_age(text: str):
    """'牡3' -> ('牡', 3)。'セ5' や '牝4' にも対応。"""
    text = (text or "").strip()
    m = re.match(r"([牡牝セせんセン騙]+)\s*(\d+)", text)
    if not m:
        return (text or None, np.nan)
    sex = m.group(1)
    return (sex, int(m.group(2)))


def parse_horse_weight(text: str):
    """'480(+2)' -> (480, 2)。計不・取消は (NaN, NaN)。"""
    text = (text or "").strip()
    m = re.match(r"(\d+)\(([-+]?\d+)\)", text)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    m2 = re.match(r"(\d+)$", text)
    if m2:
        return (int(m2.group(1)), np.nan)
    return (np.nan, np.nan)


def _to_int(text):
    try:
        return int(re.sub(r"[^\d-]", "", str(text)))
    except (ValueError, TypeError):
        return np.nan


def _to_float(text):
    try:
        return float(re.sub(r"[^\d.\-]", "", str(text)))
    except (ValueError, TypeError):
        return np.nan


# --- ヘッダ → 正規化キーの対応表 --------------------------------------------
# netkeiba のヘッダ文字列（空白除去後）を、こちらの内部キーへ写像する。
_RESULT_HEADER_MAP = {
    "着順": "finish_pos", "着": "finish_pos",
    "枠番": "frame_no", "枠": "frame_no",
    "馬番": "horse_no",
    "馬名": "horse_name",
    "性齢": "sex_age",
    "斤量": "impost",
    "騎手": "jockey",
    "タイム": "time",
    "着差": "margin",
    "通過": "passing",
    "上り": "last_3f", "上がり": "last_3f",
    "単勝": "odds",
    "人気": "popularity",
    "馬体重": "horse_weight",
    "調教師": "trainer", "厩舎": "trainer",
    "賞金(万円)": "prize", "賞金": "prize",
}


def _header_keys(table) -> list[str]:
    """テーブル先頭行の <th>（無ければ最初の <tr>）からヘッダキー列を作る。"""
    head = table.find("tr")
    cells = head.find_all(["th", "td"])
    keys = []
    for c in cells:
        raw = re.sub(r"\s+", "", _text(c))
        keys.append(_RESULT_HEADER_MAP.get(raw, raw))
    return keys


# --- レース結果ページ --------------------------------------------------------

def parse_race_result(html: str, race_id: str) -> tuple[dict, pd.DataFrame]:
    """db.netkeiba.com/race/{race_id}/ の HTML を (race_meta, results_df) に変換。"""
    soup = BeautifulSoup(html, "lxml")

    race_meta = _parse_result_meta(soup, race_id)
    table = (soup.select_one("table.race_table_01")
             or soup.select_one("table.nk_tb_common")
             or soup.find("table"))
    if table is None:
        from ..schema import RESULT_COLUMNS
        return race_meta, pd.DataFrame(columns=RESULT_COLUMNS)

    keys = _header_keys(table)
    rows = []
    for tr in table.find_all("tr")[1:]:
        cells = tr.find_all("td")
        if not cells:
            continue
        cmap = dict(zip(keys, cells))
        rows.append(_result_row(cmap, race_id))

    df = pd.DataFrame(rows)
    df = _finalize_columns(df, kind="result")
    race_meta["n_horses"] = int(df["finish_pos"].notna().sum()) or len(df)
    return race_meta, df


def _result_row(cmap: dict, race_id: str) -> dict:
    sex, age = parse_sex_age(_text(cmap.get("sex_age")))
    hw, wd = parse_horse_weight(_text(cmap.get("horse_weight")))
    return {
        "race_id": race_id,
        "horse_id": _first_link_id(cmap.get("horse_name"), "horse"),
        "horse_name": _text(cmap.get("horse_name")),
        "finish_pos": _to_int(_text(cmap.get("finish_pos"))),
        "frame_no": _to_int(_text(cmap.get("frame_no"))),
        "horse_no": _to_int(_text(cmap.get("horse_no"))),
        "sex": sex,
        "age": age,
        "impost": _to_float(_text(cmap.get("impost"))),
        "jockey": _text(cmap.get("jockey")),
        "jockey_id": _first_link_id(cmap.get("jockey"), "jockey"),
        "time_sec": parse_time_to_sec(_text(cmap.get("time"))),
        "margin": _text(cmap.get("margin")) or None,
        "passing": _text(cmap.get("passing")) or None,
        "last_3f": _to_float(_text(cmap.get("last_3f"))),
        "odds": _to_float(_text(cmap.get("odds"))),
        "popularity": _to_int(_text(cmap.get("popularity"))),
        "horse_weight": hw,
        "weight_diff": wd,
        "trainer": _text(cmap.get("trainer")),
        "trainer_id": _first_link_id(cmap.get("trainer"), "trainer"),
        "prize": _parse_prize(_text(cmap.get("prize"))),
    }


def _parse_prize(text: str):
    """'5,200.0' のような賞金表記（万円）を float に。空欄は NaN。"""
    text = (text or "").strip().replace(",", "")
    if not text:
        return np.nan
    return _to_float(text)


def _parse_result_meta(soup: BeautifulSoup, race_id: str) -> dict:
    """レース名・日付・コース・馬場などのメタ情報を抽出。"""
    meta = {"race_id": race_id, "race_name": None, "date": None, "track": None,
            "surface": None, "distance": np.nan, "direction": None,
            "going": None, "weather": None, "grade": None, "n_horses": np.nan}

    h1 = soup.select_one(".racedata h1") or soup.find("h1")
    if h1:
        meta["race_name"] = _text(h1)

    # コース・天候・馬場（"ダ右1600m / 天候 : 晴 / ダート : 良 / 発走 : 15:40"）
    cond = soup.select_one(".racedata diary_snap_cut span") or soup.select_one(".racedata span")
    cond_text = _text(cond)
    if cond_text:
        _fill_course(meta, cond_text)

    # 日付・開催（"2024年5月5日 1回東京2日目 ..." ）
    smalltxt = soup.select_one("p.smalltxt") or soup.select_one(".smalltxt")
    small = _text(smalltxt)
    if small:
        dm = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", small)
        if dm:
            meta["date"] = f"{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}"
        tm = re.search(r"\d回(\D+?)\d+日", small)
        if tm and not meta["track"]:
            meta["track"] = tm.group(1)

    # グレード（レース名や class から）
    name = meta["race_name"] or ""
    gm = re.search(r"\((G[1-3])\)|（(G[1-3])）|(G[1-3])", name)
    if gm:
        meta["grade"] = next(g for g in gm.groups() if g)
    return meta


def _fill_course(meta: dict, text: str):
    # 例: "ダ右1600m" / "芝左 外2400m" / "障芝3000m"
    m = re.search(r"(障?[芝ダ])\s*([右左直内外]*)\s*(\d+)m", text)
    if m:
        surf = m.group(1)
        meta["surface"] = {"芝": "芝", "ダ": "ダート"}.get(surf[-1], surf)
        if surf.startswith("障"):
            meta["surface"] = "障害"
        meta["direction"] = (m.group(2) or None)
        meta["distance"] = int(m.group(3))
    wm = re.search(r"天候\s*[:：]\s*(\S+)", text)
    if wm:
        meta["weather"] = wm.group(1)
    gm = re.search(r"(?:芝|ダート|馬場)\s*[:：]\s*(\S+)", text)
    if gm:
        meta["going"] = gm.group(1)


# --- 出馬表ページ ------------------------------------------------------------

_SHUTUBA_HEADER_MAP = {
    "枠": "frame_no", "枠番": "frame_no",
    "馬番": "horse_no",
    "馬名": "horse_name",
    "性齢": "sex_age",
    "斤量": "impost",
    "騎手": "jockey",
    "厩舎": "trainer", "調教師": "trainer",
    "馬体重": "horse_weight", "馬体重(増減)": "horse_weight",
    "オッズ": "odds", "単勝": "odds",
    "人気": "popularity",
}


def parse_shutuba(html: str, race_id: str) -> tuple[dict, pd.DataFrame]:
    """race.netkeiba.com/race/shutuba.html の HTML を (race_meta, entries_df) に変換。"""
    soup = BeautifulSoup(html, "lxml")
    race_meta = _parse_shutuba_meta(soup, race_id)

    table = soup.select_one("table.Shutuba_Table") or soup.find("table")
    if table is None:
        from ..schema import ENTRY_COLUMNS
        return race_meta, pd.DataFrame(columns=ENTRY_COLUMNS)

    # ヘッダ（Shutuba は th が複数行のことがあるので素直に最初の tr を使う）
    keys = []
    head = table.find("tr")
    for c in head.find_all(["th", "td"]):
        raw = re.sub(r"\s+", "", _text(c))
        keys.append(_SHUTUBA_HEADER_MAP.get(raw, raw))

    rows = []
    for tr in table.find_all("tr"):
        if "HorseList" in (tr.get("class") or []) or tr.find_all("td"):
            cells = tr.find_all("td")
            if not cells:
                continue
            # ヘッダ行を除外
            if tr.find("th") and not cells:
                continue
            cmap = dict(zip(keys, cells))
            if "horse_name" not in cmap:
                continue
            rows.append(_shutuba_row(cmap, race_id))

    df = pd.DataFrame(rows)
    df = _finalize_columns(df, kind="entry")
    race_meta["n_horses"] = len(df)
    return race_meta, df


def _shutuba_row(cmap: dict, race_id: str) -> dict:
    sex, age = parse_sex_age(_text(cmap.get("sex_age")))
    hw, _ = parse_horse_weight(_text(cmap.get("horse_weight")))
    name_cell = cmap.get("horse_name")
    return {
        "race_id": race_id,
        "horse_id": _first_link_id(name_cell, "horse"),
        "horse_name": _text(name_cell),
        "frame_no": _to_int(_text(cmap.get("frame_no"))),
        "horse_no": _to_int(_text(cmap.get("horse_no"))),
        "sex": sex,
        "age": age,
        "impost": _to_float(_text(cmap.get("impost"))),
        "jockey": _text(cmap.get("jockey")),
        "jockey_id": _first_link_id(cmap.get("jockey"), "jockey"),
        "trainer": _text(cmap.get("trainer")),
        "odds": _to_float(_text(cmap.get("odds"))),
        "popularity": _to_int(_text(cmap.get("popularity"))),
        "horse_weight": hw,
    }


def _parse_shutuba_meta(soup: BeautifulSoup, race_id: str) -> dict:
    meta = {"race_id": race_id, "race_name": None, "date": None, "track": None,
            "surface": None, "distance": np.nan, "direction": None,
            "going": None, "weather": None, "grade": None, "n_horses": np.nan}
    name = soup.select_one(".RaceName") or soup.find("h1")
    if name:
        meta["race_name"] = _text(name)
    data01 = soup.select_one(".RaceData01")
    if data01:
        _fill_course(meta, _text(data01))
    # RaceData02 に競馬場やクラスが入る
    data02 = soup.select_one(".RaceData02")
    if data02:
        spans = [_text(s) for s in data02.find_all("span")]
        for s in spans:
            if any(k in s for k in ["東京", "中山", "京都", "阪神", "中京", "札幌",
                                    "函館", "福島", "新潟", "小倉"]):
                meta["track"] = s
                break
    return meta


# --- 競走馬ページ（血統） ----------------------------------------------------

def parse_horse_profile(html: str, horse_id: str) -> dict:
    """db.netkeiba.com/horse/{horse_id}/ から血統等の基本情報を抽出。"""
    soup = BeautifulSoup(html, "lxml")
    prof = {"horse_id": horse_id, "horse_name": None, "sex": None,
            "birth_year": np.nan, "sire": None, "dam": None, "dam_sire": None,
            "trainer": None}

    title = soup.select_one(".horse_title h1") or soup.find("h1")
    if title:
        prof["horse_name"] = _text(title)

    # プロフィール表（生年月日・調教師など）
    for tr in soup.select(".db_prof_table tr, table.db_prof_table tr"):
        th = _text(tr.find("th"))
        td = _text(tr.find("td"))
        if "生年月日" in th:
            ym = re.search(r"(\d{4})年", td)
            if ym:
                prof["birth_year"] = int(ym.group(1))
        elif "調教師" in th:
            prof["trainer"] = td

    # 血統表（父・母・母父）
    ped = soup.select_one("table.blood_table")
    if ped:
        links = [a for a in ped.find_all("a", href=True) if "/horse/ped/" in a["href"] or "/horse/" in a["href"]]
        names = [_text(a) for a in links]
        # blood_table は 父, 父父, 父母, 母, 母父, 母母 ... の順で並ぶ
        if len(names) >= 1:
            prof["sire"] = names[0]
        if len(names) >= 4:
            prof["dam"] = names[3]
        if len(names) >= 5:
            prof["dam_sire"] = names[4]
    return prof


# --- 競走馬ページの戦績表を直接パース（確実な収集方式） ---------------------
# db.netkeiba.com/horse/{id}/ の戦績表(.db_h_race_results)には、その馬の全成績が
# 1 行 1 レースで載っている。race_id を抽出して別ページに飛ぶのではなく、この表を
# 直接パースすることで「取りこぼし」も「結果ページ取得の失敗」も避けられる。

# 戦績表のヘッダ → 内部キー。
# 実物の db.netkeiba.com/horse/result/{id}/ のヘッダ例:
#   日付 開催 天気 R レース名 映像 頭数 枠番 馬番 オッズ 人気 着順 騎手 斤量
#   距離 水分量 馬場 馬場指数 タイム 着差 ﾀｲﾑ指数 ... 通過 ペース 上り 馬体重 賞金
# ヘッダ文字列で列対応するので、列順が違っても見出しが合えば正しく取れる。
_HORSE_RESULT_HEADER_MAP = {
    "日付": "date",
    "開催": "venue",
    "天気": "weather",
    "R": "race_no",
    "レース名": "race_name",
    "映像": "_skip",
    "頭数": "n_horses",
    "枠番": "frame_no", "枠": "frame_no",
    "馬番": "horse_no",
    "オッズ": "odds",
    "人気": "popularity",
    "着順": "finish_pos",
    "騎手": "jockey",
    "斤量": "impost",
    "距離": "distance_raw",
    "水分量": "moisture",
    "馬場": "going",
    "馬場指数": "track_index",
    "タイム": "time",
    "着差": "margin",
    "通過": "passing",
    "ペース": "pace",
    "上り": "last_3f", "上がり": "last_3f",
    "馬体重": "horse_weight",
    "賞金": "prize",
}


def parse_horse_results(html: str, horse_id: str) -> pd.DataFrame:
    """競走馬ページの戦績表から、その馬の全成績を RESULT_COLUMNS 準拠で返す。

    別ページに飛ばず、この 1 ページだけでその馬の全レースを取得できる。
    race_id は各行のレース名リンクから取り出す（取れなくても行自体は残す）。
    """
    from ..schema import RESULT_COLUMNS
    soup = BeautifulSoup(html, "lxml")
    name_title = soup.select_one(".horse_title h1") or soup.find("h1")
    horse_name = _text(name_title)

    table = (soup.select_one("table.db_h_race_results")
             or soup.select_one("table.race_results")
             or _find_results_table(soup))
    if table is None:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    # ヘッダ行（最初の tr）から列キーを作る
    head = table.find("tr")
    keys = []
    for c in head.find_all(["th", "td"]):
        raw = re.sub(r"\s+", "", _text(c))
        keys.append(_HORSE_RESULT_HEADER_MAP.get(raw, raw))

    rows = []
    for tr in table.find_all("tr")[1:]:
        cells = tr.find_all("td")
        if not cells:
            continue
        cmap = dict(zip(keys, cells))
        if "race_name" not in cmap and "finish_pos" not in cmap:
            continue
        rows.append(_horse_result_row(cmap, horse_id, horse_name))

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)
    for c in RESULT_COLUMNS:
        if c not in df.columns:
            df[c] = np.nan
    return df[RESULT_COLUMNS]


def _find_results_table(soup):
    """class 名が違っても、ヘッダに『日付』『着順』を含む表を戦績表とみなす。"""
    for tbl in soup.find_all("table"):
        head = tbl.find("tr")
        if not head:
            continue
        htext = _text(head)
        if "日付" in htext and ("着順" in htext or "着 順" in htext):
            return tbl
    return None


def _horse_result_row(cmap: dict, horse_id: str, horse_name: str) -> dict:
    hw, wd = parse_horse_weight(_text(cmap.get("horse_weight")))
    dist, surface = _parse_distance_raw(_text(cmap.get("distance_raw")))
    # race_id はレース名セルのリンクから
    race_id = None
    name_cell = cmap.get("race_name")
    if name_cell is not None:
        for a in name_cell.find_all("a", href=True):
            m = re.search(r"(\d{11,12})", a["href"])
            if m:
                race_id = m.group(1)
                break
    return {
        "race_id": race_id,
        "horse_id": horse_id,
        "horse_name": horse_name,
        "finish_pos": _to_int(_text(cmap.get("finish_pos"))),
        "frame_no": _to_int(_text(cmap.get("frame_no"))),
        "horse_no": _to_int(_text(cmap.get("horse_no"))),
        "sex": None,
        "age": np.nan,
        "impost": _to_float(_text(cmap.get("impost"))),
        "jockey": _text(cmap.get("jockey")),
        "jockey_id": _first_link_id(cmap.get("jockey"), "jockey"),
        "time_sec": parse_time_to_sec(_text(cmap.get("time"))),
        "margin": _text(cmap.get("margin")) or None,
        "passing": _text(cmap.get("passing")) or None,
        "last_3f": _to_float(_text(cmap.get("last_3f"))),
        "odds": _to_float(_text(cmap.get("odds"))),
        "popularity": _to_int(_text(cmap.get("popularity"))),
        "horse_weight": hw,
        "weight_diff": wd,
        "trainer": None,
        "trainer_id": None,
        "prize": _parse_prize(_text(cmap.get("prize"))),
        # レース条件（races テーブルに展開する用に持っておく）
        "_date": _text(cmap.get("date")) or None,
        "_race_name": _text(cmap.get("race_name")) or None,
        "_distance": dist,
        "_surface": surface,
        "_going": _text(cmap.get("going")) or None,
        "_n_horses": _to_int(_text(cmap.get("n_horses"))),
    }


def _horse_results_meta_rows(html: str, horse_id: str) -> list[dict]:
    """競走馬ページの戦績表から、各レースのメタ情報（race_id, 日付, 距離, 馬場,
    レース名, グレード, 競馬場, 頭数）を行ごとに返す。races テーブル構築に使う。"""
    soup = BeautifulSoup(html, "lxml")
    table = (soup.select_one("table.db_h_race_results")
             or soup.select_one("table.race_results")
             or _find_results_table(soup))
    if table is None:
        return []
    head = table.find("tr")
    keys = []
    for c in head.find_all(["th", "td"]):
        raw = re.sub(r"\s+", "", _text(c))
        keys.append(_HORSE_RESULT_HEADER_MAP.get(raw, raw))

    out = []
    for tr in table.find_all("tr")[1:]:
        cells = tr.find_all("td")
        if not cells:
            continue
        cmap = dict(zip(keys, cells))
        name_cell = cmap.get("race_name")
        rid = None
        race_name = _text(name_cell)
        if name_cell is not None:
            for a in name_cell.find_all("a", href=True):
                m = re.search(r"(\d{11,12})", a["href"])
                if m:
                    rid = m.group(1)
                    break
        dist, surface = _parse_distance_raw(_text(cmap.get("distance_raw")))
        # 日付を YYYY-MM-DD に正規化（YYYY/MM/DD 形式）
        date = _normalize_date(_text(cmap.get("date")))
        # グレードはレース名から、競馬場は開催セルから
        grade = _grade_from_name(race_name)
        venue = _text(cmap.get("venue"))
        track = _track_from_venue(venue)
        out.append({
            "race_id": rid, "date": date, "race_name": race_name,
            "distance": dist, "surface": surface,
            "going": _text(cmap.get("going")) or None,
            "grade": grade, "track": track,
            "n_horses": _to_int(_text(cmap.get("n_horses"))),
        })
    return out


def _normalize_date(text: str):
    text = (text or "").strip()
    m = re.match(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return text or None


def _grade_from_name(name: str):
    if not name:
        return None
    m = re.search(r"\((G[1-3])\)|（(G[1-3])）|(G[ⅠⅡⅢ])", name)
    if m:
        g = next((x for x in m.groups() if x), None)
        return (g or "").replace("Ⅰ", "1").replace("Ⅱ", "2").replace("Ⅲ", "3")
    return None


_VENUE_TRACKS = ["札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都",
                 "阪神", "小倉"]


def _track_from_venue(venue: str):
    if not venue:
        return None
    for t in _VENUE_TRACKS:
        if t in venue:
            return t
    return None


def _parse_distance_raw(text: str):
    """'芝2400' / 'ダ1800' / '障3000' を (距離, 馬場種別) に。"""
    text = (text or "").strip()
    m = re.search(r"(障?[芝ダ])\s*(\d+)", text)
    if not m:
        return (np.nan, None)
    surf = {"芝": "芝", "ダ": "ダート"}.get(m.group(1)[-1], m.group(1))
    if m.group(1).startswith("障"):
        surf = "障害"
    return (int(m.group(2)), surf)


# --- 仕上げ ------------------------------------------------------------------

def _finalize_columns(df: pd.DataFrame, kind: str) -> pd.DataFrame:
    from ..schema import ENTRY_COLUMNS, RESULT_COLUMNS
    cols = RESULT_COLUMNS if kind == "result" else ENTRY_COLUMNS
    if df.empty:
        return pd.DataFrame(columns=cols)
    for c in cols:
        if c not in df.columns:
            df[c] = np.nan
    return df[cols]
