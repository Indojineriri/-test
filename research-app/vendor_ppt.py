"""Template-based slide generator for vendor research.

Mirrors the approach in ppt_export.py: open the supplied .pptx template,
fill the first case into the existing slide, then clone the pristine
slide XML (with rId remapping) for subsequent cases.
"""

from __future__ import annotations

import io
from copy import deepcopy
from pathlib import Path

from pptx import Presentation
from pptx.enum.text import MSO_AUTO_SIZE
from pptx.util import Pt

from models import VendorCase
import scraper

TEMPLATE_PATH = Path(__file__).parent / "templates" / "vendor_template.pptx"

# Shape names in the template (Japanese; do not rename in the .pptx).
SHAPE_TITLE = "タイトル 2"          # 各出展者紹介-{社名}-
SHAPE_SUBTITLE = "字幕 1"           # {社名}の「{製品名}」は{summary}
SHAPE_COMPANY = "正方形/長方形 7"   # 出展者の値
SHAPE_PRODUCT = "正方形/長方形 9"   # 製品名の値
SHAPE_FEATURES = "正方形/長方形 28"   # 製品の特長
SHAPE_PROBLEMS = "正方形/長方形 21"   # 解決する課題
SHAPE_USECASES = "正方形/長方形 24"   # 活用例
SHAPE_MEDIA = "IMG_0026"            # 製品画像（MEDIA type）

MAX_BULLETS = 3
BULLET_FONT_SIZE_PT = 12

R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"


def _find_shape(slide, name: str):
    """Find a shape by name, recursing into groups."""
    def walk(shapes):
        for shp in shapes:
            if shp.name == name:
                return shp
            if shp.shape_type == 6:  # GROUP
                try:
                    found = walk(shp.shapes)
                    if found is not None:
                        return found
                except Exception:
                    pass
        return None
    return walk(slide.shapes)


def _snapshot_template_runs(text_frame):
    if not text_frame.paragraphs:
        return None, None
    p0 = text_frame.paragraphs[0]
    pPr = deepcopy(p0._pPr) if p0._pPr is not None else None
    rPr = None
    if p0.runs:
        rPr_el = p0.runs[0]._r.find(f"{{{A_NS}}}rPr")
        if rPr_el is not None:
            rPr = deepcopy(rPr_el)
    return pPr, rPr


def _apply_rPr(run, template_rPr, font_size_pt: int | None = None) -> None:
    if template_rPr is None:
        if font_size_pt is not None:
            run.font.size = Pt(font_size_pt)
        return
    r = run._r
    existing = r.find(f"{{{A_NS}}}rPr")
    if existing is not None:
        r.remove(existing)
    rPr = deepcopy(template_rPr)
    if font_size_pt is not None:
        rPr.set("sz", str(font_size_pt * 100))
    r.insert(0, rPr)


def _set_single_text(shape, text: str) -> None:
    tf = shape.text_frame
    template_pPr, template_rPr = _snapshot_template_runs(tf)
    tf.clear()
    p = tf.paragraphs[0]
    if template_pPr is not None:
        existing = p._pPr
        if existing is not None:
            p._p.remove(existing)
        p._p.insert(0, deepcopy(template_pPr))
    run = p.add_run()
    run.text = text
    _apply_rPr(run, template_rPr)


def _set_bullets(shape, lines: list[str], font_size_pt: int = BULLET_FONT_SIZE_PT) -> None:
    """Rewrite the text frame to `lines` (truncated to MAX_BULLETS), preserving
    paragraph + run formatting from the template's first paragraph/run."""
    items = [s.strip() for s in (lines or []) if s and s.strip()][:MAX_BULLETS]
    if not items:
        items = ["(情報なし)"]

    tf = shape.text_frame
    template_pPr, template_rPr = _snapshot_template_runs(tf)

    tf.clear()
    for i, line in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        if template_pPr is not None:
            existing = p._pPr
            if existing is not None:
                p._p.remove(existing)
            p._p.insert(0, deepcopy(template_pPr))
        run = p.add_run()
        run.text = line
        _apply_rPr(run, template_rPr, font_size_pt=font_size_pt)

    try:
        tf.word_wrap = True
        tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    except Exception:
        pass


def _replace_media_with_picture(slide, media_shape, image_bytes: bytes) -> None:
    """Replace the template's MEDIA placeholder with a Picture at the same box.

    The template uses a MEDIA shape (video/animated) for the product slot. To
    swap in a still image we remove the MEDIA shape and drop a Picture at the
    same position and size.
    """
    left, top = media_shape.left, media_shape.top
    width, height = media_shape.width, media_shape.height
    media_shape.element.getparent().remove(media_shape.element)
    try:
        slide.shapes.add_picture(
            io.BytesIO(image_bytes), left, top, width=width, height=height
        )
    except Exception:
        pass


def _strip_timing(slide) -> None:
    """Remove <p:timing> from the slide.

    The template's timing block triggers animations on the original
    IMG_0026 media shape (spid 26). Once we rewrite the slide and
    swap that shape for a still picture, the timing references go
    stale and PowerPoint refuses to open the file. We don't need
    animations on the rebuilt slide either, so drop the whole block.
    """
    sld = slide.element
    for timing in sld.findall(f"{{{P_NS}}}timing"):
        sld.remove(timing)


def _fill_slide(slide, vendor: VendorCase) -> None:
    _strip_timing(slide)

    title = _find_shape(slide, SHAPE_TITLE)
    if title is not None:
        _set_single_text(title, f"各出展者紹介-{vendor.company}-")

    subtitle = _find_shape(slide, SHAPE_SUBTITLE)
    if subtitle is not None:
        _set_single_text(subtitle, f"{vendor.company}の「{vendor.product}」は{vendor.summary}")

    company = _find_shape(slide, SHAPE_COMPANY)
    if company is not None:
        _set_single_text(company, vendor.company)

    product = _find_shape(slide, SHAPE_PRODUCT)
    if product is not None:
        _set_single_text(product, vendor.product)

    features = _find_shape(slide, SHAPE_FEATURES)
    if features is not None:
        _set_bullets(features, vendor.features)

    problems = _find_shape(slide, SHAPE_PROBLEMS)
    if problems is not None:
        _set_bullets(problems, vendor.problems_solved)

    usecases = _find_shape(slide, SHAPE_USECASES)
    if usecases is not None:
        _set_bullets(usecases, vendor.use_cases)

    media = _find_shape(slide, SHAPE_MEDIA)
    if media is not None:
        img = scraper.get_case_image(vendor.url, vendor.image_url)
        if img is not None:
            _replace_media_with_picture(slide, media, img[0])
        else:
            # No image: remove the placeholder so the template's stale
            # media doesn't leak into every generated slide.
            media.element.getparent().remove(media.element)


def build_vendor_pptx(vendors: list[VendorCase], template_path: Path = TEMPLATE_PATH) -> bytes:
    """Build a single .pptx with one slide per vendor.

    The template's slide 1 is the canonical layout. Slide 2 (if any) is
    discarded so we don't carry over reviewer commentary.
    """
    if not vendors:
        raise ValueError("vendors must not be empty")

    prs = Presentation(str(template_path))

    # Drop everything except the first slide (templates may ship with
    # extra example slides we don't want in the output).
    sldIdLst = prs.slides._sldIdLst
    extras = list(sldIdLst)[1:]
    for sldId in extras:
        rId = sldId.get(f"{{{R_NS}}}id")
        prs.part.drop_rel(rId)
        sldIdLst.remove(sldId)

    src_slide = prs.slides[0]

    # Snapshot pristine shapes + rels before mutating the first slide.
    original_shape_xmls = [deepcopy(shp.element) for shp in src_slide.shapes]
    original_rels = {rId: rel for rId, rel in src_slide.part.rels.items()}

    _fill_slide(src_slide, vendors[0])

    rid_attrs = (f"{{{R_NS}}}embed", f"{{{R_NS}}}link", f"{{{R_NS}}}id")
    for vendor in vendors[1:]:
        new_slide = prs.slides.add_slide(src_slide.slide_layout)
        for shp in list(new_slide.shapes):
            shp.element.getparent().remove(shp.element)

        rid_map: dict[str, str] = {}
        for src_rId, rel in original_rels.items():
            if rel.is_external:
                new_rId = new_slide.part.relate_to(rel.target_ref, rel.reltype, is_external=True)
            else:
                new_rId = new_slide.part.relate_to(rel.target_part, rel.reltype)
            rid_map[src_rId] = new_rId

        for shp_xml in original_shape_xmls:
            new_el = deepcopy(shp_xml)
            for el in new_el.iter():
                for attr in rid_attrs:
                    if attr in el.attrib:
                        old = el.attrib[attr]
                        if old in rid_map:
                            el.attrib[attr] = rid_map[old]
            new_slide.shapes._spTree.append(new_el)

        _fill_slide(new_slide, vendor)

    bio = io.BytesIO()
    prs.save(bio)
    return bio.getvalue()
