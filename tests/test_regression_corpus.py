"""The large corpus files, when they are present in the checkout.

All 33,847 pages of c701to1k / c1-700 / c1k-end are 612x792, rotation 0 and
carry no images, so image detection and rotation handling must be provable
no-ops there: exactly one sectPr, exactly one page size, no log output.

These files are big (the smallest takes ~40 s) and are not required for the
suite to be meaningful, so each test skips itself when its file is missing.
"""

import io
import subprocess
import sys
import unittest
from contextlib import redirect_stdout

from module.main import run_pipeline
from tests.util import (
    REPO_ROOT, regression_pdf, document_xml, all_parts_parse, count_page_breaks,
    count_sect_pr, page_sizes, docx_page_count,
)
from tests.tmp import temp_path

LETTER = b'<w:pgSz w:w="12240" w:h="15840"/>'

# name -> (page count, extraction characters).
#
# c701to1k's character count was 3,806,875 before the Zawgyi converter was
# resynced with upstream Rabbit. Correctly stacked Pali clusters are one
# codepoint shorter than the ASAT-joined form the drifted rules produced
# (495 occurrences of ကမ္ဘာ alone in this file), which accounts for the
# 226-character drop. The per-run Zawgyi guard then added 147 back by NOT
# converting runs that were already Unicode, giving 3,806,796.
# Page count, layout and geometry are unaffected by either change.
CORPUS = {
    "c701to1k": (6870, 3806796),
    "c1-700": (14313, None),
    "c1k-end": (12664, None),
}


class RegressionCorpusTests(unittest.TestCase):

    def _run(self, name):
        path = regression_pdf(name)
        if path is None:
            self.skipTest(f"{name}.pdf not present")
        raw = path.read_bytes()
        buf = io.StringIO()
        with temp_path(".docx") as out:
            with redirect_stdout(buf):
                result = run_pipeline(raw, out, pdf_name=name, keep_pages=False)
            xml = document_xml(out)
            parts = len(all_parts_parse(out))
        return result, xml, parts, buf.getvalue()

    def _peak_mb(self, name):
        """Peak RSS of a pipeline run, measured in a clean child process.

        ru_maxrss is a high-water mark for the whole process, so measuring it
        in-process would report the cost of the ASSERTIONS (parsing a hundred
        megabytes of document.xml with ElementTree) rather than the cost of the
        writer. A fresh interpreter measures only the pipeline.
        """
        path = regression_pdf(name)
        if path is None:
            self.skipTest(f"{name}.pdf not present")
        # The child is forked from this process, and fork carries the
        # parent's ru_maxrss high-water mark across, so the child must reset
        # its own peak (/proc/self/clear_refs "5") before it starts and then
        # read VmHWM back out. Otherwise it just reports the test runner's
        # own footprint.
        code = (
            "import sys, os, io, contextlib\n"
            "sys.path.insert(0, %r)\n"
            "open('/proc/self/clear_refs', 'w').write('5')\n"
            "from module.main import run_pipeline\n"
            "raw = open(%r, 'rb').read()\n"
            "out = %r\n"
            "with contextlib.redirect_stdout(io.StringIO()):\n"
            "    run_pipeline(raw, out, pdf_name='x', keep_pages=False)\n"
            "os.unlink(out)\n"
            "hwm = [l for l in open('/proc/self/status') if l.startswith('VmHWM')]\n"
            "print(int(hwm[0].split()[1]))\n"
        )
        with temp_path(".docx") as out:
            src = code % (str(REPO_ROOT), str(path), out)
            proc = subprocess.run([sys.executable, "-c", src],
                                  capture_output=True, text=True,
                                  cwd=str(REPO_ROOT))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return int(proc.stdout.strip().splitlines()[-1]) / 1024

    def _check(self, name):
        pages, chars = CORPUS[name]
        result, xml, parts, log = self._run(name)

        self.assertEqual(result.page_count, pages)
        if chars is not None:
            self.assertEqual(result.total_characters, chars)

        # One Word page per PDF page.
        self.assertEqual(docx_page_count(xml), pages)
        self.assertEqual(count_page_breaks(xml), pages - 1)

        # Tasks 2 and 3 are no-ops on this corpus.
        self.assertEqual(count_sect_pr(xml), 1)
        self.assertEqual(set(page_sizes(xml)), {LETTER})
        self.assertNotIn("include image", log)

        self.assertEqual(parts, 10)

    def _check_memory(self, name):
        peak_mb = self._peak_mb(name)
        # The batch writer peaked at 927 MB on c701to1k and scaled with page
        # count; the streaming writer is flat.
        self.assertLess(peak_mb, 200, f"peak {peak_mb:.0f} MB")

    def test_c701to1k(self):
        self._check("c701to1k")

    def test_c701to1k_memory_is_flat(self):
        self._check_memory("c701to1k")

    def test_c1_700(self):
        self._check("c1-700")

    def test_c1k_end(self):
        self._check("c1k-end")


if __name__ == "__main__":
    unittest.main()
