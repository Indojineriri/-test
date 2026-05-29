"""Load data/games.json into the database.

Idempotent: games are matched by name, so re-running updates existing entries
and adds new ones rather than creating duplicates.

    python seed.py
"""
from __future__ import annotations

import json
import os

from app import app
from models import Game, db

DATA = os.path.join(os.path.dirname(__file__), "data", "games.json")


def run() -> None:
    with open(DATA, encoding="utf-8") as f:
        games = json.load(f)

    with app.app_context():
        db.create_all()
        added = updated = 0
        for entry in games:
            existing = Game.query.filter_by(name=entry["name"]).first()
            if existing:
                for k, v in entry.items():
                    setattr(existing, k, v)
                updated += 1
            else:
                db.session.add(Game(**entry))
                added += 1
        db.session.commit()
        total = Game.query.count()
        print(f"seed complete: +{added} added, {updated} updated, {total} total")


if __name__ == "__main__":
    run()
