from __future__ import annotations

import csv
import io

from models import Case

CSV_COLUMNS = [
    "title",
    "organization",
    "year",
    "subtitle",
    "overview",
    "challenges",
    "solutions",
    "url",
    "image_url",
]


def to_csv_bytes(cases: list[Case]) -> bytes:
    """Render the case list to CSV (UTF-8 with BOM for Excel compatibility)."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, quoting=csv.QUOTE_ALL)
    writer.writeheader()
    for c in cases:
        writer.writerow(
            {
                "title": c.title,
                "organization": c.organization,
                "year": c.year if c.year is not None else "",
                "subtitle": c.subtitle,
                "overview": "\n".join(f"・{x}" for x in c.overview),
                "challenges": "\n".join(f"・{x}" for x in c.challenges),
                "solutions": "\n".join(f"・{x}" for x in c.solutions),
                "url": c.url,
                "image_url": c.image_url or "",
            }
        )
    # BOM so Excel opens UTF-8 cleanly.
    return ("﻿" + buf.getvalue()).encode("utf-8")
