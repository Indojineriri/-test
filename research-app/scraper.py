from __future__ import annotations

import io
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


# ---------------------------------------------------------------------------
# arXiv-specific PDF figure extraction
# ---------------------------------------------------------------------------

ARXIV_ABS_RE = re.compile(r"https?://arxiv\.org/abs/([\w./-]+?)/?$")
ARXIV_PDF_RE = re.compile(r"https?://arxiv\.org/pdf/([\w./-]+?)(?:\.pdf)?/?$")


def _to_arxiv_pdf_url(url: str) -> str | None:
    m = ARXIV_ABS_RE.match(url)
    if m:
        return f"https://arxiv.org/pdf/{m.group(1)}.pdf"
    m = ARXIV_PDF_RE.match(url)
    if m:
        return f"https://arxiv.org/pdf/{m.group(1)}.pdf"
    return None


def _extract_first_pdf_figure(
    pdf_url: str,
    max_pages: int = 4,
    min_pixels: int = 200 * 150,
) -> tuple[bytes, str] | None:
    """Download a PDF and extract the largest embedded image from the first few
    pages. Skips tiny logos/icons by enforcing min_pixels.

    Returns (image_bytes, content_type) or None.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return None

    try:
        req = urllib.request.Request(pdf_url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            pdf_bytes = resp.read(30_000_000)
    except Exception:
        return None

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return None

    candidates: list[tuple[int, bytes, str]] = []  # (pixels, bytes, ext)
    try:
        for page_idx in range(min(len(doc), max_pages)):
            page = doc.load_page(page_idx)
            for img in page.get_images(full=True):
                xref = img[0]
                try:
                    base = doc.extract_image(xref)
                except Exception:
                    continue
                width = base.get("width", 0)
                height = base.get("height", 0)
                if width * height < min_pixels:
                    continue
                ext = base.get("ext", "png").lower()
                if ext == "jpx":
                    # JPEG 2000 is poorly supported by PowerPoint; skip.
                    continue
                candidates.append((width * height, base["image"], ext))
    finally:
        doc.close()

    if not candidates:
        return None

    # Pick the biggest figure (usually the teaser).
    candidates.sort(key=lambda x: -x[0])
    _, blob, ext = candidates[0]
    mime = {"jpeg": "image/jpeg", "jpg": "image/jpeg", "png": "image/png"}.get(
        ext, f"image/{ext}"
    )
    return blob, mime


def _render_pdf_first_page(pdf_url: str) -> tuple[bytes, str] | None:
    """Last-resort: render PDF page 1 as a PNG."""
    try:
        import fitz
    except ImportError:
        return None
    try:
        req = urllib.request.Request(pdf_url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            pdf_bytes = resp.read(30_000_000)
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc.load_page(0)
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        png = pix.tobytes("png")
        doc.close()
        return png, "image/png"
    except Exception:
        return None


def get_case_image(
    url: str,
    image_url: str | None = None,
    trace: list[str] | None = None,
) -> tuple[bytes, str] | None:
    """Best-effort image for a case. Priority:

    1. explicit `image_url` if provided
    2. og:image / twitter:image on the page
    3. for arXiv URLs: biggest figure embedded in the PDF
    4. for arXiv URLs: page 1 rendered as PNG (fallback)

    If `trace` is provided, append a short status line for each attempt.
    """
    def log(msg: str) -> None:
        if trace is not None:
            trace.append(msg)

    if image_url:
        got = download_image(image_url)
        if got is not None:
            log(f"image_url OK ({len(got[0])} bytes)")
            return got
        log(f"image_url failed ({image_url})")

    og = find_og_image(url)
    if og:
        got = download_image(og)
        if got is not None:
            log(f"og:image OK ({og})")
            return got
        log(f"og:image found but download failed ({og})")
    else:
        log(f"og:image not found on {url}")

    pdf_url = _to_arxiv_pdf_url(url)
    if pdf_url:
        log(f"trying arXiv PDF: {pdf_url}")
        got = _extract_first_pdf_figure(pdf_url)
        if got is not None:
            log(f"PDF embedded figure OK ({len(got[0])} bytes)")
            return got
        log("no embedded figure ≥200x150; rendering page 1")
        got = _render_pdf_first_page(pdf_url)
        if got is not None:
            log(f"PDF page 1 render OK ({len(got[0])} bytes)")
            return got
        log("PDF page 1 render failed")
    else:
        log(f"url is not arXiv (no PDF fallback)")

    return None
