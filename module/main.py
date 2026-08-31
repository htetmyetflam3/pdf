"""
Description: Core orchestrator for Myanmar PDF extraction pipeline.
Reads: PDF bytes, output path, option flags.
Processes: metadata → extract → detect → convert → post-process → write output.
Outputs: file on disk, or returns result dict for frontend use.
Can be called from CLI (cli.py) or directly from a web handler.
"""
import os
from pathlib import Path
from typing import Callable

from .metadata import extract_pdf_metadata
from .prase import extract_pdf
from .detector import Detector
from .unicoding import Rabbit
from .postprocessor import postprocess, clean_imposters, reorder_marks
from .formatter import write_output


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


def run_pipeline(pdf_bytes: bytes, out_path: str, pdf_name: str = "",
                 *, no_convert: bool = False, no_postprocess: bool = False,
                 on_progress: Callable | None = None) -> ExtractorResult:
    """
    Run the full extraction pipeline.

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

    Returns
    -------
    ExtractorResult
    """
    # 0. Extract metadata with pdfminer (fail-soft — parser does not need it)
    script_dir = Path(__file__).resolve().parent
    meta_out_dir = script_dir.parent / "output"
    meta_out_dir.mkdir(parents=True, exist_ok=True)

    try:
        metadata = extract_pdf_metadata(pdf_bytes, out_dir=meta_out_dir)
    except Exception as e:
        print(f"[!] Metadata extraction failed ({e}); continuing without it")
        metadata = {}

    # 1. Extract text with custom parser, guided by metadata
    res = extract_pdf(pdf_bytes, metadata=metadata or None, on_progress=on_progress)

    all_texts = res["pages"]
    meta = res["metadata"]
    page_layouts = list(res.get("page_layouts") or [])

    # 2. Detect + Convert (keep layout runs in sync so DOCX stays font-aware)
    zg_count = uc_count = other_count = 0
    if not no_convert:
        model_path = Path(__file__).resolve().parent / "model" / "zawgyiUnicodeModel.dat"
        detector = Detector(model_path=str(model_path))
        converted = []
        for i, txt in enumerate(all_texts):
            category, prob = detector.detect(txt)
            if category == "ZAWGYI":
                new_txt = Rabbit.zg2uni(txt)
                zg_count += 1
            elif category == "UNICODE":
                new_txt = txt
                uc_count += 1
            else:
                new_txt = txt
                other_count += 1
            converted.append(new_txt)
            if category == "ZAWGYI" and i < len(page_layouts):
                page_layouts[i] = _apply_to_layout_page(page_layouts[i], Rabbit.zg2uni)
        all_texts = converted
    else:
        uc_count = len(all_texts)

    # 3. Post-process
    if not no_postprocess:
        all_texts = postprocess(all_texts)

        def _pp(s: str) -> str:
            if not s:
                return s
            return reorder_marks(clean_imposters(s))

        page_layouts = [_apply_to_layout_page(layout, _pp) for layout in page_layouts]

    # 4. Ensure output dir exists
    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # Merge catalog info with structural metadata the writer needs.
    writer_meta = {}
    writer_meta.update((metadata or {}).get("info") or {})
    writer_meta.update(meta or {})
    if (metadata or {}).get("page_size"):
        writer_meta["page_size"] = metadata["page_size"]
    if (metadata or {}).get("font_map"):
        writer_meta["font_map"] = metadata["font_map"]
    writer_meta["page_count"] = res["pageCount"]

    # 5. Write output
    write_output(all_texts, out_path, pdf_name or out_path, writer_meta, page_layouts)

    return ExtractorResult(
        metadata=writer_meta,
        pages=all_texts,
        page_count=res["pageCount"],
        total_characters=res["totalCharacters"],
        zawgyi_count=zg_count,
        unicode_count=uc_count,
        other_count=other_count,
        output_path=out_path,
    )
