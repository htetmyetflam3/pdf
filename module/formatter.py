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
            return f'<w:r>{font_rpr(font_name, size_pt)}<w:t xml:space="preserve">{_esc(text)}</w:t></w:r>'

        body = []
        body.append(f'<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr>'
                    f'{run(f"Source: {pdf_path}", _EN_FONT, 14)}</w:p>')
        body.append(f'<w:p>{run(f"Pages: {len(all_texts)}", _EN_FONT, 12)}</w:p>')

        for k, v in _flat_meta_items(metadata or {}):
            body.append(f"<w:p>{run(f'{k}: {v}', _EN_FONT, 11)}</w:p>")

        for idx, txt in enumerate(all_texts):
            body.append(f'<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
                        f'{run(f"Page {idx+1}", _EN_FONT, 14)}</w:p>')

            page_meta = None
            if page_metadata and idx < len(page_metadata):
                page_meta = page_metadata[idx]

            lines = (page_meta or {}).get("lines") if page_meta else None
            if lines:
                for line in lines:
                    runs = line.get("runs") or [{
                        "text": line.get("text", ""),
                        "font": line.get("font"),
                        "size": line.get("size", 12),
                    }]
                    parts = []
                    for r in runs:
                        t = r.get("text") or ""
                        size = r.get("size") or line.get("size") or 12
                        font = word_font_for(r.get("font") or line.get("font"), t, metadata)
                        parts.append(run(t, font, size))
                    if not parts:
                        parts.append(run("", _EN_FONT, 12))
                    body.append(f'<w:p>{"".join(parts)}</w:p>')
            else:
                for line in (txt or "").split("\n"):
                    font = word_font_for(None, line, metadata)
                    body.append(f"<w:p>{run(line, font, 12)}</w:p>")

        doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
               f'<w:body>{"".join(body)}'
               f'<w:sectPr><w:pgSz w:w="{pg_w}" w:h="{pg_h}"/>'
               f'<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr>'
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
