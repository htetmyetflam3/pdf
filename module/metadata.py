"""
Description: PDF metadata extractor using pdfminer.six.
Reads: PDF bytes.
Processes: Extracts page tree, MediaBox, fonts, images, text layout with positions.
Outputs: Metadata dict passed to parser and writer.
Writes: JSON metadata file to ../output/ for inspection.
"""

import json
import os
from pathlib import Path
from typing import BinaryIO

from pdfminer.high_level import extract_text
from pdfminer.pdfparser import PDFParser
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfinterp import PDFResourceManager, PDFPageInterpreter
from pdfminer.layout import LAParams
from pdfminer.converter import PDFPageAggregator


def extract_pdf_metadata(pdf_bytes: bytes, out_dir: str | Path | None = None) -> dict:
    """
    Extract full metadata from PDF using pdfminer.

    Parameters
    ----------
    pdf_bytes : bytes
        Raw PDF file bytes.
    out_dir : str | Path | None
        If given, write metadata JSON to this directory.

    Returns
    -------
    dict
        {
            "page_count": int,
            "page_size": {"width": float, "height": float, "unit": "pt"},
            "pages": [
                {
                    "page_num": int,
                    "mediabox": [x0, y0, x1, y1],
                    "fonts": [{"name": str, "subtype": str, "encoding": str}],
                    "images": [{"x": float, "y": float, "width": float, "height": float}],
                    "text_blocks": [
                        {"x": float, "y": float, "text": str, "font": str, "size": float}
                    ]
                }
            ],
            "info": {title, author, creator, producer, ...},
            "font_map": {"PDF+FontName": {"family": str, "size": float}}
        }
    """
    from io import BytesIO

    stream = BytesIO(pdf_bytes)
    parser = PDFParser(stream)
    doc = PDFDocument(parser)

    # Document info
    info = {}
    if doc.info:
        for k, v in doc.info[0].items():
            try:
                info[k] = str(v.resolve()) if hasattr(v, 'resolve') else str(v)
            except Exception:
                info[k] = str(v)

    rsrcmgr = PDFResourceManager()
    laparams = LAParams(
        line_overlap=0.5,
        char_margin=2.0,
        line_margin=0.5,
        word_margin=0.1,
        boxes_flow=0.5,
        detect_vertical=False,
        all_texts=True,
    )
    device = PDFPageAggregator(rsrcmgr, laparams=laparams)

    pages_data = []
    font_map = {}

    for page_num, page in enumerate(PDFPage.create_pages(doc), 1):
        interpreter = PDFPageInterpreter(rsrcmgr, device)
        interpreter.process_page(page)

        layout = device.get_result()
        mediabox = page.mediabox  # [x0, y0, x1, y1]

        # Extract fonts from resource manager
        page_fonts = []
        if hasattr(page, 'resources') and page.resources:
            fonts = page.resources.get('Font', {})
            for font_ref, font_obj in fonts.items():
                try:
                    font = font_obj.resolve() if hasattr(font_obj, 'resolve') else font_obj
                    name = font.get('BaseFont', 'Unknown')
                    subtype = font.get('Subtype', 'Unknown')
                    encoding = font.get('Encoding', 'Unknown')
                    page_fonts.append({
                        "ref": font_ref,
                        "name": str(name),
                        "subtype": str(subtype),
                        "encoding": str(encoding)
                    })
                    # Build font map for writer
                    font_map[str(font_ref)] = {
                        "family": str(name).split('+')[-1],  # strip subset prefix
                        "full_name": str(name),
                        "size": 12  # default, updated per block
                    }
                except Exception:
                    pass

        # Extract text blocks with positions and fonts
        text_blocks = []
        images = []

        for obj in layout._objs:
            if hasattr(obj, 'get_text'):
                # LTTextBox, LTTextLine, etc.
                text = obj.get_text().strip()
                if text:
                    font_name = "Unknown"
                    font_size = 12
                    try:
                        # Get font from first character
                        for line in obj:
                            for char in line:
                                if hasattr(char, 'fontname'):
                                    font_name = char.fontname
                                    font_size = char.size
                                    break
                            break
                    except Exception:
                        pass

                    text_blocks.append({
                        "x": obj.x0,
                        "y": obj.y0,
                        "width": obj.width,
                        "height": obj.height,
                        "text": text,
                        "font": font_name,
                        "size": font_size
                    })

                    # Update font map with actual size
                    clean_font = font_name.split('+')[-1] if '+' in font_name else font_name
                    if font_name in font_map:
                        font_map[font_name]["size"] = font_size
                        font_map[font_name]["family"] = clean_font

            elif hasattr(obj, 'stream'):
                # LTImage
                images.append({
                    "x": obj.x0,
                    "y": obj.y0,
                    "width": obj.width,
                    "height": obj.height,
                    "name": getattr(obj, 'name', 'unknown')
                })

        pages_data.append({
            "page_num": page_num,
            "mediabox": mediabox,
            "fonts": page_fonts,
            "images": images,
            "text_blocks": text_blocks
        })

    metadata = {
        "page_count": len(pages_data),
        "page_size": {
            "width": float(mediabox[2]) if pages_data else 595,
            "height": float(mediabox[3]) if pages_data else 842,
            "unit": "pt"
        },
        "pages": pages_data,
        "info": info,
        "font_map": font_map
    }

    # Write to file if out_dir given
    if out_dir:
        out_path = Path(out_dir) / "metadata.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)
        print(f"[+] Metadata saved: {out_path}")

    return metadata


def load_metadata(out_dir: str | Path) -> dict:
    """Load previously saved metadata JSON."""
    out_path = Path(out_dir) / "metadata.json"
    if not out_path.exists():
        raise FileNotFoundError(f"Metadata not found: {out_path}")
    with open(out_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_page_size(metadata: dict) -> tuple[float, float]:
    """Return (width, height) in points."""
    ps = metadata.get("page_size", {})
    return ps.get("width", 595), ps.get("height", 842)


def map_font_to_ttf(font_name: str, metadata: dict) -> str | None:
    """
    Map a pdfminer font name to your TTF file path.
    Returns None if no mapping found.
    """
    font_map = metadata.get("font_map", {})
    fm = font_map.get(font_name, {})
    family = fm.get("family", font_name)

    # Your font mappings
    mappings = {
        "Amyanmar": "Unicode/YoeYar-One_Bold.ttf",
        "Arlarwade": "Unicode/Arlarwade.ttf",
        "Gautami": "Unicode/Gautami.ttf",
        "Times": "AnonymousPro/AnonymousPro-Regular.ttf",
        "Times-Bold": "AnonymousPro/AnonymousPro-Bold.ttf",
        "Times-Roman": "AnonymousPro/AnonymousPro-Regular.ttf",
    }

    for key, path in mappings.items():
        if key.lower() in family.lower():
            return path
    return None
