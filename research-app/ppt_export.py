from __future__ import annotations

import io
from copy import deepcopy
from pathlib import Path

from pptx import Presentation
from pptx.enum.text import MSO_AUTO_SIZE
from pptx.util import Pt

from models import Case
import scraper

TEMPLATE_PATH = Path(__file__).parent / "templates" / "case_template.pptx"

# Fit constraints: the three content boxes in the template are ~1.5" tall;
# at 12pt with bullet spacing, 4 lines fit cleanly without overflow.
MAX_BULLETS_PER_SECTION = 4
BULLET_FONT_SIZE_PT = 12

# Shape names in the template (Japanese, as authored). Keep these stable.
SHAPE_TITLE = "タイトル 2"
SHAPE_SUBTITLE = "テキスト ボックス 5"
SHAPE_HEADLINE = "テキスト ボックス 73"  # inside グループ化 27
SHAPE_OVERVIEW = "正方形/長方形 117"
SHAPE_CHALLENGES = "正方形/長方形 118"
SHAPE_SOLUTIONS = "正方形/長方形 119"
SHAPE_PICTURE = "図 10"
SHAPE_LINK = "テキスト ボックス 12"

R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _find_shape(slide, name: str):
    """Find a shape by name, recursing into groups."""
    def _walk(shapes):
        for shp in shapes:
            if shp.name == name:
                return shp
            if shp.shape_type == 6:  # GROUP
                try:
                    found = _walk(shp.shapes)
                    if found is not None:
                        return found
                except Exception:
                    pass
        return None
    return _walk(slide.shapes)


def _snapshot_template_runs(text_frame) -> tuple[object | None, object | None]:
    """Snapshot the first paragraph's pPr and rPr XML from a text frame.

    These are deepcopied verbatim and reused on freshly inserted paragraphs/runs
    so that bullet styling, font color (tx1 vs lt1!), language tags etc. all
    survive the rewrite. Without copying the run's rPr, the shape's fontRef
    falls back to lt1 (white) and renders invisibly on a white background.
    """
    if not text_frame.paragraphs:
        return None, None
    p0 = text_frame.paragraphs[0]
    pPr = deepcopy(p0._pPr) if p0._pPr is not None else None
    rPr = None
    if p0.runs:
        r0_xml = p0.runs[0]._r
        rPr_el = r0_xml.find(f"{{{A_NS}}}rPr")
        if rPr_el is not None:
            rPr = deepcopy(rPr_el)
    return pPr, rPr


def _apply_rPr(run, template_rPr, font_size_pt: int | None = None) -> None:
    """Replace the run's <a:rPr> with a deepcopy of the template's.

    If `font_size_pt` is given, override the sz attribute (OOXML sz is in
    hundredths of a point, so 12pt → "1200").
    """
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


def _set_text_preserving_format(
    shape,
    lines: list[str],
    font_size_pt: int | None = None,
) -> None:
    """Rewrite the text frame to `lines`, preserving paragraph + run formatting.

    One paragraph per line; bullet (pPr) and run properties (rPr) from the
    template's first paragraph/run are deepcopied to every new entry. If
    `font_size_pt` is provided, every run is forced to that size.
    """
    tf = shape.text_frame
    template_pPr, template_rPr = _snapshot_template_runs(tf)

    tf.clear()
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        if template_pPr is not None:
            existing_pPr = p._pPr
            if existing_pPr is not None:
                p._p.remove(existing_pPr)
            p._p.insert(0, deepcopy(template_pPr))
        run = p.add_run()
        run.text = line
        _apply_rPr(run, template_rPr, font_size_pt=font_size_pt)

    # As a safety net, ask PowerPoint to shrink text if it still overflows.
    try:
        tf.word_wrap = True
        tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    except Exception:
        pass


def _set_single_text(shape, text: str) -> None:
    tf = shape.text_frame
    template_pPr, template_rPr = _snapshot_template_runs(tf)
    tf.clear()
    p = tf.paragraphs[0]
    if template_pPr is not None:
        existing_pPr = p._pPr
        if existing_pPr is not None:
            p._p.remove(existing_pPr)
        p._p.insert(0, deepcopy(template_pPr))
    run = p.add_run()
    run.text = text
    _apply_rPr(run, template_rPr)


def _set_link_textbox(slide, shape, link_text: str, url: str) -> None:
    """Set the link textbox content + add a hyperlink on the run."""
    tf = shape.text_frame
    template_pPr, template_rPr = _snapshot_template_runs(tf)
    tf.clear()
    p = tf.paragraphs[0]
    if template_pPr is not None:
        existing_pPr = p._pPr
        if existing_pPr is not None:
            p._p.remove(existing_pPr)
        p._p.insert(0, deepcopy(template_pPr))
    run = p.add_run()
    run.text = link_text
    _apply_rPr(run, template_rPr)
    if url:
        try:
            run.hyperlink.address = url
        except Exception:
            pass


def _replace_picture(slide, picture_shape, image_bytes: bytes) -> None:
    """Replace the image data behind an existing PICTURE shape, keeping position.

    Adds the new image as a new image part on the slide, then redirects the
    picture's <a:blip r:embed> to the new relationship.
    """
    image_part, rId = slide.part.get_or_add_image_part(io.BytesIO(image_bytes))
    blip = picture_shape.element.find(f".//{{{A_NS}}}blip")
    if blip is not None:
        blip.set(f"{{{R_NS}}}embed", rId)


def _trim_bullets(bullets: list[str]) -> list[str]:
    """Cap to MAX_BULLETS_PER_SECTION, dropping empties."""
    out = [b.strip() for b in (bullets or []) if b and b.strip()]
    return out[:MAX_BULLETS_PER_SECTION] or ["(情報なし)"]


def _fill_slide(slide, case: Case) -> None:
    title_shape = _find_shape(slide, SHAPE_TITLE)
    if title_shape is not None:
        _set_single_text(title_shape, f"事例- {case.title} -")

    subtitle_shape = _find_shape(slide, SHAPE_SUBTITLE)
    if subtitle_shape is not None:
        _set_single_text(subtitle_shape, case.subtitle)

    headline_shape = _find_shape(slide, SHAPE_HEADLINE)
    if headline_shape is not None:
        headline = case.headline.strip() if case.headline else ""
        if not headline:
            # Fallback: "{organization} × {title}" matches the template's pattern.
            if case.organization and case.title:
                headline = f"{case.organization} × {case.title}"
            else:
                headline = case.title or case.organization or ""
        _set_single_text(headline_shape, headline)

    overview_shape = _find_shape(slide, SHAPE_OVERVIEW)
    if overview_shape is not None:
        _set_text_preserving_format(
            overview_shape, _trim_bullets(case.overview), font_size_pt=BULLET_FONT_SIZE_PT
        )

    challenges_shape = _find_shape(slide, SHAPE_CHALLENGES)
    if challenges_shape is not None:
        _set_text_preserving_format(
            challenges_shape, _trim_bullets(case.challenges), font_size_pt=BULLET_FONT_SIZE_PT
        )

    solutions_shape = _find_shape(slide, SHAPE_SOLUTIONS)
    if solutions_shape is not None:
        _set_text_preserving_format(
            solutions_shape, _trim_bullets(case.solutions), font_size_pt=BULLET_FONT_SIZE_PT
        )

    link_shape = _find_shape(slide, SHAPE_LINK)
    if link_shape is not None:
        _set_link_textbox(slide, link_shape, case.link_text or case.title, case.url)

    # Image: try explicit image_url, then og:image, then arXiv PDF figure.
    # If nothing is found, REMOVE the picture shape so we don't reuse the
    # template's placeholder picture across every slide.
    picture_shape = _find_shape(slide, SHAPE_PICTURE)
    if picture_shape is not None:
        img = scraper.get_case_image(case.url, case.image_url)
        if img is not None:
            _replace_picture(slide, picture_shape, img[0])
        else:
            picture_shape.element.getparent().remove(picture_shape.element)


def build_pptx(cases: list[Case], template_path: Path = TEMPLATE_PATH) -> bytes:
    """Build a single .pptx containing one slide per case, based on the template.

    Clones the template slide within the same Presentation instance so that
    shared parts (layouts, theme, media) are not duplicated in the package.
    """
    if not cases:
        raise ValueError("cases must not be empty")

    prs = Presentation(str(template_path))
    src_slide = prs.slides[0]

    # Snapshot the pristine shape XML and rel map BEFORE we modify the first slide,
    # so subsequent slides can be cloned from the original state.
    original_shape_xmls = [deepcopy(shp.element) for shp in src_slide.shapes]
    original_rels = {rId: rel for rId, rel in src_slide.part.rels.items()}

    _fill_slide(src_slide, cases[0])

    rid_attrs = (f"{{{R_NS}}}embed", f"{{{R_NS}}}link", f"{{{R_NS}}}id")
    for case in cases[1:]:
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

        _fill_slide(new_slide, case)

    bio = io.BytesIO()
    prs.save(bio)
    return bio.getvalue()


def build_single_pptx(case: Case, template_path: Path = TEMPLATE_PATH) -> bytes:
    prs = Presentation(str(template_path))
    _fill_slide(prs.slides[0], case)
    bio = io.BytesIO()
    prs.save(bio)
    return bio.getvalue()
