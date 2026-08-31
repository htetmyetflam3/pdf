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


def _flat_meta_items(metadata: dict):
    """Yield only scalar metadata entries for headers."""
    if not metadata:
        return
    for k, v in metadata.items():
        if isinstance(v, (dict, list, tuple)):
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


def write_docx(all_texts, out_path, pdf_path, metadata, page_metadata=None):
    import zipfile

    width_pt, height_pt = get_page_size(metadata)
    # Word page size is in twips (1 pt = 20 twips).
    pg_w = max(1, int(round(width_pt * 20)))
    pg_h = max(1, int(round(height_pt * 20)))

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>")
        zf.writestr("_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>")

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

        # ── Layout geometry (measured, Word-style) ─────────────────────────
        # These PDFs come from Word ("Save as PDF"), so reproduce the
        # original document: 1" margins, the original line pitch, justified
        # body paragraphs, centered title lines, real tabs at column gaps.
        left_x = 72.0
        right_x = 540.0
        page_w, page_h = width_pt, height_pt
        if page_metadata:
            xs, rr = [], []
            for pg in page_metadata:
                for ln in (pg or {}).get("lines") or []:
                    if ln.get("x") is not None:
                        xs.append(ln["x"])
                    if ln.get("right"):
                        rr.append(ln["right"])
            if xs:
                xs.sort()
                left_x = xs[len(xs) // 10]   # ~10th pct: the body text edge
            if rr:
                rr.sort()
                right_x = rr[int(len(rr) * 0.95)]  # text right edge

        margin_left = max(360, int(round(left_x * 20)))

        # ── Measured rhythm (per paragraph, so mixed TOC/prose pages work) ──
        # Word's Save-as-PDF bakes the ORIGINAL line spacing into the
        # baseline gaps. Zawgyi fonts have a natural line height of ~1.9em,
        # so 'continuation' gaps are up to ~2.3x the font size; anything
        # larger is skipped blank space. Justification is detected from the
        # flush-right fraction of full-width lines (widths from the font's
        # hmtx, exact for combining marks).
        body_pitch = 0.0
        wide_rights = []
        right_left_pairs = []
        if page_metadata:
            cont_gaps = []
            for pg in page_metadata:
                prev_y = None
                for ln in (pg or {}).get("lines") or []:
                    y = ln.get("y")
                    sz = ln.get("size") or 12
                    if prev_y is not None and y is not None:
                        d = prev_y - y
                        if 0.6 * sz < d <= 2.3 * sz:
                            cont_gaps.append(d)
                    prev_y = y
                    if ln.get("text", "").strip():
                        r = ln.get("right") or 0
                        w = r - (ln.get("x") or 0)
                        right_left_pairs.append((r, w))
                        if r:
                            wide_rights.append(r)
            if cont_gaps:
                cont_gaps.sort()
                body_pitch = cont_gaps[len(cont_gaps) // 2]
        if body_pitch <= 0:
            body_pitch = 1.88 * 12
        pitch_tw = max(240, int(round(body_pitch * 20)))

        justified = False
        if wide_rights:
            wide_rights.sort()
            # Robust right edge: median of the widest 5% of lines (ragged
            # right text stops short of the margin; outlier runs can overshoot).
            tail = wide_rights[-max(1, len(wide_rights) // 20):]
            right_x = tail[len(tail) // 2]
            full_thresh = 0.55 * ((right_x - left_x) or 1)
            rights = [r for r, w in right_left_pairs if w > full_thresh]
            if len(rights) >= 10:
                flush = sum(1 for r in rights if abs(r - right_x) <= 1.0 * 14)
                justified = flush / len(rights) > 0.55
        else:
            right_x = 540.0
        # Word margins live in the 0.75"-1.5" band; clamp the estimate there.
        margin_right = min(max(int(round((page_w - right_x) * 20)), 1080), 2160)

        # First-line paragraph indents: lines that start after a big gap
        # (paragraph start) but sit right of the body edge by a constant.
        first_line_tw = 0
        if page_metadata:
            starts = []
            for pg in page_metadata:
                prev_y = None
                for ln in (pg or {}).get("lines") or []:
                    if not ln.get("text", "").strip():
                        continue
                    y = ln.get("y")
                    if prev_y is not None and y is not None and (prev_y - y) > 2.3 * 14:
                        dx = (ln.get("x") or 0) - left_x
                        if 0.25 * 14 < dx < 3 * 14:
                            starts.append(dx)
                    prev_y = y
            if len(starts) >= 8:
                starts.sort()
                first_line_tw = int(round(starts[len(starts) // 2] * 20))

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
                    # Word bakes the original line spacing into baseline gaps:
                    # up to ~2.3x font size = normal continuation; more = the
                    # author pressed Enter extra times -> spacer paragraphs.
                    space_tw = pitch_tw
                    if prev_y is not None and y is not None:
                        d = prev_y - y  # y grows upward in PDF space
                        if 0.6 * size < d <= 2.3 * size:
                            space_tw = max(240, int(round(d * 20)))
                        elif d > 2.3 * size:
                            blank = round(d / body_pitch) - 1
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
               f'<w:pgMar w:top="1440" w:right="{margin_right}" w:bottom="1440" w:left="{margin_left}"/></w:sectPr>'
               f"</w:body></w:document>")
        zf.writestr("word/document.xml", doc)
    print(f"[+] Saved DOCX: {out_path}")


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
