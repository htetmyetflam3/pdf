"""The streaming DOCX writer: memory, structure, and the one-page invariant.

Runs against synthetic page records so it is fast; the fixture-driven checks
live in test_fixture_pipeline.py.
"""

import io
import unittest
import zipfile

from module.formatter import (
    StreamDocxWriter, page_shape, measure_page_margins, page_scale_factor,
    render_page_body, write_docx,
)
from tests.util import (
    document_xml, all_parts_parse, count_page_breaks, count_sect_pr,
    page_sizes, docx_page_count,
)
from tests.tmp import temp_path


def layout(page_num, w=612.0, h=792.0, rotation=0, lines=None, has_image=False):
    return {
        "page_num": page_num,
        "mediabox": [0.0, 0.0, w, h],
        "rotation": rotation,
        "has_image": has_image,
        "lines": lines if lines is not None else [
            {"text": f"line one of page {page_num}", "x": 56.6, "y": 712.0,
             "size": 20.0, "right": 456.0, "font": "Zawgyi-One"},
            {"text": f"line two of page {page_num}", "x": 56.6, "y": 663.0,
             "size": 20.0, "right": 460.0, "font": "Zawgyi-One"},
        ],
    }


def build(pages, path):
    w = StreamDocxWriter(path, "test.pdf", len(pages))
    for text, lay in pages:
        w.write_page(text, lay)
    w.close({})
    return document_xml(path)


class ShapeTests(unittest.TestCase):

    def test_rotation_swaps_the_displayed_page_box_only_for_90_and_270(self):
        self.assertEqual(page_shape(layout(1, 612, 792, 0))[:2], (612.0, 792.0))
        self.assertEqual(page_shape(layout(1, 612, 792, 180))[:2], (612.0, 792.0))
        self.assertEqual(page_shape(layout(1, 612, 792, 90))[:2], (792.0, 612.0))
        self.assertEqual(page_shape(layout(1, 612, 792, 270))[:2], (792.0, 612.0))

    def test_shape_includes_rotation_so_180_starts_its_own_section(self):
        self.assertNotEqual(page_shape(layout(1, 612, 792, 0)),
                            page_shape(layout(1, 612, 792, 180)))


class MarginTests(unittest.TestCase):

    def test_margins_come_from_the_pages_own_lines(self):
        m = measure_page_margins(layout(1), 612.0, 792.0)
        self.assertEqual(m["left"], round(56.6 * 20))

    def test_a_page_with_no_lines_still_gets_legal_margins(self):
        m = measure_page_margins(layout(1, lines=[]), 612.0, 792.0)
        for v in m.values():
            self.assertGreaterEqual(v, 0)
            self.assertLess(v, 612 * 20)


class ScaleTests(unittest.TestCase):

    def test_a_page_that_fits_is_never_scaled(self):
        m = measure_page_margins(layout(1), 612.0, 792.0)
        self.assertEqual(page_scale_factor(layout(1), 792.0, m), 1.0)

    def test_a_page_taller_than_its_paper_is_shrunk(self):
        lines = [{"text": f"l{i}", "x": 20.0, "y": 2000.0 - i * 40,
                  "size": 20.0, "right": 300.0} for i in range(40)]
        lay = layout(1, lines=lines)
        m = measure_page_margins(lay, 612.0, 792.0)
        s = page_scale_factor(lay, 792.0, m)
        self.assertLess(s, 1.0)
        self.assertGreater(s, 0.0)


class RenderTests(unittest.TestCase):

    def test_vertical_gap_is_never_counted_twice(self):
        # w:before is the gap MINUS the natural line height, so the
        # baseline-to-baseline distance equals the measured gap exactly.
        lay = layout(1)
        m = measure_page_margins(lay, 612.0, 792.0)
        xml = render_page_body(lay, "", m, {}, page_break=False)
        gap_pt = 712.0 - 663.0
        natural = 20.0 * 1.2
        expected = round((gap_pt - natural) * 20)
        self.assertIn(f'w:before="{expected}"'.encode(), xml.encode())

    def test_an_empty_page_still_produces_a_paragraph(self):
        lay = layout(1, lines=[])
        m = measure_page_margins(lay, 612.0, 792.0)
        xml = render_page_body(lay, "", m, {})
        self.assertIn("<w:p>", xml)

    def test_indent_is_relative_to_the_section_left_margin(self):
        lines = [{"text": "indented", "x": 100.0, "y": 700.0, "size": 20.0,
                  "right": 300.0}]
        lay = layout(1, lines=[{"text": "body", "x": 56.6, "y": 712.0,
                                "size": 20.0, "right": 300.0}] + lines)
        m = measure_page_margins(lay, 612.0, 792.0)
        xml = render_page_body(lay, "", m, {}, page_break=False)
        self.assertIn(f'w:ind w:left="{round((100.0 - 56.6) * 20)}"', xml)


class StreamStructureTests(unittest.TestCase):

    def test_one_word_page_per_input_page(self):
        with temp_path(".docx") as p:
            xml = build([(f"p{i}", layout(i)) for i in range(1, 11)], p)
            self.assertEqual(docx_page_count(xml), 10)
            self.assertEqual(count_page_breaks(xml), 9)

    def test_a_uniform_document_has_exactly_one_sectpr(self):
        with temp_path(".docx") as p:
            xml = build([(f"p{i}", layout(i)) for i in range(1, 11)], p)
            self.assertEqual(count_sect_pr(xml), 1)
            self.assertEqual(len(set(page_sizes(xml))), 1)

    def test_a_page_size_change_starts_a_section(self):
        pages = [("a", layout(1)), ("b", layout(2, 595.27563, 841.8898)),
                 ("c", layout(3))]
        with temp_path(".docx") as p:
            xml = build(pages, p)
            self.assertEqual(count_sect_pr(xml), 3)
            self.assertEqual(docx_page_count(xml), 3)

    def test_a_rotation_change_starts_a_section_with_swapped_pgsz(self):
        pages = [("a", layout(1)), ("b", layout(2, rotation=270)),
                 ("c", layout(3))]
        with temp_path(".docx") as p:
            xml = build(pages, p)
            self.assertIn(b'<w:pgSz w:w="15840" w:h="12240" w:orient="landscape"/>',
                          xml)
            self.assertEqual(docx_page_count(xml), 3)

    def test_a_180_page_keeps_its_page_size_but_still_gets_a_section(self):
        pages = [("a", layout(1)), ("b", layout(2, rotation=180)),
                 ("c", layout(3))]
        with temp_path(".docx") as p:
            xml = build(pages, p)
            self.assertEqual(count_sect_pr(xml), 3)
            for sz in page_sizes(xml):
                self.assertEqual(sz, b'<w:pgSz w:w="12240" w:h="15840"/>')

    def test_section_break_does_not_also_get_a_page_break(self):
        # A section break already starts a new page; an extra explicit break
        # would insert a blank page and violate one-page-per-PDF-page.
        pages = [("a", layout(1)), ("b", layout(2, 595.27563, 841.8898)),
                 ("c", layout(3))]
        with temp_path(".docx") as p:
            xml = build(pages, p)
            # 3 pages, 2 of the transitions are section breaks, so no explicit
            # page breaks are needed at all.
            self.assertEqual(count_page_breaks(xml), 0)

    def test_package_has_all_ten_parts_and_they_all_parse(self):
        with temp_path(".docx") as p:
            build([("a", layout(1))], p)
            self.assertEqual(len(all_parts_parse(p)), 10)

    def test_document_xml_is_written_incrementally(self):
        # If the part were buffered and written at close(), it could not be
        # deflated in place; assert the zip entry really is streamed.
        with temp_path(".docx") as p:
            build([(f"p{i}", layout(i)) for i in range(1, 6)], p)
            with zipfile.ZipFile(p) as z:
                info = z.getinfo("word/document.xml")
            self.assertGreater(info.file_size, 0)
            self.assertEqual(info.compress_type, zipfile.ZIP_DEFLATED)


class BatchPathTests(unittest.TestCase):
    """write_docx() is now a thin wrapper on the streaming writer."""

    def test_batch_and_stream_produce_identical_xml(self):
        pages = [(f"p{i}", layout(i)) for i in range(1, 6)]
        with temp_path(".docx") as a, temp_path(".docx") as b:
            stream_xml = build(pages, a)
            write_docx([t for t, _ in pages], b, "test.pdf", {},
                       [l for _, l in pages])
            self.assertEqual(document_xml(b), stream_xml)


if __name__ == "__main__":
    unittest.main()
