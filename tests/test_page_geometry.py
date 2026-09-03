"""Page geometry read straight out of the fixture PDF.

These tests pin the five pages of input/pdf/test.pdf that are not plain
612x792 portrait. They are the pages every other test in this suite depends
on, so if the fixture is ever replaced these fail first and explain why.
"""

import unittest

from tests.util import FIXTURE, fixture_bytes
from tests.shape_scan import scan_page_shapes, odd_pages


class PageGeometryTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.shapes = scan_page_shapes(fixture_bytes())

    def test_page_count(self):
        self.assertEqual(len(self.shapes), 2294, FIXTURE)

    def test_exactly_five_pages_are_not_plain_letter_portrait(self):
        odd = odd_pages(self.shapes)
        self.assertEqual([s["page"] for s in odd], [300, 320, 321, 635, 2294])

    def test_rotations(self):
        by_page = {s["page"]: s for s in self.shapes}
        self.assertEqual(by_page[300]["rotation"], 270)
        self.assertEqual(by_page[320]["rotation"], 180)
        self.assertEqual(by_page[635]["rotation"], 180)
        self.assertEqual(by_page[321]["rotation"], 0)
        self.assertEqual(by_page[2294]["rotation"], 0)

    def test_a4_pages(self):
        by_page = {s["page"]: s for s in self.shapes}
        for p in (300, 321, 635, 2294):
            self.assertAlmostEqual(by_page[p]["width"], 595.27563, places=3)
            self.assertAlmostEqual(by_page[p]["height"], 841.8898, places=3)
        # Page 320 differs by rotation only; its paper is still Letter.
        self.assertAlmostEqual(by_page[320]["width"], 612.0)
        self.assertAlmostEqual(by_page[320]["height"], 792.0)

    def test_only_two_pages_carry_an_image(self):
        imgs = [s["page"] for s in self.shapes if s["has_image"]]
        self.assertEqual(imgs, [635, 2294])

    def test_every_other_page_is_letter_portrait_unrotated(self):
        odd = {s["page"] for s in odd_pages(self.shapes)}
        rest = [s for s in self.shapes if s["page"] not in odd]
        self.assertEqual(len(rest), 2289)
        for s in rest:
            self.assertEqual((s["width"], s["height"], s["rotation"],
                              s["has_image"]),
                             (612.0, 792.0, 0, False), f"page {s['page']}")


if __name__ == "__main__":
    unittest.main()
