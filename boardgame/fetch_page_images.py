"""Resolve game images from Wikipedia / BGG *page* URLs into image_url.

The links in image_pages.json point at article pages (Wikipedia / BGG /
bodoge.hoobby.net), not at image files, so they can't be used directly in an
<img> tag. This script extracts the representative image for each page and
writes the direct image URL into data/games.json (image_url).

Resolution per host:
  - en.wikipedia.org : MediaWiki API `pageimages` -> original image URL.
  - boardgamegeek.com: BGG XML API2 `thing` -> <image> URL.
  - bodoge.hoobby.net: scrape the og:image meta tag.

Run on a machine WITH internet (the cloud sandbox blocks outbound hosts):

    cd boardgame
    pip install requests
    python fetch_page_images.py                 # fill empty image_url
    python fetch_page_images.py --force         # overwrite existing too
    python fetch_page_images.py --only カタン ドミニオン

Then `python seed.py` (local) or redeploy to apply. Be polite: we sleep between
requests. Unresolved entries are left empty and reported.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from urllib.parse import unquote, urlparse

DATA = os.path.join(os.path.dirname(__file__), "data", "games.json")
PAGES = os.path.join(os.path.dirname(__file__), "image_pages.json")
HEADERS = {"User-Agent": "boardgame-catalog/1.0 (image resolver)"}
DELAY_SEC = 1.5


def _wikipedia_image(session, page_url: str) -> str | None:
    title = unquote(urlparse(page_url).path.rsplit("/", 1)[-1])
    api = "https://en.wikipedia.org/w/api.php"
    params = {
        "action": "query", "format": "json", "prop": "pageimages",
        "piprop": "original|thumbnail", "pithumbsize": "600", "titles": title,
    }
    r = session.get(api, params=params, headers=HEADERS, timeout=20)
    r.raise_for_status()
    pages = r.json().get("query", {}).get("pages", {})
    for p in pages.values():
        if "original" in p:
            return p["original"]["source"]
        if "thumbnail" in p:
            return p["thumbnail"]["source"]
    return None


def _bgg_image(session, page_url: str) -> str | None:
    m = re.search(r"/boardgame/(\d+)", page_url)
    if not m:
        return None
    api = f"https://boardgamegeek.com/xmlapi2/thing?id={m.group(1)}"
    r = session.get(api, headers=HEADERS, timeout=20)
    if r.status_code == 202:
        time.sleep(3)
        r = session.get(api, headers=HEADERS, timeout=20)
    r.raise_for_status()
    m2 = re.search(r"<image>(.*?)</image>", r.text, re.DOTALL)
    if not m2:
        return None
    url = m2.group(1).strip()
    return ("https:" + url) if url.startswith("//") else (url or None)


def _og_image(session, page_url: str) -> str | None:
    r = session.get(page_url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    m = re.search(
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
        r.text,
    )
    return m.group(1) if m else None


def resolve(session, page_url: str) -> str | None:
    host = urlparse(page_url).netloc
    if "wikipedia.org" in host:
        return _wikipedia_image(session, page_url)
    if "boardgamegeek.com" in host:
        return _bgg_image(session, page_url)
    return _og_image(session, page_url)  # bodoge etc.


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--only", nargs="*", default=None)
    args = parser.parse_args()

    try:
        import requests
    except ImportError:
        print("Please `pip install requests` first.", file=sys.stderr)
        return 1

    with open(PAGES, encoding="utf-8") as f:
        page_map = json.load(f)
    with open(DATA, encoding="utf-8") as f:
        games = json.load(f)
    by_name = {g["name"]: g for g in games}

    session = requests.Session()
    ok = miss = skip = 0
    for name, page_url in page_map.items():
        if args.only and not any(t in name for t in args.only):
            continue
        game = by_name.get(name)
        if game is None:
            print(f"  ! not in games.json: {name}", file=sys.stderr)
            continue
        if game.get("image_url") and not args.force:
            skip += 1
            continue
        try:
            url = resolve(session, page_url)
            if url:
                game["image_url"] = url
                ok += 1
                print(f"  ✓ {name}: {url}")
            else:
                miss += 1
                print(f"  ! no image found: {name} ({page_url})")
        except Exception as e:  # noqa: BLE001
            miss += 1
            print(f"  ! failed {name}: {e}")
        time.sleep(DELAY_SEC)

    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(games, f, ensure_ascii=False, indent=2)
    print(f"\nDone. resolved={ok} missing={miss} skipped={skip}")
    print("Next: `python seed.py` (local) or redeploy to apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
