from collections import Counter
from pathlib import Path

from .metadata import get_page_size, map_font_to_ttf


_MM_FONT = "YoeYar-One"
_EN_FONT = "Anonymous Pro"
_MM_HINTS = (
    "zawgyi", "myanmar", "amyanmar", "yoeyar", "pyidaungsu",
    "padauk", "notosansmyanmar", "tharlon", "sabae",
)
_EN_HINTS = ("times", "anonymous", "arial", "helvetica", "courier", "gautami", "roman")


def _is_myanmar_text(text: str) -> bool:
    return any("\u1000" <= c <= "\u109F" or "\uAA60" <= c <= "\uAA7F" for c in (text or ""))


# Internal plumbing keys that must not surface in the document header.
_INTERNAL_META_KEYS = {"page_count", "page_size", "font_map"}


def _flat_meta_items(metadata: dict):
    """Yield only scalar metadata entries for headers."""
    if not metadata:
        return
    for k, v in metadata.items():
        if isinstance(v, (dict, list, tuple)):
            continue
        if k in _INTERNAL_META_KEYS:
            continue
        yield k, v


def _build_meta_header(pdf_path, metadata, page_count):
    """Build the plain-text metadata header."""
    meta_lines = [f"# Source: {pdf_path}", f"# Pages: {page_count}", "#" * 50]
    for k, v in _flat_meta_items(metadata):
        meta_lines.insert(-1, f"# {k}: {v}")
    return "\n".join(meta_lines) + "\n\n"


def word_font_for(pdf_font: str | None, text: str = "", metadata: dict | None = None) -> str:
    """Map a PDF font family (or run text) to a Word-safe installed font name."""
    path = map_font_to_ttf(pdf_font or "", metadata) if pdf_font else None
    if path:
        if "Anonymous" in path:
            return _EN_FONT
        return _MM_FONT
    name = (pdf_font or "").lower()
    if any(h in name for h in _MM_HINTS):
        return _MM_FONT
    if any(h in name for h in _EN_HINTS):
        return _EN_FONT
    return _MM_FONT if _is_myanmar_text(text) else _EN_FONT


def write_txt(all_texts, out_path, pdf_path, metadata):
    meta = _build_meta_header(pdf_path, metadata, len(all_texts))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(meta)
        for idx, txt in enumerate(all_texts):
            f.write(f"--- Page {idx+1} ---\n")
            f.write(txt if txt else "")
            f.write("\n\n")
    print(f"[+] Saved TXT: {out_path}")


# ---------------------------------------------------------------------------
# Layout measurement (shared by the python-docx writer and the manual writer)
# ---------------------------------------------------------------------------

def _measure_layout(metadata, page_metadata):
    """Return the page geometry / line-pitch values used by the DOCX writers.

    Everything here is measured from the PDF itself: page box, text block
    edges, top/bottom margins and the real baseline pitch. Nothing assumes
    Word's 1" defaults, because these PDFs do not use them (the sample corpus
    sits at a 56.6 pt left edge with a 49 pt baseline pitch at 20 pt type).
    """
    width_pt, height_pt = get_page_size(metadata)
    # Word page size is in twips (1 pt = 20 twips).
    pg_w = max(1, int(round(width_pt * 20)))
    pg_h = max(1, int(round(height_pt * 20)))

    page_w, page_h = width_pt, height_pt

    # ── Text block edges, measured ─────────────────────────────────────
    # Left: the modal line start (body edge), not a percentile, so a few
    # indented lines cannot drag it. Right: a percentile of line ends,
    # clamped inside the page box -- glyph-advance rounding makes a small
    # tail of lines compute a `right` beyond the physical page.
    left_x = 72.0
    right_x = min(540.0, width_pt - 72.0)
    body_size = 12.0
    if page_metadata:
        xs, rr, szs = [], [], []
        for pg in page_metadata:
            for ln in (pg or {}).get("lines") or []:
                if not (ln.get("text") or "").strip():
                    continue
                if ln.get("x") is not None:
                    xs.append(round(float(ln["x"]), 1))
                r = ln.get("right")
                if r:
                    rr.append(float(r))
                if ln.get("size"):
                    szs.append(round(float(ln["size"]), 1))
        if xs:
            left_x = Counter(xs).most_common(1)[0][0]
        if szs:
            body_size = Counter(szs).most_common(1)[0][0]
        if rr:
            rr.sort()
            # `right` is x plus the sum of glyph advances. For Zawgyi the
            # stacked marks carry a nominal advance but render zero-width,
            # so summed widths overshoot the true inked extent (measured
            # ~11% long against the rendered page). Use the widest observed
            # line as the column requirement rather than a percentile, and
            # let the page box cap it: a column that is too NARROW makes
            # Word re-wrap and destroys the PDF's line breaks, while one a
            # little too wide changes nothing visually.
            est = rr[-1]
            # Leave a hairline gutter only; do not mirror the left margin,
            # which would clip the widest lines and re-wrap them.
            right_x = min(est, width_pt - 18.0)
        if right_x <= left_x:
            right_x = max(left_x + 72.0, width_pt - left_x)

    margin_left = max(0, int(round(left_x * 20)))

    # ── Measured rhythm (per paragraph, so mixed TOC/prose pages work) ──
    body_pitch = 0.0
    wide_rights = []
    right_left_pairs = []
    if page_metadata:
        all_gaps = []
        for pg in page_metadata:
            prev_y = None
            for ln in (pg or {}).get("lines") or []:
                if not (ln.get("text") or "").strip():
                    continue
                y = ln.get("y")
                if prev_y is not None and y is not None:
                    d = prev_y - y
                    # Only reject nonsense (column resets, overlapping runs).
                    # The real pitch is discovered below, not assumed to sit
                    # under some multiple of the font size.
                    if 0.3 < d < 0.5 * page_h:
                        all_gaps.append(d)
                prev_y = y
                r = ln.get("right") or 0
                w = r - (ln.get("x") or 0)
                right_left_pairs.append((r, w))
                if r:
                    wide_rights.append(r)
        if all_gaps:
            # The single-spaced baseline pitch is the MODE of the gap
            # distribution: the most repeated spacing is by definition
            # normal line advance. Median would be skewed by paragraph
            # breaks. Quantise to 0.5 pt so float noise clusters.
            hist = Counter(round(g * 2) / 2 for g in all_gaps)
            body_pitch = hist.most_common(1)[0][0]
    if body_pitch <= 0:
        body_pitch = 1.88 * 12
    pitch_tw = max(240, int(round(body_pitch * 20)))

    # A gap counts as a real blank line only when it clearly exceeds the
    # measured single-space pitch, not a font-size guess.
    para_gap = body_pitch * 1.45

    justified = False
    if wide_rights:
        full_thresh = 0.55 * ((right_x - left_x) or 1)
        rights = [r for r, w in right_left_pairs if w > full_thresh]
        if len(rights) >= 10:
            # Flush-right within half a character = justified body text.
            flush = sum(1 for r in rights if abs(r - right_x) <= 6.0)
            justified = flush / len(rights) > 0.55

    # Right margin follows the measured text block. Floor at 0 (not 0.75")
    # so a wide text block is not force-narrowed, which would re-wrap every
    # line and destroy the PDF's line breaks.
    margin_right = max(0, int(round((page_w - right_x) * 20)))

    # First-line paragraph indents: lines that start after a big gap
    # (paragraph start) but sit right of the body edge by a constant.
    first_line_tw = 0
    margin_top = 1440
    margin_bottom = 1440
    if page_metadata:
        starts = []
        tops, bottoms = [], []
        spans = []
        for pg in page_metadata:
            prev_y = None
            page_ys = []
            for ln in (pg or {}).get("lines") or []:
                if not (ln.get("text") or "").strip():
                    continue
                y = ln.get("y")
                if y is not None:
                    page_ys.append(y)
                if prev_y is not None and y is not None and (prev_y - y) > para_gap:
                    dx = (ln.get("x") or 0) - left_x
                    if 0.25 * 14 < dx < 3 * 14:
                        starts.append(dx)
                prev_y = y
            if page_ys:
                tops.append(max(page_ys))
                bottoms.append(min(page_ys))
                if len(page_ys) > 1:
                    spans.append(max(page_ys) - min(page_ys))
        if len(starts) >= 8:
            starts.sort()
            first_line_tw = int(round(starts[len(starts) // 2] * 20))
        # `y` is a BASELINE. The visual top of the text block sits one
        # ascent above it (~0.8 em for these faces), so the page margin is
        # measured to the glyph top, not to the baseline.
        ascent = body_size * 0.8
        if tops:
            tops.sort()
            first_baseline = tops[len(tops) // 2]
            margin_top = max(0, int(round((page_h - first_baseline - ascent) * 20)))
        if bottoms:
            bottoms.sort()
            last_baseline = bottoms[len(bottoms) // 2]
            descent = body_size * 0.2
            margin_bottom = max(0, int(round((last_baseline - descent) * 20)))

        # The text block must actually hold the tallest page. Word breaks a
        # page as soon as the next line does not fit, so a block even one
        # line short cascades every page onto two and doubles the document.
        #
        # The requirement is the measured first-to-last BASELINE SPAN, not
        # lines * body_pitch: a document can mix rhythms (c1-700 carries both
        # a 49 pt and a 26.5 pt pitch), and multiplying the busiest page's
        # line count by the single most common pitch overstates the need so
        # badly it goes past the paper (18 x 49 = 882 pt on a 792 pt page)
        # and pins both margins to the floor. Each page already carries its
        # own spacing, so the span is the honest number.
        if spans:
            spans.sort()
            need_pt = spans[-1] + ascent + body_size * 0.2
            need_tw = int(round(need_pt * 20))
            floor_tw = 288  # 0.2" -- keep the block off the paper edge
            slack = pg_h - margin_top - margin_bottom - need_tw
            if slack < 0:
                take = min(max(0, margin_bottom - floor_tw), -slack)
                margin_bottom -= take
                slack += take
            if slack < 0:
                take = min(max(0, margin_top - floor_tw), -slack)
                margin_top -= take

    return {
        "pg_w": pg_w,
        "pg_h": pg_h,
        "page_w": page_w,
        "page_h": page_h,
        "margin_left": margin_left,
        "margin_right": margin_right,
        "margin_top": margin_top,
        "margin_bottom": margin_bottom,
        "para_gap": para_gap,
        "pitch_tw": pitch_tw,
        "body_pitch": body_pitch,
        "justified": justified,
        "first_line_tw": first_line_tw,
        "left_x": left_x,
        "right_x": right_x,
    }


# ---------------------------------------------------------------------------
# python-docx writer — generates a real Word 2016+/Office Open XML package
# (styles, settings, font table, doc props, document.xml.rels, ...).
# ---------------------------------------------------------------------------

def _set_pydocx_run_font(run, font_name, size_pt):
    """Apply a font to a python-docx run, including complex-script (w:cs)."""
    from docx.shared import Pt
    from docx.oxml.ns import qn

    size_pt = size_pt or 12
    run.font.name = font_name
    try:
        run.font.size = Pt(max(2, int(round(float(size_pt)))))
    except (TypeError, ValueError):
        run.font.size = Pt(12)

    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.rFonts
    if rFonts is None:
        rFonts = rPr.get_or_add_rFonts()
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rFonts.set(qn(attr), font_name)


def _pydocx_add_run_text(p, text, font_name, size_pt):
    """Add one run, converting literal \\t to real Word tab stops."""
    parts = (text or "").split("\t")
    for i, part in enumerate(parts):
        r = p.add_run(part)
        _set_pydocx_run_font(r, font_name, size_pt)
        if i < len(parts) - 1:
            tab_run = p.add_run()
            tab_run.add_tab()
            _set_pydocx_run_font(tab_run, font_name, size_pt)


def _set_pydocx_compat_mode(doc):
    """Tell Word 2016+ this is a modern (not legacy/compatibility) document.

    python-docx's default template declares compatibilityMode=14 (Word 2010),
    which makes Word 2016+ show the document in Compatibility Mode and call it
    an "old" document.  Bumping it to 16 declares the Word 2016 format.
    """
    from docx.oxml.ns import qn

    settings = doc.settings.element
    compat = settings.find(qn("w:compat"))
    if compat is None:
        from docx.oxml import OxmlElement
        compat = OxmlElement("w:compat")
        settings.append(compat)
    found = False
    for cs in compat.findall(qn("w:compatSetting")):
        if cs.get(qn("w:name")) == "compatibilityMode":
            cs.set(qn("w:val"), "16")
            found = True
            break
    if not found:
        from docx.oxml import OxmlElement
        cs = OxmlElement("w:compatSetting")
        cs.set(qn("w:name"), "compatibilityMode")
        cs.set(qn("w:uri"), "http://schemas.microsoft.com/office/word")
        cs.set(qn("w:val"), "16")
        compat.append(cs)


def _write_docx_python_docx(all_texts, out_path, pdf_path, metadata, page_metadata):
    from docx import Document
    from docx.shared import Twips
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_LINE_SPACING

    layout = _measure_layout(metadata, page_metadata)
    doc = Document()
    _set_pydocx_compat_mode(doc)

    section = doc.sections[0]
    section.page_width = Twips(layout["pg_w"])
    section.page_height = Twips(layout["pg_h"])
    section.left_margin = Twips(layout["margin_left"])
    section.right_margin = Twips(layout["margin_right"])
    section.top_margin = Twips(1440)
    section.bottom_margin = Twips(1440)

    def style_par(par, style_name):
        try:
            par.style = doc.styles[style_name]
        except Exception:
            pass
        return par

    def new_paragraph(runs, space_tw=None, alignment=None,
                      left_tw=0, first_line_tw=0, style_name=None):
        p = doc.add_paragraph()
        if style_name:
            style_par(p, style_name)
        pf = p.paragraph_format
        if space_tw:
            pf.line_spacing_rule = WD_LINE_SPACING.AT_LEAST
            pf.line_spacing = Twips(int(space_tw))
        if left_tw:
            pf.left_indent = Twips(int(left_tw))
        if first_line_tw:
            pf.first_line_indent = Twips(int(first_line_tw))
        if alignment is not None:
            p.alignment = alignment
        for text, font_name, size_pt in runs:
            _pydocx_add_run_text(p, text, font_name, size_pt)
        return p

    # ── Header / metadata ──────────────────────────────────────────────
    new_paragraph([(f"Source: {pdf_path}", _EN_FONT, 14)],
                  style_name="Title")
    new_paragraph([(f"Pages: {len(all_texts)}", _EN_FONT, 12)])
    for k, v in _flat_meta_items(metadata or {}):
        new_paragraph([(f"{k}: {v}", _EN_FONT, 11)])

    for idx, txt in enumerate(all_texts):
        # Page heading paragraph with a real page break.
        p = new_paragraph([("", _EN_FONT, 14)], style_name="Heading 1")
        r = p.add_run()
        r.add_break(WD_BREAK.PAGE)
        _set_pydocx_run_font(r, _EN_FONT, 14)
        _pydocx_add_run_text(p, f"Page {idx+1}", _EN_FONT, 14)

        page_meta = None
        if page_metadata and idx < len(page_metadata):
            page_meta = page_metadata[idx]

        lines = (page_meta or {}).get("lines") if page_meta else None
        if lines:
            prev_y = None
            for line in lines:
                y = line.get("y")
                size = line.get("size") or 12
                space_tw = layout["pitch_tw"]
                if prev_y is not None and y is not None:
                    d = prev_y - y  # y grows upward in PDF space
                    if 0.6 * size < d <= 2.3 * size:
                        space_tw = max(240, int(round(d * 20)))
                    elif d > 2.3 * size:
                        blank = round(d / layout["body_pitch"]) - 1
                        for _ in range(max(0, min(blank, 30))):
                            new_paragraph([("", _EN_FONT, size)])
                prev_y = y

                runs = line.get("runs") or [{
                    "text": line.get("text", ""),
                    "font": line.get("font"),
                    "size": size,
                }]
                parts = []
                for r in runs:
                    t = r.get("text") or ""
                    rsize = r.get("size") or size
                    font = word_font_for(r.get("font") or line.get("font"), t, metadata)
                    parts.append((t, font, rsize))
                if not parts:
                    parts.append(("", _EN_FONT, 12))

                # Paragraph properties: original line pitch, alignment.
                alignment = None
                lw = (line.get("right") or 0) - (line.get("x") or 0)
                cx = (line.get("x") or 0) + lw / 2
                if lw and abs(cx - layout["page_w"] / 2) <= 12 and lw < 0.6 * (layout["right_x"] - layout["left_x"]):
                    alignment = WD_ALIGN_PARAGRAPH.CENTER
                elif layout["justified"] and lw > 0.55 * (layout["right_x"] - layout["left_x"]):
                    alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

                left_tw = 0
                try:
                    left_tw = max(0, int(round((float(line.get("x")) - layout["left_x"]) * 20)))
                except (TypeError, ValueError):
                    pass

                new_paragraph(
                    parts,
                    space_tw=space_tw,
                    alignment=alignment,
                    left_tw=left_tw,
                    first_line_tw=layout["first_line_tw"],
                )
        else:
            for line in (txt or "").split("\n"):
                font = word_font_for(None, line, metadata)
                new_paragraph([(line, font, 12)])

    doc.save(out_path)
    return doc


# ---------------------------------------------------------------------------
# Manual / fallback OOXML writer (kept for environments without python-docx).
# It now writes the standard supporting parts as well, so Word and
# python-docx can open the result instead of treating it as a legacy file.
# ---------------------------------------------------------------------------

def _write_docx_manual(all_texts, out_path, pdf_path, metadata, page_metadata):
    import zipfile

    width_pt, height_pt = get_page_size(metadata)
    # Word page size is in twips (1 pt = 20 twips).
    pg_w = max(1, int(round(width_pt * 20)))
    pg_h = max(1, int(round(height_pt * 20)))

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:

        def _content_types():
            return (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
                '<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>'
                '<Override PartName="/word/fontTable.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.fontTable+xml"/>'
                '<Override PartName="/word/webSettings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.webSettings+xml"/>'
                '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
                '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
                "</Types>"
            )

        def _root_rels():
            return (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
                '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
                "</Relationships>"
            )

        def _document_rels():
            return (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>'
                '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/webSettings" Target="webSettings.xml"/>'
                '<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/fontTable" Target="fontTable.xml"/>'
                "</Relationships>"
            )

        def _styles_xml():
            return (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:docDefaults>'
                '<w:rPrDefault><w:rPr>'
                '<w:rFonts w:ascii="Anonymous Pro" w:hAnsi="Anonymous Pro" w:cs="YoeYar-One" w:eastAsia="YoeYar-One"/>'
                '<w:sz w:val="24"/><w:szCs w:val="24"/>'
                '</w:rPr></w:rPrDefault>'
                '<w:pPrDefault><w:pPr><w:spacing w:line="240" w:lineRule="auto"/></w:pPr></w:pPrDefault>'
                '</w:docDefaults>'
                '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
                '<w:style w:type="character" w:default="1" w:styleId="DefaultParagraphFont"><w:name w:val="Default Paragraph Font"/></w:style>'
                '<w:style w:type="table" w:default="1" w:styleId="TableNormal"><w:name w:val="Normal Table"/><w:qFormat/></w:style>'
                '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:qFormat/>'
                '<w:pPr><w:spacing w:before="0" w:after="120"/><w:jc w:val="left"/></w:pPr>'
                '<w:rPr><w:b/><w:sz w:val="28"/><w:szCs w:val="28"/></w:rPr></w:style>'
                '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:qFormat/>'
                '<w:pPr><w:keepNext/><w:spacing w:before="240" w:after="120"/><w:jc w:val="left"/></w:pPr>'
                '<w:rPr><w:b/><w:sz w:val="28"/><w:szCs w:val="28"/></w:rPr></w:style>'
                "</w:styles>"
            )

        def _settings_xml():
            return (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:zoom w:percent="100"/>'
                "<w:defaultTabStop w:val=\"720\"/>"
                '<w:compat>'
                '<w:compatSetting w:name="compatibilityMode" w:uri="http://schemas.microsoft.com/office/word" w:val="16"/>'
                "</w:compat>"
                "</w:settings>"
            )

        def _web_settings_xml():
            return (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:webSettings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:optimizeForBrowser/>"
                "</w:webSettings>"
            )

        def _font_table_xml():
            return (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:fonts xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:font w:name="YoeYar-One"><w:charset w:val="00"/><w:family w:val="auto"/><w:pitch w:val="variable"/></w:font>'
                '<w:font w:name="Anonymous Pro"><w:charset w:val="00"/><w:family w:val="auto"/><w:pitch w:val="variable"/></w:font>'
                "</w:fonts>"
            )

        def _core_xml():
            return (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
                'xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
                '<dc:title>Myanmar PDF extraction</dc:title>'
                '<dc:creator>pdf-text-extractor</dc:creator>'
                '<cp:lastModifiedBy>pdf-text-extractor</cp:lastModifiedBy>'
                '<dcterms:modified xsi:type="dcterms:W3CDTF">2026-01-01T00:00:00Z</dcterms:modified>'
                "</cp:coreProperties>"
            )

        def _app_xml():
            return (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
                'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
                "<Application>pdf-text-extractor</Application>"
                "<AppVersion>16.0000</AppVersion>"
                "</Properties>"
            )

        zf.writestr("[Content_Types].xml", _content_types())
        zf.writestr("_rels/.rels", _root_rels())
        zf.writestr("word/_rels/document.xml.rels", _document_rels())
        zf.writestr("word/styles.xml", _styles_xml())
        zf.writestr("word/settings.xml", _settings_xml())
        zf.writestr("word/webSettings.xml", _web_settings_xml())
        zf.writestr("word/fontTable.xml", _font_table_xml())
        zf.writestr("docProps/core.xml", _core_xml())
        zf.writestr("docProps/app.xml", _app_xml())

        def _esc(s):
            return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

        def font_rpr(font_name, size_pt=12):
            sz = max(2, int(round(float(size_pt or 12) * 2)))  # half-points
            return (f"<w:rPr>"
                    f'<w:rFonts w:ascii="{font_name}" w:hAnsi="{font_name}" '
                    f'w:cs="{font_name}" w:eastAsia="{font_name}"/>'
                    f'<w:sz w:val="{sz}"/><w:szCs w:val="{sz}"/>'
                    f"</w:rPr>")

        def run(text, font_name, size_pt=12):
            # Literal \t (inserted by the parser at big column gaps) becomes a
            # real Word tab; everything else stays one xml:space=preserve run.
            parts = [_esc(p) for p in (text or "").split("\t")]
            inner = "<w:tab/>".join(
                f'<w:t xml:space="preserve">{p}</w:t>' for p in parts)
            return f"<w:r>{font_rpr(font_name, size_pt)}{inner}</w:r>"

        body = []
        body.append(f'<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr>'
                    f'{run(f"Source: {pdf_path}", _EN_FONT, 14)}</w:p>')
        body.append(f'<w:p>{run(f"Pages: {len(all_texts)}", _EN_FONT, 12)}</w:p>')

        for k, v in _flat_meta_items(metadata or {}):
            body.append(f"<w:p>{run(f'{k}: {v}', _EN_FONT, 11)}</w:p>")

        layout = _measure_layout(metadata, page_metadata)
        left_x = layout["left_x"]
        body_pitch = layout["body_pitch"]
        pitch_tw = layout["pitch_tw"]
        para_gap = layout["para_gap"]
        justified = layout["justified"]
        first_line_tw = layout["first_line_tw"]
        margin_right = layout["margin_right"]
        margin_left = layout["margin_left"]
        margin_top = layout["margin_top"]
        margin_bottom = layout["margin_bottom"]
        page_w = layout["page_w"]
        right_x = layout["right_x"]

        def indent_twips(x):
            try:
                return max(0, int(round((float(x) - left_x) * 20)))
            except (TypeError, ValueError):
                return 0

        for idx, txt in enumerate(all_texts):
            body.append(f'<w:p><w:pPr><w:pStyle w:val="Heading1"/>'
                        f'<w:spacing w:before="240" w:after="120"/></w:pPr>'
                        f'<w:r><w:br w:type="page"/></w:r>'
                        f'{run(f"Page {idx+1}", _EN_FONT, 14)}</w:p>')

            page_meta = None
            if page_metadata and idx < len(page_metadata):
                page_meta = page_metadata[idx]

            lines = (page_meta or {}).get("lines") if page_meta else None
            if lines:
                prev_y = None
                for line in lines:
                    y = line.get("y")
                    size = line.get("size") or 12
                    # Baseline pitch is measured (the mode of the gap
                    # distribution), so a gap only means "the author pressed
                    # Enter" when it exceeds that measured pitch -- comparing
                    # against a font-size multiple mislabels every single line
                    # whenever the document is leaded loosely.
                    space_tw = pitch_tw
                    if prev_y is not None and y is not None:
                        d = prev_y - y  # y grows upward in PDF space
                        if d <= para_gap:
                            space_tw = max(240, int(round(d * 20)))
                        else:
                            space_tw = max(240, int(round(body_pitch * 20)))
                            blank = int(round(d / body_pitch)) - 1
                            for _ in range(max(0, min(blank, 30))):
                                body.append(f'<w:p>{run("", _EN_FONT, size)}</w:p>')
                    prev_y = y

                    runs = line.get("runs") or [{
                        "text": line.get("text", ""),
                        "font": line.get("font"),
                        "size": size,
                    }]
                    parts = []
                    for r in runs:
                        t = r.get("text") or ""
                        rsize = r.get("size") or size
                        font = word_font_for(r.get("font") or line.get("font"), t, metadata)
                        parts.append(run(t, font, rsize))
                    if not parts:
                        parts.append(run("", _EN_FONT, 12))

                    # Paragraph properties: original line pitch, alignment.
                    ppr = [f'<w:spacing w:line="{space_tw}" w:lineRule="atLeast"/>']
                    ind = indent_twips(line.get("x"))
                    lw = (line.get("right") or 0) - (line.get("x") or 0)
                    cx = (line.get("x") or 0) + lw / 2
                    if lw and abs(cx - page_w / 2) <= 12 and lw < 0.6 * (right_x - left_x):
                        ppr.append('<w:jc w:val="center"/>')
                    elif justified and lw > 0.55 * (right_x - left_x):
                        ppr.append('<w:jc w:val="both"/>')
                    if ind or first_line_tw:
                        ppr.append(f'<w:ind w:left="{ind}" w:firstLine="{first_line_tw}"/>')
                    body.append(f'<w:p><w:pPr>{"".join(ppr)}</w:pPr>{"".join(parts)}</w:p>')
            else:
                for line in (txt or "").split("\n"):
                    font = word_font_for(None, line, metadata)
                    body.append(f"<w:p>{run(line, font, 12)}</w:p>")

        doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
               f'<w:body>{"".join(body)}'
               f'<w:sectPr><w:pgSz w:w="{pg_w}" w:h="{pg_h}"/>'
               f'<w:pgMar w:top="{margin_top}" w:right="{margin_right}" '
               f'w:bottom="{margin_bottom}" w:left="{margin_left}" '
               f'w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>'
               f"</w:body></w:document>")
        zf.writestr("word/document.xml", doc)


def write_docx(all_texts, out_path, pdf_path, metadata, page_metadata=None):
    """Write a Word 2016+/python-docx compatible .docx.

    Uses python-docx when it is installed (recommended, generates the full
    OOXML part set).  Otherwise falls back to the built-in OOXML writer,
    which now also emits the standard supporting parts.
    """
    writer = "manual"
    try:
        from docx import Document
        _write_docx_python_docx(all_texts, out_path, pdf_path, metadata, page_metadata)
        writer = "python-docx"
    except ImportError:
        _write_docx_manual(all_texts, out_path, pdf_path, metadata, page_metadata)
        writer = "manual"
    except Exception as e:
        # If python-docx is present but the write fails, fall back rather
        # than silently producing nothing.
        print(f"[!] python-docx write failed ({e}); using manual fallback")
        _write_docx_manual(all_texts, out_path, pdf_path, metadata, page_metadata)
        writer = "manual"
    print(f"[+] Saved DOCX: {out_path} ({writer})")


def write_output(all_texts, out_path, pdf_path, metadata=None, page_metadata=None):
    """Route to the correct writer based on file extension."""
    out_path = Path(out_path)
    out_ext = out_path.suffix.lower()
    if out_ext == ".txt":
        write_txt(all_texts, out_path, pdf_path, metadata or {})
    elif out_ext == ".docx":
        write_docx(all_texts, out_path, pdf_path, metadata or {}, page_metadata)
    else:
        raise ValueError(f"Unsupported output format: {out_ext}")
    print("[+] Done.")


# ---------------------------------------------------------------------------
# Streaming writers
#
# NEW: the writers above take the whole document at once. These accept one
# page at a time so the pipeline never has to hold every page in memory.
# Same output as the batch writers — they reuse the same rendering helpers.
# ---------------------------------------------------------------------------

class StreamTxtWriter:
    """Writes TXT incrementally, one page per call."""

    def __init__(self, out_path, pdf_path, page_count, metadata=None):
        self.out_path = Path(out_path)
        self.pdf_path = pdf_path
        self.page_count = page_count
        self._idx = 0
        self._fh = open(self.out_path, "w", encoding="utf-8")
        # /Info and the page count are known before the walk starts, so the
        # header can go out immediately — no buffering needed.
        self._fh.write(_build_meta_header(pdf_path, metadata or {}, page_count))

    def write_page(self, text, layout=None):
        self._idx += 1
        self._fh.write(f"--- Page {self._idx} ---\n")
        self._fh.write(text or "")
        self._fh.write("\n\n")

    def close(self, metadata=None):
        self._fh.close()
        print(f"[+] Saved TXT: {self.out_path}")


class StreamDocxWriter:
    """Buffers layout-light page records, then emits the DOCX on close().

    python-docx builds its XML tree in memory, so a true append-per-page DOCX
    is not possible without hand-rolling the package. This keeps only the
    already-post-processed text/layout for each page and hands them to the
    existing writer at the end, which is still far cheaper than the old
    pipeline (no duplicated raw pages, no pdfminer page records).
    """

    def __init__(self, out_path, pdf_path, page_count):
        self.out_path = out_path
        self.pdf_path = pdf_path
        self.page_count = page_count
        self._texts = []
        self._layouts = []

    def write_page(self, text, layout=None):
        self._texts.append(text)
        self._layouts.append(layout)

    def close(self, metadata=None):
        write_docx(self._texts, self.out_path, self.pdf_path,
                   metadata or {}, self._layouts)


def open_stream_writer(out_path, pdf_path, doc, enabled: bool = True):
    """Return a streaming writer for `out_path`, or None to use batch mode."""
    if not enabled:
        return None
    ext = Path(out_path).suffix.lower()
    page_count = getattr(doc, "page_count", 0)
    doc_meta = getattr(doc, "meta", None) or {}
    if ext == ".txt":
        return StreamTxtWriter(out_path, pdf_path, page_count, doc_meta)
    if ext == ".docx":
        return StreamDocxWriter(out_path, pdf_path, page_count)
    raise ValueError(f"Unsupported output format: {ext}")
