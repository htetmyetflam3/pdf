"""
Description: Core orchestrator for Myanmar PDF extraction pipeline.
Reads: PDF bytes, output path, option flags.
Processes: extract → detect → convert → post-process → write output.
Outputs: file on disk, or returns result dict for frontend use.
Can be called from CLI (cli.py) or directly from a web handler.
"""
import re
import os
from typing import Callable
from .prase import extract_pdf
from .detector import Detector
from .unicoding import Rabbit
from .postprocessor import postprocess
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
        Destination file path (.txt / .docx / .pdf).
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
    # 1. Extract
    res = extract_pdf(pdf_bytes, on_progress=on_progress)

    all_texts = res["pages"]
    metadata = res["metadata"]

    # 2. Detect + Convert
    zg_count = uc_count = other_count = 0
    if not no_convert:
        detector = Detector()
        converted = []
        for txt in all_texts:
            category, prob = detector.detect(txt)
            if category == "ZAWGYI":
                converted.append(Rabbit.zg2uni(txt))
                zg_count += 1
            elif category == "UNICODE":
                converted.append(txt)
                uc_count += 1
            else:
                converted.append(txt)
                other_count += 1
        all_texts = converted
    else:
        uc_count = len(all_texts)

    # 3. Post-process
    if not no_postprocess:
        all_texts = postprocess(all_texts)

    # 4. Ensure output dir exists
    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # 5. Write output
    write_output(all_texts, out_path, pdf_name or out_path, metadata)

    return ExtractorResult(
        metadata=metadata,
        pages=all_texts,
        page_count=res["pageCount"],
        total_characters=res["totalCharacters"],
        zawgyi_count=zg_count,
        unicode_count=uc_count,
        other_count=other_count,
        output_path=out_path,
    )
