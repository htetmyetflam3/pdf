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
    # NOTE: \u1039 (stacker) is intentionally NOT a reorderable mark. Per the
    # design: it belongs to the base cluster, resets the mark-run state, and the
    # base after it starts a fresh ordering. Sequences like kinzi
    # '\u1004\u103A\u1039' (asat BEFORE stacker) are canonical and pass through
    # untouched. (The original literal 'u\1039' was an octal escape that never
    # matched anything, so the pass-through behavior was already in effect.)
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

