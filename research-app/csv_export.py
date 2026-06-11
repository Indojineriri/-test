from __future__ import annotations

import csv
import io

from models import Case, VendorCase

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


VENDOR_CSV_COLUMNS = [
    "company",
    "product",
    "focus_tech",
    "summary",
    "features",
    "problems_solved",
    "use_cases",
    "url",
    "image_url",
]


def vendors_to_csv_bytes(vendors: list[VendorCase]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=VENDOR_CSV_COLUMNS, quoting=csv.QUOTE_ALL)
    writer.writeheader()
    for v in vendors:
        writer.writerow(
            {
                "company": v.company,
                "product": v.product,
                "focus_tech": v.focus_tech or "",
                "summary": v.summary,
                "features": "\n".join(f"・{x}" for x in v.features),
                "problems_solved": "\n".join(f"・{x}" for x in v.problems_solved),
                "use_cases": "\n".join(f"・{x}" for x in v.use_cases),
                "url": v.url,
                "image_url": v.image_url or "",
            }
        )
    return ("﻿" + buf.getvalue()).encode("utf-8")
