"""Stdlib-only PDF page-shape scanner, used by the tests as ground truth.

Deliberately independent of the writer and of any third-party PDF library
(PyMuPDF in particular is not installable on the target machine and is not a
dependency of this project): it goes straight to the repo's own object store
and reads /MediaBox, /Rotate and /XObject for every page. It walks all 2,294
pages of the fixture in about a tenth of a second, so a test can afford to
assert against the real file rather than a recorded snapshot.
"""

from module.prase import (
    open_document, iter_page_refs, parse_mediabox, parse_rotation,
    page_has_image,
)


def scan_page_shapes(pdf_bytes: bytes) -> list[dict]:
    """Return one record per page: number, mediabox, rotation, has_image."""
    doc = open_document(pdf_bytes)
    out = []
    for i, (num, gen) in enumerate(iter_page_refs(doc.objects, doc.pages_obj)):
        mb = parse_mediabox(doc.objects, num, gen)
        out.append({
            "page": i + 1,
            "mediabox": mb,
            "width": (mb[2] - mb[0]) if mb else None,
            "height": (mb[3] - mb[1]) if mb else None,
            "rotation": parse_rotation(doc.objects, num, gen),
            "has_image": page_has_image(doc.objects, num, gen),
        })
    return out


def odd_pages(shapes: list[dict], base=(612.0, 792.0)) -> list[dict]:
    """Every page that differs from the document's plain-portrait baseline."""
    return [s for s in shapes
            if s["rotation"] or s["has_image"]
            or round(s["width"] or 0, 1) != base[0]
            or round(s["height"] or 0, 1) != base[1]]
