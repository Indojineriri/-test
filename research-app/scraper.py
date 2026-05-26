from __future__ import annotations

import re
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (compatible; research-app/1.0)"
HEADERS = {"User-Agent": UA, "Accept": "text/html,*/*"}


def _fetch(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def find_og_image(page_url: str) -> str | None:
    """Return the og:image (or twitter:image) URL for a page, or None."""
    try:
        html = _fetch(page_url, timeout=15).decode("utf-8", errors="ignore")
    except Exception:
        return None

    patterns = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
    ]
    for pat in patterns:
        m = re.search(pat, html, flags=re.IGNORECASE)
        if m:
            return urllib.parse.urljoin(page_url, m.group(1))

    # arXiv abstract pages do not expose og:image; fall back to the first PDF figure
    # is too brittle, so leave it to the caller.
    return None


def download_image(url: str, max_bytes: int = 8_000_000) -> tuple[bytes, str] | None:
    """Download an image. Returns (bytes, content_type) or None on failure."""
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=20) as resp:
            ct = resp.headers.get("Content-Type", "image/png").split(";")[0].strip()
            data = resp.read(max_bytes + 1)
            if len(data) > max_bytes:
                return None
            if not ct.startswith("image/"):
                return None
            return data, ct
    except Exception:
        return None
