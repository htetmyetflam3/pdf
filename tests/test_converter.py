"""The Zawgyi -> Unicode converter, checked against upstream Rabbit.

module/unicoding.py is a verbatim port of Rabbit-Converter/Rabbit's
source/rule/zg2uni.json. It had previously drifted to a hand-maintained
subset, and the drift produced output that looked right but carried the wrong
codepoints — the kind of bug that survives proofreading. These tests pin the
cases that broke, so a future edit cannot quietly reintroduce them.
"""

import unittest

from module.unicoding import Rabbit, _RULES
from module.postprocessor import clean_imposters, reorder_marks

ASAT = "\u103a"    # visible killer stroke
VIRAMA = "\u1039"  # invisible stacker


class RuleTableTests(unittest.TestCase):

    def test_rule_count_matches_upstream(self):
        self.assertEqual(len(_RULES), 118)

    def test_no_javascript_regex_literals_leaked_in(self):
        # '/(foo)/g' pasted from JS compiles as a literal-slash pattern and
        # silently never matches. One such rule was dead for exactly this
        # reason.
        for pattern, _ in _RULES:
            self.assertFalse(pattern.startswith("/") and pattern.endswith("/g"),
                             f"JS regex literal: {pattern!r}")

    def test_no_zawgyi_codepoint_survives_into_the_output(self):
        # Intermediate rules may legitimately shuffle Zawgyi codepoints
        # around (rule 3 folds 'ွြ' into 'ႊ' before expanding it; 'ျၤ'->'ၤျ'
        # just reorders), so the invariant is about the FINAL output, not
        # about any individual rule's replacement string.
        # Each input is a well-formed syllable: a bare combining mark with no
        # base consonant has nothing to attach to and is not valid input.
        for zg in ("ကမၻာ", "႑", "ၳ", "ၴ", "ေျမေအာက္ကမၻာ",
                   "ကႊ", "ကၤ", "ကႋ", "ကႌ", "ကႍ",
                   "ကၦ", "ကၧ", "႒", "႗", "၎"):
            out = Rabbit.zg2uni(zg)
            for ch in out:
                self.assertFalse("\u1060" <= ch <= "\u109f",
                                 f"{zg!r} -> {out!r} still holds Zawgyi {ch!r}")


class PaliClusterTests(unittest.TestCase):
    """Stacked Pali consonants must join with VIRAMA, never ASAT."""

    def test_kambha_uses_virama_not_asat(self):
        got = Rabbit.zg2uni("ကမၻာ")
        self.assertEqual(got, "ကမ္ဘာ")
        self.assertIn(VIRAMA, got)
        self.assertNotIn(ASAT, got)

    def test_the_page_8_line_that_was_reported_broken(self):
        got = Rabbit.zg2uni("ေျမေအာက္ကမၻာ")
        self.assertEqual(got, "မြေအောက်ကမ္ဘာ")

    def test_nna_dda_is_fully_converted(self):
        # Used to map to 'ဏ္႑', leaving the raw Zawgyi codepoint behind.
        got = Rabbit.zg2uni("႑")
        self.assertEqual(got, "ဏ္ဍ")

    def test_tha_ha_to_stackers_convert(self):
        # These were unreachable behind the dead JS-literal rule.
        for zg in ("ၳ", "ၴ"):
            self.assertEqual(Rabbit.zg2uni(zg), "္ထ", f"{zg!r} did not convert")

    def test_reordering_preserves_pali_clusters(self):
        # The reorderer is downstream of the converter; confirm it does not
        # disturb a correctly stacked cluster.
        uni = Rabbit.zg2uni("ေျမေအာက္ကမၻာ")
        self.assertEqual(reorder_marks(clean_imposters(uni)), uni)


class KnownGoodConversions(unittest.TestCase):

    CASES = [
        ("ကမၻာ", "ကမ္ဘာ"),
        ("ေျမေအာက္ကမၻာ", "မြေအောက်ကမ္ဘာ"),
        ("ဒိန္း", "ဒိန်း"),
        ("တစ္၀က္", "တစ်ဝက်"),
        ("ခ်ိန္ ၀င္", "ချိန် ဝင်"),
        ("၀ါ", "ဝါ"),
    ]

    def test_conversions(self):
        for zg, want in self.CASES:
            with self.subTest(zg=zg):
                self.assertEqual(Rabbit.zg2uni(zg), want)


class DoubleConversionTests(unittest.TestCase):
    """Rabbit is NOT idempotent — that is a property of the pipeline, not it.

    zg2uni is a chain of blind rewrites, so feeding it Unicode mangles it
    (upstream Rabbit behaves identically; this is not a port defect). The
    pipeline must therefore never hand it text that is already Unicode.

    That matters because encoding is detected per PAGE while a page can MIX
    encodings: page 6 of the fixture is Zawgyi except for one line that is
    already Unicode, and 48 of the first 400 pages contain at least one line
    whose encoding differs from the page verdict.
    """

    def test_double_conversion_is_destructive(self):
        uni = "ဝင်ရောက်သွားချိန်"
        self.assertNotEqual(Rabbit.zg2uni(uni), uni)

    def test_asat_is_what_gets_destroyed(self):
        # The visible symptom: ASAT turns into a stray medial ya.
        self.assertIn(ASAT, "ဝင်ရောက်")
        self.assertNotIn(ASAT, Rabbit.zg2uni("ဝင်ရောက်"))

    @unittest.expectedFailure
    def test_KNOWN_BUG_mixed_encoding_pages_double_convert(self):
        """Page 6 of the fixture is Zawgyi with one already-Unicode line.

        detect/convert is per PAGE (main.process_page), so that line is
        converted a second time and comes out mangled:

            ၀င်ရောက်သွားချိန်   ->   ၀ငျရောကျသှားခြိနျ

        Fixing it means detecting per line (or per run) instead of per page,
        which changes conversion behaviour across the whole corpus and is a
        deliberate decision, not a drive-by. Marked expectedFailure so the
        suite records the bug and tells us the day it starts passing.
        """
        from module import prase as P
        from tests.util import fixture_bytes

        doc = P.open_document(fixture_bytes())
        refs = list(P.iter_page_refs(doc.objects, doc.pages_obj))
        page = P.extract_one_page(doc, refs[5], 5)
        line = [l for l in page["text"].split("\n") if l.strip()][10]
        self.assertEqual(Rabbit.zg2uni(line).strip(), line.strip())


if __name__ == "__main__":
    unittest.main()
