"""Bulk-set rules_url (and optionally image_url) on games from a simple mapping.

When you've gathered rule-explanation links (e.g. from Claude research), drop
them into a JSON file mapping game name -> URL, then run this to write them into
data/games.json. Re-seeding/redeploy then exposes them as the
「🔗 詳しいルール解説を見る」 link.

    # links.json example:
    # { "カタン": "https://...", "ドミニオン": "https://..." }
    python set_links.py links.json                 # sets rules_url
    python set_links.py links.json --field image_url

Names must match games.json exactly. Unknown names are reported and skipped.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

DATA = os.path.join(os.path.dirname(__file__), "data", "games.json")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mapping", help="JSON file: {game name: url}")
    parser.add_argument("--field", default="rules_url",
                        choices=["rules_url", "image_url", "video_url"],
                        help="which field to set (default: rules_url)")
    args = parser.parse_args()

    with open(args.mapping, encoding="utf-8") as f:
        mapping = json.load(f)
    with open(DATA, encoding="utf-8") as f:
        games = json.load(f)

    by_name = {g["name"]: g for g in games}
    set_n = 0
    for name, url in mapping.items():
        game = by_name.get(name)
        if game is None:
            print(f"  ! not found, skipped: {name}", file=sys.stderr)
            continue
        game[args.field] = url
        set_n += 1
        print(f"  ✓ {name} -> {url}")

    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(games, f, ensure_ascii=False, indent=2)
    print(f"\nDone. {set_n} games updated ({args.field}).")
    print("Next: `python seed.py` (local) or redeploy to apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
