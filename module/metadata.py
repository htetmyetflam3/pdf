"""
Description: PDF metadata extractor using pdfminer.six.
Reads: PDF bytes.
Processes: Page tree, MediaBox, fonts (resolving indirect objects), images.
Outputs: Metadata dict passed to parser and writer.
Writes: JSON metadata file to ../output/ for inspection.

Note: pdfminer layout text is not used for extraction — Zawgyi/CID fonts
need the custom parser in prase.py. This module only supplies structure
(page size, font map, images) so parsing and DOCX output can stay font-aware.
"""

from __future__ import annotations

import json
import re
from io import BytesIO
from pathlib import Path

from pdfminer.pdfparser import PDFParser
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfpage import PDFPage
from pdfminer.pdftypes import PDFObjRef, PDFStream, resolve1
from pdfminer.psparser import PSLiteral


def _unescape_pdf_name(name: str) -> str:
    """Decode PDF name hex escapes (Times#20New#20Roman → Times New Roman)."""
    return re.sub(r"#([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), name or "")


def _decode_pdf_text(value) -> str:
    """Decode Info-dict strings, including UTF-16BE with BOM (þÿ...)."""
    if isinstance(value, bytes):
        if value.startswith((b"\xfe\xff", b"\xff\xfe")):
            try:
                return value.decode("utf-16").strip("\ufeff")
            except Exception:
                pass
        try:
            return value.decode("utf-8")
        except Exception:
            value = value.decode("latin-1", "replace")
    text = value if isinstance(value, str) else str(value)
    if text.startswith("þÿ"):
        try:
            return text.encode("latin-1").decode("utf-16-be").strip("\ufeff")
        except Exception:
            return text
    if text.startswith("\ufeff"):
        return text.lstrip("\ufeff")
    return text


def _ps_name(obj) -> str:
    """Best-effort string from a pdfminer name / literal / bytes / ref."""
    try:
        obj = resolve1(obj)
    except Exception:
        pass
    if obj is None:
        return "Unknown"
    if isinstance(obj, PSLiteral):
        return _unescape_pdf_name(obj.name)
    if isinstance(obj, bytes):
        return _unescape_pdf_name(obj.decode("latin-1", "replace"))
    return _unescape_pdf_name(str(obj).lstrip("/"))


def _as_dict(obj) -> dict:
    """Resolve indirect objects until we have a dict (or {})."""
    try:
        obj = resolve1(obj)
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _as_list(obj) -> list:
    try:
        obj = resolve1(obj)
    except Exception:
        return []
    if obj is None:
        return []
    if isinstance(obj, (list, tuple)):
        return list(obj)
    return []


def _to_jsonable(obj, depth: int = 0):
    """Coerce pdfminer objects into JSON-serializable Python types."""
    if depth > 16:
        return str(obj)
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, bytes):
        try:
            return obj.decode("utf-8")
        except Exception:
            return obj.decode("latin-1", "replace")
    if isinstance(obj, PSLiteral):
        return obj.name
    if isinstance(obj, PDFObjRef):
        try:
            return _to_jsonable(resolve1(obj), depth + 1)
        except Exception:
            return f"R:{getattr(obj, 'objid', '?')}"
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            out[str(_to_jsonable(k, depth + 1))] = _to_jsonable(v, depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(x, depth + 1) for x in obj]
    try:
        return float(obj)
    except Exception:
        return str(obj)


def _encoding_name(enc) -> str:
    enc = resolve1(enc) if enc is not None else None
    if enc is None:
        return "Unknown"
    if isinstance(enc, dict):
        return _ps_name(enc.get("BaseEncoding", "Custom"))
    return _ps_name(enc)


def _mediabox_list(mb) -> list[float]:
    vals = _as_list(mb)
    if len(vals) >= 4:
        try:
            return [float(vals[0]), float(vals[1]), float(vals[2]), float(vals[3])]
        except Exception:
            pass
    return [0.0, 0.0, 612.0, 792.0]


def _register_font(font_map: dict, ref_name: str, font: dict) -> dict:
    """Build a page-font record and index it in font_map under several keys."""
    full_name = _ps_name(font.get("BaseFont", "Unknown"))
    family = full_name.split("+")[-1] if full_name else "Unknown"
    subtype = _ps_name(font.get("Subtype", "Unknown"))
    encoding = _encoding_name(font.get("Encoding"))
    entry = {
        "family": family,
        "full_name": full_name,
        "size": 12,
        "subtype": subtype,
        "encoding": encoding,
    }
    keys = {ref_name, ref_name.lstrip("/"), family, full_name}
    if not ref_name.startswith("/"):
        keys.add("/" + ref_name)
    for key in keys:
        if key:
            font_map[str(key)] = entry
    return {
        "ref": ref_name.lstrip("/"),
        "name": full_name,
        "subtype": subtype,
        "encoding": encoding,
        "family": family,
    }


def _page_images(resources: dict) -> list[dict]:
    images = []
    xobjects = _as_dict(resources.get("XObject"))
    for name, ref in xobjects.items():
        try:
            xobj = resolve1(ref)
            getter = xobj.get if isinstance(xobj, (dict, PDFStream)) else None
            if getter is None:
                continue
            if _ps_name(getter("Subtype")) != "Image":
                continue
            images.append({
                "name": _ps_name(name),
                "width": float(getter("Width") or 0),
                "height": float(getter("Height") or 0),
            })
        except Exception:
            continue
    return images


def extract_pdf_metadata(pdf_bytes: bytes, out_dir: str | Path | None = None,
                         max_pages: int | None = None) -> dict:
    """
    Extract structural metadata from PDF using pdfminer.

    Parameters
    ----------
    pdf_bytes : bytes
        Raw PDF file bytes.
    out_dir : str | Path | None
        If given, write metadata JSON to this directory.
    max_pages : int | None
        Stop walking the page tree after this many pages. pdfminer's
        PDFPage.create_pages() resolves the tree lazily per page, so a cap
        genuinely skips work — on a 14k-page file a full walk costs minutes
        while the first ~20 pages already determine page size + font names.
        The custom parser in prase.py resolves everything else per page from
        raw objects.

    Returns
    -------
    dict
        {
            "page_count": int,
            "page_size": {"width": float, "height": float, "unit": "pt"},
            "pages": [...],
            "info": {...},
            "font_map": {...}
        }
    """
    empty = {
        "page_count": 0,
        "page_size": {"width": 595.0, "height": 842.0, "unit": "pt"},
        "pages": [],
        "info": {},
        "font_map": {},
    }

    try:
        stream = BytesIO(pdf_bytes)
        parser = PDFParser(stream)
        doc = PDFDocument(parser)
    except Exception as e:
        empty["error"] = str(e)
        _write_metadata_json(empty, out_dir)
        print(f"[!] Metadata parse failed: {e}")
        return empty

    info = {}
    try:
        if doc.info:
            for k, v in doc.info[0].items():
                try:
                    info[_ps_name(k)] = _decode_pdf_text(_to_jsonable(v))
                except Exception:
                    info[str(k)] = str(v)
    except Exception:
        pass

    pages_data = []
    font_map: dict = {}
    font_obj_cache: dict = {}
    mediabox = [0.0, 0.0, 612.0, 792.0]
    first_mediabox: list | None = None
    truncated = False

    try:
        for page_num, page in enumerate(PDFPage.create_pages(doc), 1):
            if max_pages is not None and page_num > max_pages:
                truncated = True
                break
            if page_num == 1:
                first_mediabox = _mediabox_list(getattr(page, "mediabox", None))
            try:
                mediabox = _mediabox_list(getattr(page, "mediabox", None))
                resources = getattr(page, "resources", None)
                if isinstance(resources, PDFObjRef):
                    resources = resolve1(resources)
                resources = resources if isinstance(resources, dict) else {}

                page_fonts = []
                fonts = resources.get("Font", {})
                # The actual bug: /Font is often an indirect object, not a dict.
                fonts = _as_dict(fonts)
                for font_ref, font_obj in fonts.items():
                    try:
                        cache_key = None
                        if isinstance(font_obj, PDFObjRef):
                            cache_key = (font_obj.objid, getattr(font_obj, "genno", 0))
                            font = font_obj_cache.get(cache_key)
                            if font is None:
                                font = _as_dict(font_obj)
                                font_obj_cache[cache_key] = font
                        else:
                            font = _as_dict(font_obj)
                        if not font:
                            continue
                        page_fonts.append(_register_font(font_map, _ps_name(font_ref), font))
                    except Exception:
                        continue

                pages_data.append({
                    "page_num": page_num,
                    "mediabox": mediabox,
                    "fonts": page_fonts,
                    "images": _page_images(resources),
                })
            except Exception:
                pages_data.append({
                    "page_num": page_num,
                    "mediabox": mediabox,
                    "fonts": [],
                    "images": [],
                })
    except Exception as e:
        empty["error"] = str(e)
        empty["info"] = info
        empty["font_map"] = font_map
        empty["pages"] = pages_data
        empty["page_count"] = len(pages_data)
        _write_metadata_json(empty, out_dir)
        print(f"[!] Metadata page walk failed: {e}")
        return empty

    mb = first_mediabox or mediabox
    width = float(mb[2] - mb[0]) if mb else 595.0
    height = float(mb[3] - mb[1]) if mb else 842.0

    metadata = {
        "page_count": len(pages_data),
        "page_size": {
            "width": width if width > 0 else 595.0,
            "height": height if height > 0 else 842.0,
            "unit": "pt",
        },
        "pages": pages_data,
        "info": info,
        "font_map": font_map,
    }
    if truncated:
        # pages[] only holds the first `max_pages` entries; the real page count
        # comes from the parser (res["pageCount"]).
        metadata["pages_truncated"] = True

    _write_metadata_json(metadata, out_dir)
    return metadata


def _write_metadata_json(metadata: dict, out_dir: str | Path | None) -> None:
    if not out_dir:
        return
    try:
        out_path = Path(out_dir) / "metadata.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = _to_jsonable(metadata)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        print(f"[+] Metadata saved: {out_path}")
    except Exception as e:
        print(f"[!] Could not write metadata.json: {e}")


def load_metadata(out_dir: str | Path) -> dict:
    """Load previously saved metadata JSON."""
    out_path = Path(out_dir) / "metadata.json"
    if not out_path.exists():
        raise FileNotFoundError(f"Metadata not found: {out_path}")
    with open(out_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_page_size(metadata: dict | None) -> tuple[float, float]:
    """Return (width, height) in points."""
    if not metadata:
        return 595.0, 842.0
    ps = metadata.get("page_size") or {}
    return float(ps.get("width", 595) or 595), float(ps.get("height", 842) or 842)


# Module-level: built and sorted ONCE (map_font_to_ttf used to rebuild and
# re-sort this on every call — it runs once per DOCX run, thousands per file).
_FONT_TTF_MAPPINGS = {
    "Amyanmar": "Unicode/YoeYar-One_Bold.ttf",
    "Arlarwade": "Unicode/Arlarwade.ttf",
    "Gautami": "Unicode/Gautami.ttf",
    "Zawgyi": "Unicode/YoeYar-One_Regular.ttf",
    "YoeYar": "Unicode/YoeYar-One_Regular.ttf",
    "Pyidaungsu": "Unicode/Pyidaungsu_Regular.ttf",
    "Padauk": "Unicode/Padauk.ttf",
    "Myanmar": "Unicode/MyanmarText_Regular.ttf",
    "Times-Bold": "AnonymousPro/AnonymousPro-Bold.ttf",
    "Times New Roman": "AnonymousPro/AnonymousPro-Regular.ttf",
    "Times-Roman": "AnonymousPro/AnonymousPro-Regular.ttf",
    "Times": "AnonymousPro/AnonymousPro-Regular.ttf",
    "Anonymous": "AnonymousPro/AnonymousPro-Regular.ttf",
}
_FONT_TTF_SORTED = sorted(
    ((k.lower(), v) for k, v in _FONT_TTF_MAPPINGS.items()),
    key=lambda kv: -len(kv[0]),
)


def map_font_to_ttf(font_name: str, metadata: dict | None) -> str | None:
    """
    Map a pdfminer / parser font name to a TTF file path under fonts/.
    Returns None if no mapping found.
    """
    font_map = (metadata or {}).get("font_map") or {}
    fm = font_map.get(font_name, {}) if font_name else {}
    family = fm.get("family") or font_name or ""

    family_l = family.lower()
    name_l = (font_name or "").lower()
    for kl, path in _FONT_TTF_SORTED:
        if kl in family_l or kl in name_l:
            return path
    return None
