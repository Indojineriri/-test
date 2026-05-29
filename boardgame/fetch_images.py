"""Fetch official game images from BoardGameGeek and write them into games.json.

Why a separate script: each game already stores its BGG page URL, which encodes
the BGG thing-id. The BGG XML API2 returns the canonical, hot-linkable image URL
(on cf.geekdo-images.com) for each id. We read those and fill in `image_url`.

IMPORTANT — network access:
This must run from a host that can reach BoardGameGeek. The cloud dev/CI sandbox
blocks outbound hosts (allowlist), so run this on your own machine or GCP VM:

    cd boardgame
    pip install requests
    python fetch_images.py            # fills empty image_url for all games
    python fetch_images.py --force    # overwrite existing image_url too
    python fetch_images.py --only カタン ドミニオン   # specific titles

Then re-seed / redeploy so the new URLs show up. BGG asks for polite use, so we
sleep between requests. Existing image_url values are kept unless --force.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

DATA = os.path.join(os.path.dirname(__file__), "data", "games.json")
API = "https://boardgamegeek.com/xmlapi2/thing?id={id}"
# Be a good citizen: identify ourselves and don't hammer the API.
HEADERS = {"User-Agent": "boardgame-catalog/1.0 (image fetch script)"}
DELAY_SEC = 2.0


def bgg_id(url: str) -> str | None:
    m = re.search(r"/boardgame/(\d+)", url or "")
    return m.group(1) if m else None


def fetch_image_url(session, thing_id: str) -> str | None:
    """Return the <image> URL for a BGG thing id, or None."""
    resp = session.get(API.format(id=thing_id), headers=HEADERS, timeout=20)
    if resp.status_code == 202:
        # BGG sometimes queues the request; wait and retry once.
        time.sleep(3)
        resp = session.get(API.format(id=thing_id), headers=HEADERS, timeout=20)
    resp.raise_for_status()
    m = re.search(r"<image>(.*?)</image>", resp.text, re.DOTALL)
    if not m:
        return None
    url = m.group(1).strip()
    # API may emit protocol-relative URLs (//cf.geekdo-images.com/...).
    if url.startswith("//"):
        url = "https:" + url
    return url or None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true",
                        help="overwrite games that already have an image_url")
    parser.add_argument("--only", nargs="*", default=None,
                        help="only update games whose name contains these terms")
    args = parser.parse_args()

    try:
        import requests
    except ImportError:
        print("Please `pip install requests` first.", file=sys.stderr)
        return 1

    with open(DATA, encoding="utf-8") as f:
        games = json.load(f)

    session = requests.Session()
    updated = skipped = failed = 0

    for g in games:
        name = g["name"]
        if args.only and not any(term in name for term in args.only):
            continue
        if g.get("image_url") and not args.force:
            skipped += 1
            continue
        tid = bgg_id(g.get("bgg_url", ""))
        if not tid:
            print(f"  - skip (no BGG id): {name}")
            skipped += 1
            continue
        try:
            url = fetch_image_url(session, tid)
            if url:
                g["image_url"] = url
                updated += 1
                print(f"  ✓ {name}: {url}")
            else:
                failed += 1
                print(f"  ! no <image> for {name} (id {tid})")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ! failed {name} (id {tid}): {e}")
        time.sleep(DELAY_SEC)

    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(games, f, ensure_ascii=False, indent=2)

    print(f"\nDone. updated={updated} skipped={skipped} failed={failed}")
    print("Next: `python seed.py` (or redeploy) to load the new image URLs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
