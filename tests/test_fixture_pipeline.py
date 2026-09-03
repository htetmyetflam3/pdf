"""End-to-end run of the real fixture through the pipeline.

One 2,294-page run of input/pdf/test.pdf produces both a .docx and a .txt,
and every assertion below reads those two artefacts. The run takes about
thirteen seconds, so it happens once for the whole class.
"""

import io
import re
import resource
import unittest
from contextlib import redirect_stdout

from module.main import run_pipeline
from tests.util import (
    fixture_bytes, document_xml, all_parts_parse, count_page_breaks,
    count_sect_pr, page_sizes, docx_page_count,
)
from tests.tmp import temp_path

PAGES = 2294
IMAGE_PAGES = (635, 2294)
LETTER = b'<w:pgSz w:w="12240" w:h="15840"/>'
A4 = b'<w:pgSz w:w="11906" w:h="16838"/>'
A4_LANDSCAPE = b'<w:pgSz w:w="16838" w:h="11906" w:orient="landscape"/>'


class FixturePipelineTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        raw = fixture_bytes()
        cls._docx = temp_path(".docx")
        cls._txt = temp_path(".txt")
        cls.docx_path = cls._docx.__enter__()
        cls.txt_path = cls._txt.__enter__()

        before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        buf = io.StringIO()
        with redirect_stdout(buf):
            cls.result = run_pipeline(raw, cls.docx_path,
                                      pdf_name="input/pdf/test.pdf",
                                      keep_pages=False)
        cls.docx_log = buf.getvalue()
        cls.peak_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        cls.before_kb = before

        buf = io.StringIO()
        with redirect_stdout(buf):
            run_pipeline(raw, cls.txt_path, pdf_name="input/pdf/test.pdf",
                         keep_pages=False)
        cls.txt_log = buf.getvalue()
        cls.xml = document_xml(cls.docx_path)
        with open(cls.txt_path, encoding="utf-8") as fh:
            cls.txt = fh.read()

    @classmethod
    def tearDownClass(cls):
        cls._docx.__exit__(None, None, None)
        cls._txt.__exit__(None, None, None)

    # -- invariant 3 --------------------------------------------------------

    def test_docx_page_count_equals_pdf_page_count(self):
        self.assertEqual(self.result.page_count, PAGES)
        self.assertEqual(docx_page_count(self.xml), PAGES)

    def test_every_page_transition_is_exactly_one_break(self):
        breaks = count_page_breaks(self.xml)
        sections = count_sect_pr(self.xml) - 1  # the last one ends the body
        self.assertEqual(breaks + sections, PAGES - 1)

    # -- task 2: images -----------------------------------------------------

    def test_image_pages_are_logged_once_each_and_nothing_else_is(self):
        lines = [l for l in self.docx_log.splitlines() if "include image" in l]
        self.assertEqual(lines, [
            f"page {n} include image skipping the page "
            f"and leave with empty blank page" for n in IMAGE_PAGES])

    def test_nothing_is_logged_for_pages_without_images(self):
        # Only the two image lines plus the writer's own "Saved" line.
        noise = [l for l in self.docx_log.splitlines()
                 if l.strip() and "include image" not in l
                 and not l.startswith("[+]")]
        self.assertEqual(noise, [])

    def test_image_pages_are_blank_in_the_text_output(self):
        pages = self.txt.split("-------------------Page ")
        for n in IMAGE_PAGES:
            body = pages[n].split("-----------------\n", 1)[1]
            self.assertEqual(body.strip(), "", f"page {n}")

    def test_the_file_is_never_rejected_for_containing_an_image(self):
        self.assertEqual(self.result.page_count, PAGES)
        self.assertGreater(self.result.total_characters, 1_000_000)

    # -- task 3: rotation and page size ------------------------------------

    def test_the_five_odd_pages_produce_their_own_sections(self):
        # 300 (A4/270), 320 (Letter/180), 321 (A4), 635 (A4/180), 2294 (A4).
        # Each is bracketed by plain Letter pages, so each needs a break in
        # and a break out; 2294 is last so it only needs the break in.
        self.assertEqual(count_sect_pr(self.xml), 9)

    def test_the_270_page_is_landscape_a4(self):
        self.assertIn(A4_LANDSCAPE, page_sizes(self.xml))

    def test_the_180_pages_keep_their_page_size(self):
        # Page 320 is a 180-degree Letter page: still 12240x15840.
        sizes = page_sizes(self.xml)
        self.assertIn(LETTER, sizes)
        self.assertEqual(sizes.count(A4_LANDSCAPE), 1)

    def test_the_a4_page_triggers_a_section_on_size_alone(self):
        self.assertIn(A4, page_sizes(self.xml))

    def test_only_the_expected_page_shapes_appear(self):
        self.assertEqual(set(page_sizes(self.xml)), {LETTER, A4, A4_LANDSCAPE})

    # -- task 1: streaming --------------------------------------------------

    def test_peak_memory_is_far_below_the_batch_baseline(self):
        # The python-docx batch writer peaked at 301 MB on this file.
        self.assertLess(self.peak_kb / 1024, 150,
                        f"peak {self.peak_kb / 1024:.0f} MB")

    def test_all_ten_ooxml_parts_are_present_and_well_formed(self):
        self.assertEqual(len(all_parts_parse(self.docx_path)), 10)

    # -- text output --------------------------------------------------------

    def test_txt_has_one_separator_per_page(self):
        seps = re.findall(r"^-------------------Page (\d+) -----------------$",
                          self.txt, re.M)
        self.assertEqual(len(seps), PAGES)
        self.assertEqual([int(s) for s in seps], list(range(1, PAGES + 1)))

    def test_txt_carries_no_geometry(self):
        # TXT has no fonts and no page boxes; it must not leak twips or sizes.
        self.assertNotIn("w:pgSz", self.txt)
        self.assertNotIn("w:ind", self.txt)

    def test_character_count_is_unchanged_from_the_baseline(self):
        # 1,217,617 before the converter was resynced with upstream Rabbit.
        # Correct Pali stacking is one codepoint shorter than the broken form
        # in some cases, so the count moved by 67; the page count and the
        # layout are unaffected.
        self.assertEqual(self.result.total_characters, 1_217_550)


if __name__ == "__main__":
    unittest.main()
