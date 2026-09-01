#!/usr/bin/env python3
"""Merge large TXT/DOCX chunk files and inspect DOCX content (line counts).

This version prefers python-docx for DOCX work because the old raw-XML /
SAX approach produced packages that Microsoft Word and python-docx
sometimes refused to open (especially files created by Word 2016+).

Usage:
    python tools/merge.py [SOURCE_DIR]
    python tools/merge.py --inspect file1.docx file2.docx
    python tools/merge.py --verify
"""

import shutil
import sys
import zipfile
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from xml.sax import make_parser, ContentHandler
from xml.sax.handler import feature_external_ges, feature_external_pes

try:
    from docx import Document
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml.ns import qn

    HAS_PYTHON_DOCX = True
except Exception:
    HAS_PYTHON_DOCX = False

DOCX_FILES = ["c1to700.docx", "c701to1k.docx", "ending.docx"]
TXT_FILES = ["c1to700.txt", "c701to1k.txt", "ending.txt"]


# ---------------------------------------------------------------------------
# TXT: stream line by line
# ---------------------------------------------------------------------------

def merge_txt_files(source_dir, output_dir):
    output_path = output_dir / "merged.txt"

    with open(output_path, "w", encoding="utf-8") as outfile:
        for fname in TXT_FILES:
            src = source_dir / fname
            if not src.exists():
                print(f"Warning: {fname} not found, skipping.")
                continue
            print(f"  Streaming {fname} ...")
            with open(src, "r", encoding="utf-8") as infile:
                shutil.copyfileobj(infile, outfile)
                outfile.write("\n")
    print(f"Merged .txt → {output_path}")


# ---------------------------------------------------------------------------
# DOCX helpers (python-docx)
# ---------------------------------------------------------------------------

def _existing_files(source_dir, names):
    return [source_dir / n for n in names if (source_dir / n).exists()]


def _set_compat_mode_16(doc):
    """Mark the package as Word 2016+ so Word doesn't open it in Compatibility Mode."""
    from docx.oxml import OxmlElement

    settings = doc.settings.element
    compat = settings.find(qn("w:compat"))
    if compat is None:
        compat = OxmlElement("w:compat")
        settings.append(compat)
    found = False
    for cs in compat.findall(qn("w:compatSetting")):
        if cs.get(qn("w:name")) == "compatibilityMode":
            cs.set(qn("w:val"), "16")
            found = True
            break
    if not found:
        cs = OxmlElement("w:compatSetting")
        cs.set(qn("w:name"), "compatibilityMode")
        cs.set(qn("w:uri"), "http://schemas.microsoft.com/office/word")
        cs.set(qn("w:val"), "16")
        compat.append(cs)


def _collect_rel_ids(element):
    """Collect every relationship id referenced by an OOXML subtree."""
    ids = set()
    for el in element.iter():
        for attr in (qn("r:id"), qn("r:embed"), qn("r:link")):
            val = el.attrib.get(attr)
            if val:
                ids.add(val)
    return ids


def _map_rels_for_element(src_doc_part, dst_doc_part, element):
    """Copy relationships used by `element` into the destination package.

    Returns {old_rId: new_rId}.  Images and external hyperlinks are copied
    properly; unsupported relationship kinds are reported (the merged file is
    still valid Word document XML, but those objects may not render).
    """
    mapping = {}
    for old_rid in sorted(_collect_rel_ids(element)):
        rel = src_doc_part.rels.get(old_rid)
        if rel is None:
            print(f"  [!] Relationship {old_rid!r} not found in source; leaving it as-is.")
            continue
        reltype = rel.reltype
        try:
            if rel.is_external and reltype == RT.HYPERLINK:
                # External hyperlink: only the URL is needed.
                mapping[old_rid] = dst_doc_part.relate_to(
                    rel.target_ref, reltype, is_external=True)
            elif not rel.is_external and reltype == RT.IMAGE:
                # Image: copy the image bytes into the target package.
                mapping[old_rid], _ = dst_doc_part.get_or_add_image(
                    BytesIO(rel.target_part.blob))
            else:
                print(f"  [!] Relationship {old_rid!r} ({reltype}) is not copied by "
                      "the merge helper; formatting/objects may be lost.")
        except Exception as e:
            print(f"  [!] Could not copy relationship {old_rid!r}: {e}")
    return mapping


def _remap_rels(element, mapping):
    for el in element.iter():
        for attr in (qn("r:id"), qn("r:embed"), qn("r:link")):
            old_rid = el.attrib.get(attr)
            if old_rid in mapping:
                el.attrib[attr] = mapping[old_rid]


def merge_docx_files_python_docx(source_dir, output_dir, docx_files=None):
    """Merge DOCX files with python-docx.

    Uses the first input file's styles/template, then deep-copies every body
    element from all input files (in order), preserving images/hyperlinks.
    """
    docx_files = docx_files or _existing_files(source_dir, DOCX_FILES)
    if not docx_files:
        print("No template .docx found.")
        return

    output_path = output_dir / "merged.docx"
    print("  Building merged.docx with python-docx ...")

    base = Document(str(docx_files[0]))
    body = base.element.body
    sectPr = body.find(qn("w:sectPr"))

    # Remove the template's body content but keep the trailing section props.
    for child in list(body):
        body.remove(child)
    if sectPr is not None:
        body.append(sectPr)

    for src_path in docx_files:
        src = Document(str(src_path))
        src_body = src.element.body
        copied = 0
        for child in list(src_body):
            if child.tag == qn("w:sectPr"):
                # Section properties are taken from the first template only.
                continue
            new_node = deepcopy(child)
            mapping = _map_rels_for_element(src.part, base.part, new_node)
            _remap_rels(new_node, mapping)
            body.append(new_node)
            copied += 1
        print(f"  Merged {src_path.name}: {copied} body elements")

    _set_compat_mode_16(base)
    base.save(str(output_path))
    print(f"Merged .docx → {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# DOCX inspection (python-docx)
# ---------------------------------------------------------------------------

def inspect_docx(path):
    """Print line/paragraph counts for a .docx using python-docx.

    In documents produced by the generator, each text line is stored as one
    Word paragraph, so ``len(doc.paragraphs)`` is the practical line count.
    Empty paragraphs are counted separately as blank/spacer lines.
    """
    path = str(path)
    if not Path(path).exists():
        raise FileNotFoundError(f"DOCX not found: {path}")

    doc = Document(path)
    paragraphs = doc.paragraphs
    content = [p for p in paragraphs if p.text.strip()]
    # Count explicit page-break markers (a Word page break is <w:br w:type="page"/>).
    page_breaks = len(doc.element.body.xpath('.//w:br[@w:type="page"]'))
    chars = sum(len(p.text) for p in paragraphs)

    print(f"DOCX: {path}")
    print(f"  paragraphs      : {len(paragraphs):,}   (each generated text line = 1 paragraph)")
    print(f"  content lines    : {len(content):,}")
    print(f"  blank lines      : {len(paragraphs) - len(content):,}")
    print(f"  page-break marks : {page_breaks:,}")
    print(f"  tables           : {len(doc.tables):,}")
    print(f"  sections         : {len(doc.sections):,}")
    print(f"  characters       : {chars:,}")
    return {
        "paragraphs": len(paragraphs),
        "content_lines": len(content),
        "blank_lines": len(paragraphs) - len(content),
        "page_breaks": page_breaks,
        "tables": len(doc.tables),
        "sections": len(doc.sections),
        "characters": chars,
    }


def verify_docx(path):
    """Open a DOCX with python-docx and report whether it is readable."""
    try:
        stats = inspect_docx(path)
        print(f"  [OK] python-docx opened {path}")
        return True
    except Exception as e:
        print(f"  [!!] python-docx could NOT open {path}: {e}")
        return False


def check_docx_inputs(source_dir):
    """Quickly report whether the DOCX inputs can be opened by python-docx."""
    if not HAS_PYTHON_DOCX:
        return
    for path in _existing_files(source_dir, DOCX_FILES):
        try:
            doc = Document(str(path))
            paras = len(doc.paragraphs)
            print(f"  [i] input OK: {path.name} (paragraphs={paras:,}, tables={len(doc.tables):,})")
        except Exception as e:
            print(f"  [!!] input is NOT a valid/readable DOCX: {path}")
            print(f"       {e}")


# ---------------------------------------------------------------------------
# Legacy SAX fallback (used only when python-docx is unavailable)
# ---------------------------------------------------------------------------

class BodyExtractor(ContentHandler):
    def __init__(self, out_buffer):
        self.out = out_buffer
        self.depth = 0
        self.in_body = False
        self.body_closed = False
        self._buf = []

    def _flush_buf(self):
        if self._buf:
            self.out.write("".join(self._buf).encode("utf-8"))
            self._buf.clear()

    def startElement(self, name, attrs):
        if self.body_closed:
            return
        if name == "w:body":
            self.in_body = True
            self.depth = 1
            return
        if not self.in_body:
            return
        self.depth += 1
        tag = ["<", name]
        for k, v in attrs.items():
            tag.append(" ")
            tag.append(k)
            tag.append('="')
            tag.append(v.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;"))
            tag.append('"')
        tag.append(">")
        self._buf.append("".join(tag))

    def endElement(self, name):
        if self.body_closed:
            return
        if name == "w:body":
            self.in_body = False
            self.body_closed = True
            self._flush_buf()
            return
        if not self.in_body:
            return
        self.depth -= 1
        self._buf.append("</")
        self._buf.append(name)
        self._buf.append(">")
        if self.depth == 1:
            self._flush_buf()

    def characters(self, content):
        if not self.in_body or self.body_closed:
            return
        self._buf.append(
            content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        if len(self._buf) > 500:
            self._flush_buf()


class Utf8ChunkDecoder:
    """Feeds a SAX parser from a byte stream in chunks, handling split bytes."""

    def __init__(self, byte_stream, chunk_size=65536):
        self.stream = byte_stream
        self.chunk_size = chunk_size
        self._carry = b""

    def __iter__(self):
        while True:
            chunk = self.stream.read(self.chunk_size)
            if not chunk:
                if self._carry:
                    yield self._carry.decode("utf-8", errors="ignore")
                break
            data = self._carry + chunk
            cutoff = len(data)
            self._carry = b""
            for i in range(min(4, len(data)), 0, -1):
                b = data[-i]
                if b < 0x80 or b >= 0xC0:
                    cutoff = len(data) - i + 1
                    try:
                        data[:cutoff].decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                    self._carry = data[cutoff:]
                    break
            else:
                self._carry = data
                continue
            try:
                yield data[:cutoff].decode("utf-8")
            except UnicodeDecodeError:
                # Extremely unlikely after the boundary check; drop silently.
                continue


def extract_body_streaming(docx_path, out_buffer):
    with zipfile.ZipFile(docx_path, "r") as zin:
        with zin.open("word/document.xml") as xml_stream:
            parser = make_parser()
            parser.setFeature(feature_external_ges, False)
            parser.setFeature(feature_external_pes, False)
            handler = BodyExtractor(out_buffer)
            parser.setContentHandler(handler)
            decoder = Utf8ChunkDecoder(xml_stream, chunk_size=65536)
            for text_chunk in decoder:
                parser.feed(text_chunk)
            parser.close()


def merge_docx_files_sax(source_dir, output_dir):
    """Legacy raw-XML fallback merge (no python-docx)."""
    docx_files = _existing_files(source_dir, DOCX_FILES)
    if not docx_files:
        print("No template .docx found.")
        return

    print(f"  Building merged.docx with SAX fallback ...")
    temp_body_file = output_dir / ".merged_body_temp.xml"
    temp_docxml = output_dir / ".merged_document.xml"

    try:
        with open(temp_body_file, "wb") as body_out:
            for src in docx_files:
                print(f"  Streaming body from {src.name} ...")
                extract_body_streaming(src, body_out)

        with open(temp_docxml, "wb") as dout:
            with zipfile.ZipFile(str(docx_files[0]), "r") as zin:
                template_xml = zin.read("word/document.xml").decode("utf-8", errors="replace")
            start = template_xml.find("<w:body>")
            end = template_xml.find("</w:body>")
            if start == -1 or end == -1:
                raise ValueError("Template has no <w:body> ... </w:body>")
            dout.write(template_xml[:start + 8].encode("utf-8"))
            with open(temp_body_file, "rb") as body_in:
                shutil.copyfileobj(body_in, dout)
            dout.write(template_xml[end:].encode("utf-8"))

        final_path = output_dir / "merged.docx"
        with zipfile.ZipFile(str(docx_files[0]), "r") as zin:
            with zipfile.ZipFile(str(final_path), "w", zipfile.ZIP_DEFLATED) as zout:
                for item in zin.infolist():
                    if item.filename == "word/document.xml":
                        with open(temp_docxml, "rb") as f:
                            zout.writestr(item, f.read())
                    else:
                        with zin.open(item) as fsrc:
                            zout.writestr(item, fsrc.read())
        print(f"Merged .docx → {final_path}")
        return final_path
    finally:
        temp_body_file.unlink(missing_ok=True)
        temp_docxml.unlink(missing_ok=True)


def merge_docx_files(source_dir, output_dir):
    if HAS_PYTHON_DOCX:
        return merge_docx_files_python_docx(source_dir, output_dir)
    print("[!] python-docx is not installed; using the SAX fallback merge.")
    return merge_docx_files_sax(source_dir, output_dir)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Merge large TXT/DOCX chunks and inspect DOCX line counts.")
    parser.add_argument("source_dir", nargs="?", default=".",
                        help="Directory containing c1to700/c701to1k/ending files (default: cwd)")
    parser.add_argument("--inspect", nargs="+", metavar="DOCX",
                        help="Inspect DOCX paragraph/line counts instead of merging")
    parser.add_argument("--verify", action="store_true",
                        help="After merging, open merged.docx again with python-docx")
    parser.add_argument("--txt-only", action="store_true",
                        help="Merge TXT files only")
    parser.add_argument("--docx-only", action="store_true",
                        help="Merge DOCX files only")
    args = parser.parse_args(argv)

    if args.inspect:
        failed = False
        for path in args.inspect:
            try:
                inspect_docx(path)
            except Exception as e:
                print(f"[!!] Could not inspect {path}: {e}")
                failed = True
        return 1 if failed else 0

    if not HAS_PYTHON_DOCX:
        print("[i] python-docx not found. Install it with:")
        print("    pip install python-docx")
        print("    Alpine Linux: apk add py3-lxml && pip install python-docx")
        print("    (the TXT merge will still work; DOCX merge will use the SAX fallback)")

    source_dir = Path(args.source_dir).resolve()
    output_dir = source_dir / "output"
    output_dir.mkdir(exist_ok=True)
    print(f"Source: {source_dir}")
    print(f"Output: {output_dir}")

    if args.txt_only:
        merge_txt_files(source_dir, output_dir)
    elif args.docx_only:
        check_docx_inputs(source_dir)
        merge_docx_files(source_dir, output_dir)
    else:
        merge_txt_files(source_dir, output_dir)
        check_docx_inputs(source_dir)
        merge_docx_files(source_dir, output_dir)

    if args.verify:
        merged = output_dir / "merged.docx"
        if merged.exists():
            verify_docx(merged)

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
