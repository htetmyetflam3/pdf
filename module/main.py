"""
Description: Core orchestrator for Myanmar PDF extraction pipeline.
Reads: PDF bytes, output path, option flags.
Processes: open → per page (read → detect → convert → post-process → write).
Outputs: file on disk, or returns result dict for frontend use.
Can be called from CLI (cli.py) or directly from a web handler.

RESTRUCTURED
------------
This used to run six full-document passes before writing a single byte:
pdfminer metadata prescan (capped at 20 pages), a whole-file object parse, a
page loop accumulating every page's text AND layout, a detect/convert pass
over all pages, a post-process pass over all pages, then the writer.

It is now linear: each stage below is a function that handles ONE page, and
run_pipeline() composes them in a single walk. The pdfminer prescan and its
_META_SCAN_PAGES cap are gone — prase resolves per-page fonts and mediabox
from raw objects, exactly and for every page, so the 20-page scope that made
pages 1-20 take a different code path than pages 21+ no longer exists.
"""
import os
from pathlib import Path
from typing import Callable

from .prase import open_document, iter_pdf_pages, page_size_from_mediabox, extract_pdf
from .detector import Detector
from .unicoding import Rabbit
from .postprocessor import postprocess, clean_imposters, reorder_marks
from .formatter import write_output, open_stream_writer


class ExtractorResult:
    """Holds the full pipeline result for inspection or API responses."""
    def __init__(self, metadata, pages, page_count, total_characters,
                 zawgyi_count, unicode_count, other_count, output_path):
        self.metadata = metadata
        self.pages = pages
        self.page_count = page_count
        self.total_characters = total_characters
        self.zawgyi_count = zawgyi_count
        self.unicode_count = unicode_count
        self.other_count = other_count
        self.output_path = output_path


def _apply_to_layout_page(layout: dict | None, fn) -> dict | None:
    """Run a text transform on every line/run of one page layout."""
    if not layout:
        return layout
    out = dict(layout)
    new_lines = []
    for line in layout.get("lines") or []:
        line2 = dict(line)
        line2["text"] = fn(line.get("text") or "")
        if "runs" in line:
            line2["runs"] = [{**r, "text": fn(r.get("text") or "")} for r in line["runs"]]
        new_lines.append(line2)
    out["lines"] = new_lines
    return out


# ---------------------------------------------------------------------------
# Stage functions — each takes ONE page and returns it transformed.
# ---------------------------------------------------------------------------

def make_detector() -> Detector:
    """Stage 0: build the Zawgyi/Unicode detector once."""
    model_path = Path(__file__).resolve().parent / "model" / "zawgyiUnicodeModel.dat"
    return Detector(model_path=str(model_path))


def detect_page(detector: Detector, text: str) -> tuple[str, float]:
    """Stage 1 (one page): classify encoding."""
    return detector.detect(text)


def convert_page(text: str, layout: dict, category: str) -> tuple[str, dict]:
    """Stage 2 (one page): Zawgyi -> Unicode, keeping layout runs in sync."""
    if category != "ZAWGYI":
        return text, layout
    return Rabbit.zg2uni(text), _apply_to_layout_page(layout, Rabbit.zg2uni)


def postprocess_page(text: str, layout: dict) -> tuple[str, dict]:
    """Stage 3 (one page): imposter cleanup + mark reordering."""
    def _pp(s: str) -> str:
        if not s:
            return s
        return reorder_marks(clean_imposters(s))

    return _pp(text), _apply_to_layout_page(layout, _pp)


def build_writer_meta(doc, page_size=None, font_map=None) -> dict:
    """Stage 4: assemble the metadata dict the writer consumes."""
    writer_meta = {}
    writer_meta.update(doc.meta or {})
    ps = page_size or page_size_from_mediabox(doc.mediabox)
    if ps:
        writer_meta["page_size"] = ps
    fm = font_map if font_map is not None else doc.font_map
    if fm:
        writer_meta["font_map"] = fm
    writer_meta["page_count"] = doc.page_count
    return writer_meta


def process_page(detector, page: dict, *, no_convert: bool,
                 no_postprocess: bool) -> dict:
    """Run every per-page stage in order for a single page."""
    text = page["text"]
    layout = page["layout"]

    category = "UNICODE"
    if not no_convert:
        category, _prob = detect_page(detector, text)
        text, layout = convert_page(text, layout, category)

    if not no_postprocess:
        text, layout = postprocess_page(text, layout)

    return {"index": page["index"], "text": text, "layout": layout,
            "category": category}


def ensure_out_dir(out_path: str) -> None:
    """Stage 5: make sure the destination directory exists."""
    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)


# ---------------------------------------------------------------------------
# Linear driver
# ---------------------------------------------------------------------------

def run_pipeline(pdf_bytes: bytes, out_path: str, pdf_name: str = "",
                 *, no_convert: bool = False, no_postprocess: bool = False,
                 on_progress: Callable | None = None,
                 stream: bool = True, keep_pages: bool = True) -> ExtractorResult:
    """
    Run the full extraction pipeline as one linear per-page walk.

    Parameters
    ----------
    pdf_bytes : bytes
        Raw PDF file bytes.
    out_path : str
        Destination file path (.txt / .docx).
    pdf_name : str
        Original filename (for metadata header).
    no_convert : bool
        Skip Zawgyi→Unicode conversion.
    no_postprocess : bool
        Skip imposter cleanup + mark reordering.
    on_progress : callable
        Called with {"done": int, "total": int} per page.
    stream : bool
        Write each page as it is produced instead of buffering the document.
    keep_pages : bool
        Keep page text in the result. Set False for minimum memory on huge
        files; counters are still accurate.

    Returns
    -------
    ExtractorResult
    """
    ensure_out_dir(out_path)

    # 1. Document-level context only: trailer, catalog, page tree, font tables.
    doc = open_document(pdf_bytes)

    detector = None if no_convert else make_detector()

    zg_count = uc_count = other_count = 0
    total_chars = 0
    kept_pages: list[str] = []

    writer = open_stream_writer(
        out_path, pdf_name or out_path, doc, enabled=stream)

    # RESTRUCTURED: these used to hold every page a second time whenever the
    # writer was disabled. open_stream_writer() always returns a writer now —
    # streaming is the only way a document is written — so nothing accumulates.
    buffered_texts: list[str] = []
    buffered_layouts: list[dict] = []

    # 2. One page at a time: read -> detect -> convert -> post-process -> write.
    for page in iter_pdf_pages(pdf_bytes, doc=doc, on_progress=on_progress):
        done = process_page(detector, page, no_convert=no_convert,
                            no_postprocess=no_postprocess)

        if no_convert:
            uc_count += 1
        elif done["category"] == "ZAWGYI":
            zg_count += 1
        elif done["category"] == "UNICODE":
            uc_count += 1
        else:
            other_count += 1

        total_chars += len(done["text"])
        if keep_pages:
            kept_pages.append(done["text"])

        if writer is not None:
            writer.write_page(done["text"], done["layout"])
        else:
            buffered_texts.append(done["text"])
            buffered_layouts.append(done["layout"])
        # `page` and `done` go out of scope here — nothing document-sized is held.

    # 3. Finalise. font_map/mediabox are complete now that every page was seen.
    writer_meta = build_writer_meta(doc)

    if writer is not None:
        writer.close(writer_meta)
    else:
        write_output(buffered_texts, out_path, pdf_name or out_path,
                     writer_meta, buffered_layouts)

    return ExtractorResult(
        metadata=writer_meta,
        pages=kept_pages,
        page_count=doc.page_count,
        total_characters=total_chars,
        zawgyi_count=zg_count,
        unicode_count=uc_count,
        other_count=other_count,
        output_path=out_path,
    )
