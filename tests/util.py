"""Shared helpers for the test suite. Stdlib only — no pytest, no PyMuPDF."""

import os
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "input" / "pdf" / "test.pdf"

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

_cache = {}


def fixture_bytes() -> bytes:
    """The fixture PDF, read once per process (it is 2,294 pages)."""
    if "pdf" not in _cache:
        _cache["pdf"] = FIXTURE.read_bytes()
    return _cache["pdf"]


def regression_pdf(name: str) -> Path | None:
    """A large regression PDF if it is present, else None (they are optional)."""
    p = REPO_ROOT / "input" / "pdf" / f"{name}.pdf"
    return p if p.exists() else None


def document_xml(docx_path) -> bytes:
    with zipfile.ZipFile(docx_path) as z:
        return z.read("word/document.xml")


def all_parts_parse(docx_path) -> list[str]:
    """Parse every part of the package; returns the list of part names."""
    names = []
    with zipfile.ZipFile(docx_path) as z:
        for name in z.namelist():
            ET.fromstring(z.read(name))
            names.append(name)
    return names


def count_page_breaks(xml: bytes) -> int:
    return xml.count(b"<w:pageBreakBefore/>")


def count_sect_pr(xml: bytes) -> int:
    return xml.count(b"<w:sectPr")


def page_sizes(xml: bytes) -> list[bytes]:
    """Every <w:pgSz .../> in document order."""
    return re.findall(rb"<w:pgSz[^/]*/>", xml)


def docx_page_count(xml: bytes) -> int:
    """Word pages implied by the XML.

    One page to begin with, then one more for every explicit page break and
    one more for every section break (a section break starts a new page too,
    except for the final body-level sectPr, which ends the document rather
    than starting anything).
    """
    return 1 + count_page_breaks(xml) + max(0, count_sect_pr(xml) - 1)


def paragraph_texts(xml: bytes) -> list[str]:
    """The text of every <w:p>, in order."""
    root = ET.fromstring(xml)
    out = []
    for p in root.iter(f"{{{W_NS}}}p"):
        out.append("".join(t.text or "" for t in p.iter(f"{{{W_NS}}}t")))
    return out
