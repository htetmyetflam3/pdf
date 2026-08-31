from pathlib import Path


def _build_meta_header(pdf_path, metadata, page_count):
    """Build the plain-text metadata header."""
    meta_lines = [f"# Source: {pdf_path}", f"# Pages: {page_count}", "#" * 50]
    if metadata:
        for k, v in metadata.items():
            meta_lines.insert(-1, f"# {k}: {v}")
    return '\n'.join(meta_lines) + '\n\n'


def write_txt(all_texts, out_path, pdf_path, metadata):
    meta = _build_meta_header(pdf_path, metadata, len(all_texts))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(meta)
        for idx, txt in enumerate(all_texts):
            f.write(f"--- Page {idx+1} ---\n")
            f.write(txt if txt else "")
            f.write("\n\n")
    print(f"[+] Saved TXT: {out_path}")


def write_docx(all_texts, out_path, pdf_path, metadata):
    import zipfile
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('[Content_Types].xml',
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '</Types>')
        zf.writestr('_rels/.rels',
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')

        # Font settings
        mm_font = "YoeYar-One"
        en_font = "Anonymous Pro"

        def font_rpr(font_name):
            return (f'<w:rPr>'
                    f'<w:rFonts w:ascii="{font_name}" w:hAnsi="{font_name}" '
                    f'w:cs="{font_name}" w:eastAsia="{font_name}"/>'
                    f'</w:rPr>')

        def run(text, font_name):
            return f'<w:r>{font_rpr(font_name)}<w:t xml:space="preserve">{_esc(text)}</w:t></w:r>'

        def _esc(s):
            return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")

        body = []
        body.append(f'<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr>'
                    f'{run(f"Source: {pdf_path}", en_font)}</w:p>')
        body.append(f'<w:p>{run(f"Pages: {len(all_texts)}", en_font)}</w:p>')

        for k, v in metadata.items():
            body.append(f'<w:p>{run(f"{k}: {v}", en_font)}</w:p>')

        for idx, txt in enumerate(all_texts):
            body.append(f'<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
                        f'{run(f"Page {idx+1}", en_font)}</w:p>')

            for line in (txt or "").split('\n'):
                # Detect script per line
                has_mm = any('\u1000' <= c <= '\u109F' or '\uAA60' <= c <= '\uAA7F' for c in line)
                font = mm_font if has_mm else en_font
                body.append(f'<w:p>{run(line, font)}</w:p>')

        doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
               f'<w:body>{"".join(body)}'
               f'<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
               f'<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr>'
               f'</w:body></w:document>')
        zf.writestr('word/document.xml', doc)
    print(f"[+] Saved DOCX: {out_path}")


# ── Dispatcher ─────────────────────────────────────────────────────────────────
def write_output(all_texts, out_path, pdf_path, metadata=None):
    """Route to the correct writer based on file extension."""
    out_path = Path(out_path)
    out_ext = out_path.suffix.lower()
    if out_ext == '.txt':
        write_txt(all_texts, out_path, pdf_path, metadata or {})
    elif out_ext == '.docx':
        write_docx(all_texts, out_path, pdf_path, metadata or {})
    else:
        raise ValueError(f"Unsupported output format: {out_ext}")
    print("[+] Done.")
