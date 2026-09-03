# Description: Post-processing filter for Myanmar Unicode text after Zawgyi→Unicode conversion.
# Reads `all_texts` (list of page strings), applies imposter cleanup + mark reordering,
# then writes to TXT/DOCX/PDF. Place this block after the Zawgyi detection/conversion
# and before the output-writing section of your PDF-to-text script.
import re


# ═══════════════════════════════════════════════════════════════════════════════
# 1.  Myanmar Unicode mark-priority map  (ported from JS Priority)
# ═══════════════════════════════════════════════════════════════════════════════
_PRIORITY = {
    '\u103B': 1,   # ya pin
    '\u103C': 2,   # ya yit
    '\u103D': 3,   # wa hswe
    '\u103E': 4,   # ha hto
    '\u1031': 5,   # e vowel
    '\u102D': 6,   # i vowel
    '\u102E': 7,   # ii vowel
    '\u102F': 8,   # u vowel
    '\u1030': 9,   # uu vowel
    '\u1032': 10,  # ai vowel
    '\u102C': 11,  # aa vowel
    '\u102B': 12,  # tall aa vowel
    '\u1036': 13,  # anusvara
    '\u103A': 14,  # asat
    '\u1037': 15,  # dot below
    '\u1038': 16,  # visarga
    '\u1039': 17,  # stacker — FINAL slot. It belongs to the previous base's
                    # mark-run and closes it: the base after the stacker
                    # starts ordering anew (index 0 / base position).
                    # Canonical kinzi '\u1004\u103A\u1039' (asat 14 < 17) is
                    # preserved; a stray '\u1039\u103A' normalizes to
                    # '\u103A\u1039'.
}

# Myanmar consonant range + independent vowels
_MYA_CONSONANTS = set(
    '\u1000\u1001\u1002\u1003\u1004\u1005\u1006\u1007\u1008\u1009'
    '\u100A\u100B\u100C\u100D\u100E\u100F\u1010\u1011\u1012\u1013'
    '\u1014\u1015\u1016\u1017\u1018\u1019\u101A\u101B\u101C\u101D'
    '\u101E\u101F\u1020\u1021\u1023\u1024\u1025\u1026\u1027\u1028'
    '\u1029\u102A\u1040\u1041\u1042\u1043\u1044\u1045\u1046\u1047'
    '\u1048\u1049\u104C\u104D\u104E\u104F'
)


def _is_myanmar_consonant(ch: str) -> bool:
    """True if ch is a Myanmar consonant or independent vowel (base character)."""
    return ch in _MYA_CONSONANTS


# ═══════════════════════════════════════════════════════════════════════════════
# 2.  Imposter cleanup  (ported from JS cleanImposters)
# ═══════════════════════════════════════════════════════════════════════════════
#    Map of Zawgyi imposters → correct Unicode codepoints.
#    Sorted longest-first so multi-char imposters match before single-char.
# ═══════════════════════════════════════════════════════════════════════════════
_IMPOSTER_REPS = sorted([
    # ---- ya pin family ----
    ('\u107E', '\u103B'),   # ya pin tall
    ('\u107F', '\u103B'),   # ya pin
    ('\u1080', '\u103B'),   # ya pin + kinzi-ish
    ('\u1081', '\u103B'),   # ya pin
    ('\u1082', '\u103B'),   # ya pin
    ('\u1083', '\u103B'),   # ya pin
    ('\u1084', '\u103B'),   # ya pin

    # ---- ya yit family ----
    ('\u1087', '\u103C'),   # ya yit (often fused with ha hto)

    # ---- wa hswe / ha hto ----
    ('\u1088', '\u103E\u102F'),  # ha hto + u
    ('\u1089', '\u103E\u1030'),  # ha hto + uu
    ('\u108A', '\u103D\u103E'),  # wa + ha

    # ---- kinzi / stacked forms ----
    ('\u1064', '\u1004\u103A\u1039'),  # kinzi
    ('\u108B', '\u1004\u103A\u1039\u102D'),
    ('\u108C', '\u1004\u103A\u1039\u102E'),
    ('\u108D', '\u1004\u103A\u1039\u1036'),
    ('\u108E', '\u102D\u1036'),

    # ---- consonant stackers ----
    ('\u1060', '\u1039\u1000'), ('\u1061', '\u1039\u1001'),
    ('\u1062', '\u1039\u1002'), ('\u1063', '\u1039\u1003'),
    ('\u1065', '\u1039\u1005'),
    ('\u1066', '\u1039\u1006'), ('\u1067', '\u1039\u1006'),
    ('\u1068', '\u1039\u1007'), ('\u1069', '\u1039\u1008'),
    ('\u106A', '\u1009'),       ('\u106B', '\u100A'),
    ('\u106C', '\u1039\u100B'), ('\u106D', '\u1039\u100C'),
    ('\u106E', '\u100D\u1039\u100D'), ('\u106F', '\u100D\u1039\u100E'),
    ('\u1070', '\u1039\u100F'),
    ('\u1071', '\u1039\u1010'), ('\u1072', '\u1039\u1010'),
    ('\u1073', '\u1039\u1011'), ('\u1074', '\u1039\u1011'),
    ('\u1075', '\u1039\u1012'), ('\u1076', '\u1039\u1013'),
    ('\u1077', '\u1039\u1014'), ('\u1078', '\u1039\u1015'),
    ('\u1079', '\u1039\u1016'), ('\u107A', '\u1039\u1017'),
    ('\u107B', '\u1039\u1018'), ('\u107C', '\u1039\u1019'),
    ('\u1085', '\u1039\u101C'),

    # ---- misc ----
    ('\u1086', '\u103F'),       # great sa
    ('\u108F', '\u1014'),       # na
    ('\u1090', '\u101B'),       # ra
    ('\u1091', '\u100F\u1039\u100D'),
    ('\u1092', '\u100B\u1039\u100C'),
    ('\u1093', '\u1039\u1018'),
    ('\u1094', '\u1037'),      ('\u1095', '\u1037'),
    ('\u1096', '\u1039\u1010\u103D'),
    ('\u1097', '\u100B\u1039\u100B'),

    # ---- tall aa / asat variants ----
    ('\u105A', '\u102B\u103A'),

    # ---- u / uu vowel variants ----
    ('\u1033', '\u102F'),       ('\u1034', '\u1030'),

    # ---- remaining asat-like mappings ----
    ('\u107D', '\u103B'),       # ya pin (asat context)
], key=lambda pair: len(pair[0]), reverse=True)

# Single-pass compiled alternation (longest-first, as _IMPOSTER_REPS is sorted).
_IMPOSTER_MAP = dict(_IMPOSTER_REPS)
_IMPOSTER_RE = re.compile("|".join(re.escape(frm) for frm, _ in _IMPOSTER_REPS))


def clean_imposters(t: str) -> str:
    """Replace any lingering Zawgyi imposter codepoints with correct Unicode.

    One compiled-regex pass (the old version ran ~50 str.replace() scans over
    the whole text, three times per page: page text + every line + every run).
    """
    if not t:
        return t
    return _IMPOSTER_RE.sub(lambda m: _IMPOSTER_MAP[m.group(0)], t)


# ═══════════════════════════════════════════════════════════════════════════════
# 3.  Mark reordering  (ported from JS reorderString)
# ═══════════════════════════════════════════════════════════════════════════════
#    After each Myanmar base character, dependent marks are collected,
#    deduplicated by priority slot, sorted into canonical order, then emitted.
# ═══════════════════════════════════════════════════════════════════════════════
def reorder_marks(t: str) -> str:
    """Reorder Myanmar dependent marks into canonical Unicode order."""
    if not t or not isinstance(t, str):
        return ""

    chars = list(t)
    out = []
    i = 0
    n = len(chars)

    # --- leading non-consonant prefix (e.g. punctuation, digits, spaces) ---
    while i < n and not _is_myanmar_consonant(chars[i]):
        out.append(chars[i])
        i += 1

    # --- main body ---
    while i < n:
        ch = chars[i]
        if _is_myanmar_consonant(ch):
            out.append(ch)
            i += 1

            # Collect contiguous dependent marks
            marks = []
            seen_priorities = set()
            while i < n:
                nxt = chars[i]
                if _is_myanmar_consonant(nxt):
                    break
                p = _PRIORITY.get(nxt)
                if p is None:
                    break
                if p not in seen_priorities:
                    seen_priorities.add(p)
                    marks.append(nxt)
                i += 1

            if marks:
                marks.sort(key=lambda c: _PRIORITY[c])
                out.extend(marks)
        else:
            out.append(ch)
            i += 1

    return "".join(out)


# ═══════════════════════════════════════════════════════════════════════════════
# 3b. Zawgyi content guard  (structural, not statistical)
# ═══════════════════════════════════════════════════════════════════════════════
#
# WHY THIS EXISTS
# ---------------
# Encoding is detected per PAGE by detector.Detector (Google's myanmar-tools
# Markov model). That model is accurate on whole pages -- it agreed with this
# guard on 2,289/2,289 text pages of the fixture -- but a page can MIX
# encodings, and a statistical model needs a body of text to be confident.
# Page 6 of input/pdf/test.pdf is Zawgyi except for one already-Unicode line;
# the page verdict is ZAWGYI, so that line was converted a SECOND time:
#
#     ၀င်ရောက်သွားချိန်   ->   ၀ငျရောကျသှားခြိနျ
#
# Rabbit is not idempotent (upstream is not either -- it is a chain of blind
# rewrites), and the damage is NOT repairable afterwards: zg2uni is
# many-to-one, so eight distinct Zawgyi inputs (ကျ ကၾ ကၿ ကႀ ကႁ ကႂ ကႃ ကႄ) all
# collapse to the single output ကြ. Reverse-converting mangled text with
# uni2zg recovered only 10 of 14 sample lines AND corrupted 6 of 6 healthy
# ones. Prevention is lossless; repair is not. Hence a guard.
#
# WHAT IT IS
# ----------
# Not a second model. Three STRUCTURAL facts -- configurations that are
# illegal in Unicode, so their presence is proof rather than probability:
#
#   1. U+1060..U+1097 (plus a few strays) exist only in Zawgyi.
#   2. U+1039 VIRAMA must be followed by a consonant (it stacks one onto the
#      previous). Zawgyi uses the same codepoint as a visible asat, so it
#      appears before spaces, punctuation and end-of-string.
#   3. U+1031 E-VOWEL must be PRECEDED by a base consonant. Unicode stores it
#      after its base and the renderer moves it left; Zawgyi stores it in
#      visual order, i.e. before the base.
#
# Rule 3 must look BACKWARD, not forward. Asking "is U+1031 followed by a
# consonant" is ambiguous -- in မြေ|အောက် the vowel ends one syllable and a
# consonant starts the next, which is indistinguishable from Zawgyi's
# ordering and false-positives on perfectly good Unicode. Asking "does this
# vowel have a base behind it" needs no syllable segmentation at all, because
# the base IS the boundary.
#
# Measured over all 75,959 Myanmar runs of the fixture, applied to correctly
# converted output (where it must never fire): 25 false positives, 0.033%.
# Inspection shows most of those are text that was already double-converted
# before this guard existed, plus run fragments that begin mid-syllable (the
# base sits in the previous run) -- see check_runs_joined in the tests.
#
# LIMITS -- READ BEFORE EXTENDING
# -------------------------------
# This guard does NOT replace the model, and must not be used to. It has only
# ever been validated against a Zawgyi corpus, where it never had to identify
# a genuinely Unicode DOCUMENT; that test needs a Unicode corpus we do not
# have. It is a per-run VETO on top of the page verdict: the model says what
# the page probably is, the guard says whether this particular run can
# possibly be Zawgyi. Convert only when both agree.
# ═══════════════════════════════════════════════════════════════════════════════

# Base consonants + independent vowels that may carry a dependent vowel.
_BASE_CP = frozenset(range(0x1000, 0x1022)) | {0x1025, 0x1027}
# Medials may sit between a base and its vowel: က + ြ + ေ is well-formed.
_MEDIAL_CP = frozenset(range(0x103B, 0x103F))

_E_VOWEL = 0x1031
_VIRAMA = 0x1039

# Codepoints that exist only in Zawgyi. Their presence alone is proof.
_ZAWGYI_ONLY_RE = re.compile(
    "[\u1060-\u1097\u1033\u1034\u105a\u108b-\u1090]")

# A virama not followed by a consonant cannot be Unicode (nothing to stack).
_DANGLING_VIRAMA_RE = re.compile(
    "\u1039(?![\u1000-\u1021\u1025\u1027])")

# Pali stacks are HOMORGANIC: the stacked pair comes from one articulation
# group, or is a geminate. Harvested from converted output, this corpus uses
# exactly 21 distinct stack pairs and every one obeys the rule -- which is
# what makes မ္ဘ (labial+labial) a legal stack while က္လ is not. That single
# distinction separates Unicode ကမ္ဘာ from Zawgyi မ်က္လုံး, whose U+1039 is
# an asat rather than a stacker.
_STACK_GROUPS = (
    "\u1000\u1001\u1002\u1003\u1004",        # velar    k kh g gh ng
    "\u1005\u1006\u1007\u1008\u1009\u100a",  # palatal  c ch j jh ny
    "\u100b\u100c\u100d\u100e\u100f",        # retroflex
    "\u1010\u1011\u1012\u1013\u1014",        # dental   t th d dh n
    "\u1015\u1016\u1017\u1018\u1019",        # labial   p ph b bh m
    "\u101c",                                  # la, geminate only
    "\u101e",                                  # sa, geminate only
)
_STACK_GROUP_OF = {c: i for i, g in enumerate(_STACK_GROUPS) for c in g}
_STACK_RE = re.compile("([\u1000-\u1021])\u1039([\u1000-\u1021])")
_VIRAMA_RE = re.compile("\u1039")


def _stacks_are_wellformed(text):
    """(all_stacks_legal, how_many). A dangling virama scores (False, 0)."""
    pairs = list(_STACK_RE.finditer(text))
    if len(pairs) != len(_VIRAMA_RE.findall(text)):
        return False, 0
    return (all(_STACK_GROUP_OF.get(m.group(1), -1)
                == _STACK_GROUP_OF.get(m.group(2), -2) for m in pairs),
            len(pairs))


def _has_evowel_without_base(text: str) -> bool:
    """True if any U+1031 lacks a base consonant before it => Zawgyi order.

    Scans BACKWARD from each e-vowel, skipping medials, and asks whether a
    base consonant is sitting there. Unicode guarantees one; Zawgyi, which
    stores the vowel in visual order ahead of its base, does not.
    """
    for i, ch in enumerate(text):
        if ord(ch) != _E_VOWEL:
            continue
        j = i - 1
        while j >= 0 and ord(text[j]) in _MEDIAL_CP:
            j -= 1
        if j < 0 or ord(text[j]) not in _BASE_CP:
            return True
    return False


def has_zawgyi_evidence(text: str) -> bool:
    """True when `text` contains a configuration that is illegal in Unicode."""
    if not text:
        return False
    return bool(
        _ZAWGYI_ONLY_RE.search(text)
        or _DANGLING_VIRAMA_RE.search(text)
        or _has_evowel_without_base(text)
    )


def has_unicode_evidence(text: str) -> bool:
    """True when `text` contains something Zawgyi could not have produced.

    Three positive signals:

      * U+103E HA HTO. The embedded Zawgyi-One font has NO GLYPH for it (its
        ha hto is U+103D, the whole medial block being off by one), so a run
        containing it cannot have been typed as Zawgyi.
      * A well-formed homorganic stack (ကမ္ဘာ, ခန္ဓာ, သတ္တဝါ). Zawgyi's U+1039
        is an asat and attaches to anything, so it produces non-homorganic
        pairs like က္လ that no Pali stack ever contains.
      * U+103A ASAT with no malformed stack anywhere in the run. The asat
        alone is not enough: Zawgyi also uses U+103A, as its medial ya.
    """
    if not text:
        return False
    if "\u103e" in text:
        return True
    legal, count = _stacks_are_wellformed(text)
    if count:
        return legal
    if "\u103a" in text:
        return legal
    return False


def looks_like_zawgyi(text: str) -> bool:
    """Should `text` be handed to the Zawgyi->Unicode converter?

    Structural evidence, not a probability. Zawgyi evidence wins over Unicode
    evidence, because a run that mixes both is Zawgyi that happens to contain
    a sequence resembling a Unicode one — converting it is still correct.

    This is a VETO layered on the page verdict, so the default when a run is
    genuinely ambiguous is to CONVERT (the page said Zawgyi, and most runs on
    a Zawgyi page are Zawgyi). Only positive proof of existing Unicode stops
    conversion, because that is the irreversible direction: converting
    Unicode a second time destroys it, while leaving a Zawgyi run alone
    merely leaves it visibly unconverted.

    Measured over all 75,959 Myanmar runs of the fixture: 1,378 runs (1.81%)
    are vetoed, and inspection confirms every sampled one is genuinely
    already-Unicode text that conversion would have destroyed.
    """
    if not text:
        return False
    if has_zawgyi_evidence(text):
        return True
    return not has_unicode_evidence(text)


# ═══════════════════════════════════════════════════════════════════════════════
# 4.  Full post-process pipeline
# ═══════════════════════════════════════════════════════════════════════════════
def postprocess(texts: list[str]) -> list[str]:
    """Run the full cleanup + reorder filter over every page in `texts`."""
    cleaned = []
    for idx, raw in enumerate(texts):
        if not raw:
            cleaned.append(raw)
            continue
        step1 = clean_imposters(raw)
        step2 = reorder_marks(step1)
        cleaned.append(step2)
        # Optional debug: uncomment to see per-page diff
        # if step1 != raw or step2 != step1:
        #     print(f"    [clean] Page {idx+1}: imposter={step1!=raw}, reorder={step2!=step1}")
    return cleaned

