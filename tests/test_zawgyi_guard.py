"""The per-run Zawgyi guard.

Encoding is detected per PAGE, but pages mix encodings, and Rabbit is not
idempotent: handing it text that is already Unicode destroys that text
irreversibly. The guard is a structural veto layered on the page verdict —
see the block comment in module/postprocessor.py for the rules and the
measurements behind them.
"""

import re
import unittest

from module.postprocessor import (
    looks_like_zawgyi, has_zawgyi_evidence, has_unicode_evidence,
)
from module.unicoding import Rabbit


class ZawgyiEvidenceTests(unittest.TestCase):
    """Configurations that are illegal in Unicode, so they prove Zawgyi."""

    def test_zawgyi_only_codepoints(self):
        self.assertTrue(has_zawgyi_evidence("ကမၻာ"))     # U+107B
        self.assertTrue(has_zawgyi_evidence("ခုႏွစ္"))     # U+1004+
        self.assertTrue(has_zawgyi_evidence("တိုက္ခိုက္မႈ"))

    def test_virama_with_nothing_to_stack_onto(self):
        # U+1039 must be followed by a consonant; Zawgyi uses it as a visible
        # asat, so it lands before spaces, punctuation and end-of-string.
        self.assertTrue(has_zawgyi_evidence("ဒိန္း"))
        self.assertTrue(has_zawgyi_evidence("ရွီယန္နားလည္ထားသည္။"))

    def test_e_vowel_with_no_base_behind_it(self):
        # The rule that matters: Unicode always writes base-then-vowel.
        self.assertTrue(has_zawgyi_evidence("မရွိေပ။"))
        self.assertTrue(has_zawgyi_evidence("ေျမေအာက္ကမၻာ"))

    def test_the_backward_test_does_not_fire_on_valid_unicode(self):
        # Asking "is U+1031 FOLLOWED by a consonant" false-positives here,
        # because မြေ|အောက် is two syllables. Asking what precedes it does not.
        self.assertFalse(has_zawgyi_evidence("မြေအောက်ကမ္ဘာ"))
        self.assertFalse(has_zawgyi_evidence("ရှေ့ပြေးအဖြစ် အသုံးချနေခြင်းဖြစ်၏။"))

    def test_medials_may_sit_between_a_base_and_its_vowel(self):
        self.assertFalse(has_zawgyi_evidence("ကြေ"))
        self.assertFalse(has_zawgyi_evidence("မြေ"))


class UnicodeEvidenceTests(unittest.TestCase):
    """Signals Zawgyi could not have produced."""

    def test_ha_hto_is_not_in_the_zawgyi_font(self):
        # U+103E has no glyph in the embedded Zawgyi-One; the Zawgyi ha hto
        # is U+103D, the medial block being off by one.
        self.assertTrue(has_unicode_evidence("ရှီယန်"))
        self.assertTrue(has_unicode_evidence("မရှိပေ။"))

    def test_asat_without_a_malformed_stack(self):
        self.assertTrue(has_unicode_evidence("၀င်ရောက်သွားချိန်"))
        self.assertTrue(has_unicode_evidence("ဒိန်း"))

    def test_homorganic_stacks_are_unicode(self):
        # Both consonants from one articulation group, or a geminate.
        for s in ("ကမ္ဘာ", "ခန္ဓာ", "သတ္တဝါ", "တစ်စက္ကန့်၏"):
            self.assertTrue(has_unicode_evidence(s), s)

    def test_a_non_homorganic_stack_is_zawgyis_asat(self):
        # က္လ is velar+lateral: no Pali stack looks like this, so the U+1039
        # is Zawgyi spelling မျက် rather than stacking anything.
        self.assertFalse(has_unicode_evidence("မ်က္လုံး"))

    def test_zawgyi_text_carries_no_unicode_evidence(self):
        for zg in ("ရွီယန္က", "နတ္ဘုရား", "မရွိေပ။"):
            self.assertFalse(has_unicode_evidence(zg), zg)

    def test_zawgyi_evidence_outranks_a_stray_unicode_signal(self):
        # ေျမေအာက္ကမၻာ trips the asat branch, but it also carries hard Zawgyi
        # evidence (leading U+1031, U+107B), so the guard must still convert.
        mixed = "ေျမေအာက္ကမၻာ"
        self.assertTrue(has_unicode_evidence(mixed))
        self.assertTrue(has_zawgyi_evidence(mixed))
        self.assertTrue(looks_like_zawgyi(mixed))


class GuardDecisionTests(unittest.TestCase):

    ZAWGYI = [
        "ရွီယန္နားလည္ထားသည္။", "ေျမေအာက္ကမၻာ", "မရွိေပ။", "ဒိန္း",
        "သတိမလြတ္ရဲပါ။", "ရွီယန္က", "နတ္ဘုရား", "ထပ္ခါ", "မ်က္လုံး",
        "အျခား", "ဘယ္သူမွ", "လွပေသာ", "တိုက္ခိုက္မႈ",
    ]
    UNICODE = [
        "၀င်ရောက်သွားချိန်", "ဝင်ရောက်သွားချိန်", "၀ိညာဉ်ခေါ်ပုလဲ",
        "မြေအောက်ကမ္ဘာ", "ဒိန်း", "မရှိပေ။", "ရှီယန်နားလည်ထားသည်။",
        "၀ိညာဉ်စွမ်းအင်ဖြာထွက်မ", "ကမ္ဘာ", "ခန္ဓာ", "သတ္တဝါ",
        "တစ်စက္ကန့်၏", "ကျောင်းသား",
    ]

    def test_zawgyi_is_converted(self):
        for s in self.ZAWGYI:
            self.assertTrue(looks_like_zawgyi(s), f"would skip Zawgyi: {s}")

    def test_already_unicode_is_left_alone(self):
        for s in self.UNICODE:
            self.assertFalse(looks_like_zawgyi(s), f"would destroy: {s}")

    def test_empty_and_non_myanmar_text(self):
        for s in ("", "   ", "Chapter 701", "----- -----", "123"):
            self.assertFalse(has_zawgyi_evidence(s))

    def test_ambiguous_runs_default_to_converting(self):
        # The page verdict already said Zawgyi. Leaving a Zawgyi run alone is
        # visible and harmless; converting Unicode twice is irreversible. So
        # the tie must break towards converting.
        self.assertTrue(looks_like_zawgyi("ကက"))

    def test_the_veto_protects_text_conversion_would_destroy(self):
        for s in self.UNICODE:
            if Rabbit.zg2uni(s) != s:
                self.assertFalse(looks_like_zawgyi(s),
                                 f"conversion would mangle {s}")


class GuardCorpusTests(unittest.TestCase):
    """Rates measured against the real fixture, not synthetic strings."""

    MYA = re.compile("[\u1000-\u109f]")

    @classmethod
    def setUpClass(cls):
        from module import prase as P
        from tests.util import fixture_bytes

        doc = P.open_document(fixture_bytes())
        refs = list(P.iter_page_refs(doc.objects, doc.pages_obj))
        cls.runs = []
        # 300 pages is enough for a stable rate and keeps the test quick.
        for i, ref in enumerate(refs[:300]):
            page = P.extract_one_page(doc, ref, i)
            for line in page["layout"]["lines"]:
                for run in line.get("runs") or []:
                    s = (run.get("text") or "").strip()
                    if s and cls.MYA.search(s):
                        cls.runs.append(s)

    def test_the_corpus_actually_loaded(self):
        self.assertGreater(len(self.runs), 1000)

    def test_converted_text_with_a_unicode_marker_is_protected(self):
        # The invariant that matters. A run that carries positive Unicode
        # evidence must survive a second pass untouched, because that is the
        # irreversible direction.
        #
        # Runs with NO marker at all are deliberately NOT covered: they are
        # ambiguous, and on a page the model called Zawgyi the guard converts
        # by design. Asserting otherwise would encode the wrong default.
        checked = 0
        for raw in self.runs:
            # Only runs the pipeline would really convert. Feeding Rabbit a
            # run that was already Unicode produces mangled text, and the
            # guard is right to flag that -- it just is not a case that can
            # occur downstream of the guard.
            if not looks_like_zawgyi(raw):
                continue
            conv = Rabbit.zg2uni(raw)
            if has_zawgyi_evidence(conv):
                # Mixed-encoding run: both halves live in ONE run, e.g.
                # "အသိစိတ္မ်ားပင္ ေ၀၀ါးသွားခဲ့သည်။", whose tail is already
                # Unicode. Converting mangles that tail, so Zawgyi evidence
                # survives the pass. A per-run guard cannot split it -- see
                # test_mixed_runs_are_a_known_limit.
                continue
            if not has_unicode_evidence(conv):
                continue
            checked += 1
            self.assertFalse(looks_like_zawgyi(conv),
                             f"{conv!r} carries Unicode evidence yet would "
                             f"be converted again")
        self.assertGreater(checked, 500, "sample too small to be meaningful")

    def test_most_runs_survive_a_second_pass(self):
        # Whole-corpus safety net: the guard must leave the large majority of
        # converted output alone, or it is not doing its job at all.
        converted = [Rabbit.zg2uni(s) for s in self.runs
                     if looks_like_zawgyi(s)]
        stable = sum(1 for s in converted if not looks_like_zawgyi(s))
        rate = stable / len(converted)
        # 86% measured. The residual is short markerless runs ("ကက", a
        # two-syllable fragment) which carry no positive Unicode signal and
        # so re-trip the convert-by-default branch. Harmless in the pipeline,
        # which converts each run exactly once.
        self.assertGreater(rate, 0.85,
                           f"only {rate:.1%} of converted runs are left alone")

    def test_mixed_runs_are_a_known_limit(self):
        """Runs carrying BOTH encodings at once cannot be handled per-run.

        Detection moved from per-page to per-run, which fixes lines that mix
        encodings. A single RUN that mixes them would need per-syllable
        detection and is out of scope; this pins how often it happens so a
        regression is visible.
        """
        # Detected by residue: a clean Zawgyi run converts to text with no
        # Zawgyi evidence left. If evidence survives, the run held both.
        mixed = [s for s in self.runs
                 if looks_like_zawgyi(s)
                 and has_zawgyi_evidence(Rabbit.zg2uni(s))]
        rate = len(mixed) / len(self.runs)
        self.assertLess(rate, 0.05,
                        f"mixed-encoding runs jumped to {rate:.2%}")


if __name__ == "__main__":
    unittest.main()
