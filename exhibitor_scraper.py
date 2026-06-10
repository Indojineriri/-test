"""出展企業データの取得段（RTJ / robot-technology.jp）。

公式の出展企業検索は AJAX でリスト描画される一方、各社の詳細ページは
``/visitor/Information/index/ChargeNo=N`` という連番 URL で配信されている。
本モジュールは ChargeNo を 1 から順に巡回し、各ページを requests + BeautifulSoup
で取得・解析して、後段の Claude 判定が使える構造化データに落とす。

設計方針:
- フィールド単位の解析（社名・カテゴリ・会社URL）はベストエフォート。
- どのページでも「整形済みの可視テキスト全文（raw_text）」を必ず保存する。
  後段の Claude はこの raw_text を主たる判定材料にするため、HTML 構造が
  多少変わっても抽出品質が崩れにくい。
- 連続して「社名の取れないページ」が一定数続いたら末尾と判断して停止する。

注意: 実行にはサイトへの外向き通信が必要。サンドボックス等で通信が
許可リスト制限されている環境では動かないため、ネットワーク制限のない
ローカル環境で実行すること。

CLI:
    python exhibitor_scraper.py --out exhibitors.json --max 800 --delay 0.7
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, asdict

import requests
from bs4 import BeautifulSoup

DETAIL_URL = "https://robot-technology.jp/visitor/Information/index/ChargeNo={n}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.9",
}

# タイトル末尾の定型サフィックス（社名抽出時に落とす）。
_TITLE_NOISE = re.compile(r"(出展企業情報|ロボットテクノロジージャパン|RTJ\d{0,4}|愛知のロボット展示会)")


@dataclass
class Exhibitor:
    charge_no: int
    name: str
    url: str  # 詳細ページ URL
    website: str | None  # 会社の外部サイト（取れれば）
    categories: list[str]  # 製品分類（取れれば）
    raw_text: str  # 整形済み可視テキスト全文（Claude 判定の主材料）


def _clean_text(soup: BeautifulSoup) -> str:
    """ナビ/スクリプト等を除いた可視テキストを改行区切りで返す。"""
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    # 連続空行を畳む。
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines)


def _extract_name(soup: BeautifulSoup) -> str | None:
    """社名をベストエフォートで抽出。h1 → title の順。"""
    for sel in ("h1", "h2.company-name", ".company-name", "h2"):
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            cand = el.get_text(strip=True)
            if not _TITLE_NOISE.search(cand):
                return cand
    if soup.title and soup.title.get_text(strip=True):
        # 例: "コスメック｜出展企業情報｜ロボットテクノロジージャパン2026 …"
        head = re.split(r"[｜|│]", soup.title.get_text(strip=True))[0].strip()
        if head and not _TITLE_NOISE.search(head):
            return head
    return None


def _extract_website(soup: BeautifulSoup) -> str | None:
    """robot-technology.jp 以外への最初の外部リンクを会社サイトとみなす。"""
    for a in soup.select("a[href^=http]"):
        href = a.get("href", "")
        if "robot-technology.jp" in href:
            continue
        if any(s in href for s in ("twitter.com", "x.com", "facebook.com", "youtube", "instagram", "linkedin")):
            continue
        return href
    return None


def _extract_categories(soup: BeautifulSoup) -> list[str]:
    """製品分類ラベルをベストエフォートで収集（取れなくても可）。"""
    cats: list[str] = []
    # 「製品分類」「出展対象」等の見出しに続く li / a を拾う想定。
    for label in soup.find_all(string=re.compile(r"製品(分類|カテゴリ)|出展対象")):
        container = label.find_parent()
        if not container:
            continue
        for li in container.find_all_next("li", limit=40):
            t = li.get_text(strip=True)
            if t and len(t) < 40:
                cats.append(t)
        break
    # 重複除去（順序保持）。
    seen: set[str] = set()
    return [c for c in cats if not (c in seen or seen.add(c))]


def parse_exhibitor(html: str, charge_no: int, url: str) -> Exhibitor | None:
    """1 ページ分の HTML を解析。社名が取れなければ None（=該当なし）。"""
    soup = BeautifulSoup(html, "html.parser")
    name = _extract_name(soup)
    if not name:
        return None
    return Exhibitor(
        charge_no=charge_no,
        name=name,
        url=url,
        website=_extract_website(soup),
        categories=_extract_categories(soup),
        raw_text=_clean_text(soup),
    )


def fetch_one(session: requests.Session, charge_no: int, timeout: int = 20) -> Exhibitor | None:
    url = DETAIL_URL.format(n=charge_no)
    resp = session.get(url, timeout=timeout)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or resp.encoding
    return parse_exhibitor(resp.text, charge_no, url)


def scrape(
    max_charge_no: int = 800,
    delay: float = 0.7,
    stop_after_misses: int = 25,
    progress=None,
) -> list[Exhibitor]:
    """ChargeNo=1..max を巡回。連続 miss が閾値に達したら早期終了。

    progress: callable(done:int, total:int, found:int) を渡すと進捗通知する
    （Streamlit 等の UI 用）。
    """
    session = requests.Session()
    session.headers.update(HEADERS)
    out: list[Exhibitor] = []
    misses = 0
    for n in range(1, max_charge_no + 1):
        try:
            ex = fetch_one(session, n)
        except requests.RequestException as e:
            print(f"[warn] ChargeNo={n} 取得失敗: {e}", file=sys.stderr)
            ex = None
        if ex:
            out.append(ex)
            misses = 0
        else:
            misses += 1
        if progress:
            progress(n, max_charge_no, len(out))
        if misses >= stop_after_misses:
            print(
                f"[info] ChargeNo={n} までで連続 {misses} 件 miss。末尾と判断し停止。",
                file=sys.stderr,
            )
            break
        time.sleep(delay)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="RTJ 出展企業データ取得（ChargeNo 連番巡回）")
    ap.add_argument("--out", default="exhibitors.json", help="出力 JSON パス")
    ap.add_argument("--max", type=int, default=800, help="巡回する最大 ChargeNo")
    ap.add_argument("--delay", type=float, default=0.7, help="リクエスト間の待機秒数")
    ap.add_argument(
        "--stop-after-misses", type=int, default=25, help="連続 miss でこの数に達したら停止"
    )
    args = ap.parse_args()

    items = scrape(args.max, args.delay, args.stop_after_misses)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump([asdict(x) for x in items], f, ensure_ascii=False, indent=2)
    print(f"取得 {len(items)} 社 → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
