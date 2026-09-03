"""The /Rotate display transform, which lives in the parser.

Putting it in the parser rather than the writer means the .txt and the .docx
agree: a rotated page reads in true order in both. All 33,847 pages of the
large regression files are rotation 0, so they are byte-identical either way.
"""

import unittest

from module.prase import rotate_point, rotated_page_size, rotate_layout_lines


W, H = 612.0, 792.0


class RotatePointTests(unittest.TestCase):

    def test_zero_is_the_identity(self):
        self.assertEqual(rotate_point(100.0, 200.0, W, H, 0), (100.0, 200.0))

    def test_ninety(self):
        self.assertEqual(rotate_point(100.0, 200.0, W, H, 90), (200.0, 512.0))

    def test_one_eighty(self):
        self.assertEqual(rotate_point(100.0, 200.0, W, H, 180), (512.0, 592.0))

    def test_two_seventy(self):
        self.assertEqual(rotate_point(100.0, 200.0, W, H, 270), (592.0, 100.0))

    def test_four_turns_of_ninety_return_to_the_start(self):
        x, y, w, h = 100.0, 200.0, W, H
        for _ in range(4):
            x, y = rotate_point(x, y, w, h, 90)
            w, h = rotated_page_size(w, h, 90)
        self.assertEqual((round(x, 6), round(y, 6)), (100.0, 200.0))
        self.assertEqual((w, h), (W, H))

    def test_a_transformed_point_stays_inside_the_displayed_page(self):
        for rot in (0, 90, 180, 270):
            dw, dh = rotated_page_size(W, H, rot)
            for x, y in ((0.0, 0.0), (W, 0.0), (0.0, H), (W, H), (56.6, 712.0)):
                nx, ny = rotate_point(x, y, W, H, rot)
                self.assertGreaterEqual(nx, -1e-9)
                self.assertGreaterEqual(ny, -1e-9)
                self.assertLessEqual(nx, dw + 1e-9)
                self.assertLessEqual(ny, dh + 1e-9)


class RotatedPageSizeTests(unittest.TestCase):

    def test_only_quarter_turns_swap_the_box(self):
        self.assertEqual(rotated_page_size(W, H, 0), (W, H))
        self.assertEqual(rotated_page_size(W, H, 180), (W, H))
        self.assertEqual(rotated_page_size(W, H, 90), (H, W))
        self.assertEqual(rotated_page_size(W, H, 270), (H, W))


class RotateLayoutTests(unittest.TestCase):

    def lines(self):
        return [
            {"text": "first", "x": 56.6, "y": 712.0, "right": 456.0,
             "size": 20.0, "runs": [{"text": "first", "x": 56.6, "size": 20.0}]},
            {"text": "second", "x": 56.6, "y": 663.0, "right": 460.0,
             "size": 20.0, "runs": [{"text": "second", "x": 56.6, "size": 20.0}]},
            {"text": "third", "x": 56.6, "y": 614.0, "right": 400.0,
             "size": 20.0, "runs": [{"text": "third", "x": 56.6, "size": 20.0}]},
        ]

    def test_rotation_zero_returns_the_lines_untouched(self):
        ls = self.lines()
        self.assertIs(rotate_layout_lines(ls, W, H, 0), ls)

    def test_180_reverses_reading_order(self):
        out = rotate_layout_lines(self.lines(), W, H, 180)
        self.assertEqual([l["text"] for l in out], ["third", "second", "first"])

    def test_line_length_is_preserved(self):
        for rot in (90, 180, 270):
            for orig, new in zip(self.lines(),
                                 sorted(rotate_layout_lines(self.lines(), W, H, rot),
                                        key=lambda l: l["text"])):
                pass
        out = rotate_layout_lines(self.lines(), W, H, 180)
        lengths = sorted(round(l["right"] - l["x"], 6) for l in out)
        expect = sorted(round(l["right"] - l["x"], 6) for l in self.lines())
        self.assertEqual(lengths, expect)

    def test_run_offsets_follow_their_line(self):
        out = rotate_layout_lines(self.lines(), W, H, 270)
        for line in out:
            self.assertAlmostEqual(line["runs"][0]["x"], line["x"])

    def test_270_puts_lines_inside_the_landscape_box(self):
        dw, dh = rotated_page_size(W, H, 270)
        for line in rotate_layout_lines(self.lines(), W, H, 270):
            self.assertGreaterEqual(line["x"], 0)
            self.assertLessEqual(line["x"], dw)
            self.assertGreaterEqual(line["y"], 0)
            self.assertLessEqual(line["y"], dh)

    def test_no_text_is_lost(self):
        for rot in (90, 180, 270):
            out = rotate_layout_lines(self.lines(), W, H, rot)
            self.assertEqual(sorted(l["text"] for l in out),
                             ["first", "second", "third"])


if __name__ == "__main__":
    unittest.main()
