#!/usr/bin/env python3
"""
Final-particle style scoring for syllable-segmented Burmese text.

Input: the segmenter's output (syllables double-space delimited,
sentences end with the Burmese fullstop U+104F). Quote marks are
preserved in the input.

Rule (data-driven, no linguistics needed):
  the whitespace-token directly before " ။" is the sentence-final particle.
  If its FIRST character is not in BASE ∪ STANDALONE -> skip (broken vowel,
  latin, digits, headers...). Otherwise tally it.

Quoted-dialogue handling (data-driven):
  The source quotes every dialogue line/sentence itself:  " ... ",
  " ... ", ' ... '  (mixed styles, sometimes with . . . inside).
  So a sentence-chunk that CONTAINS any quote mark is quoted speech or
  quoted thought and is excluded from the writing-style score. No global
  quote state is tracked (the source's quote typography is inconsistent;
  any depth model drifts). Chunks mixing dialogue + narration are also
  excluded (conservative). Trailing quote/punct-only tokens are stripped
  before reading the particle, so `...  သည်  ”  ။` still scores သည်.
  Use --include-quotes to score everything.

Output:
  - quoted-dialogue report (share of sentences excluded)
  - gate skip report (what was rejected and why)
  - per-particle table: count, share, register, writtenness
  - book average writtenness + threshold sweep (lowest usable threshold)
  - narration wordlist export (count>=3, + reduplicated)

Usage: python tools/final_particle_score.py <syllable_txt> [more.txt ...]
       python tools/final_particle_score.py book.txt --include-quotes
"""

import sys
from collections import Counter

# ── gate: first char must be one of these (verbatim user lists) ──────────────
BASE = set(
    'က ခ ဂ ဃ င စ ဇ ည ဋ ဉ ဍ ဏ တ ထ ဒ န ပ ဖ ဗ ဘ မ ယ ရ လ သ ဟ အ '
    'ဥ ဧ ဈ ဝ ဓ ဩ ဿ ဣ ဦ ဠ ဌ ဆ ဎ ဪ ၎'.split()
)
STANDALONE_FIRST = {'၍', '၌', '၏', 'ဤ', 'ဪ', '၎', 'ဦ'}
GATE = BASE | STANDALONE_FIRST

# ── quoted-dialogue handling ─────────────────────────────────────────────────
# Local rule (validated on the source): every quoted line carries its own
# quote marks (" ... ", " ... ", ' ... '). If a chunk contains any quote
# mark it is quoted speech/thought -> excluded from the style score.
import re
_QUOTE_EVENT_RE = re.compile('["“”«»`‘’\']')
# tokens made only of quotes/punct at a chunk tail are stripped pre-gate
_TAIL_PUNCT_RE = re.compile('^[“”«»"`‘’\'.…,!?()\\-—– ]+$')


def strip_tail_punct(tokens):
    """Drop trailing tokens that are only quotes/punctuation."""
    while tokens and _TAIL_PUNCT_RE.match(tokens[-1]):
        tokens.pop()
    return tokens


def sentence_stream(text: str, skip_quoted: bool = True):
    """Yield the final-particle candidate token of each ။ sentence.

    When skip_quoted is set, chunks containing quote marks (quoted speech or
    quoted thought) yield nothing."""
    for chunk in text.split(" ။"):
        if skip_quoted and _QUOTE_EVENT_RE.search(chunk):
            continue
        parts = strip_tail_punct(chunk.split())
        if parts:
            yield parts[-1]


def extract_particles(text: str):
    """Final particle = last whitespace token before each ' ။' (no quote logic;
    kept for backward compatibility)."""
    for chunk in text.split(" ။"):
        parts = chunk.split()
        if parts:
            yield parts[-1]


# ── register lexicon (edit freely; only top-frequency particles matter) ──────
WRITTEN = {  # literary style weight 1.0
    'သည်', '၏', 'ချေ', 'မည်', 'ပင်', 'လော', 'သို့', 'အုံး', 'သည့်', 'ဟုတ်',
    '၍', '၌', 'ဤ',
}
SPOKEN = {  # colloquial weight 0.0
    'တယ်', 'ဘူး', 'လား', 'မယ်', 'လဲ', 'ပဲ', 'ပေ', 'ပေါ့', 'နည်း', 'သလား',
    'နိုင်ဘူး', 'ခူး', 'ဂျိုး',
}
NEUTRAL = {  # both registers (polite/completion/verb tails) weight 0.5
    'ပါ', 'ပြီ', 'လေ', 'သာ', 'တော့', 'ရှိ', 'ခဲ့', 'သေး', 'ရ', 'တာ', 'နဲ့',
    'နိုင်', 'ဆဲ', 'ည်', 'လာ', 'ကွယ်', 'မောင်', 'ရဝင်',
}


def writtenness(p: str) -> tuple[float, str]:
    if p in WRITTEN:
        return 1.0, "written"
    if p in SPOKEN:
        return 0.0, "spoken"
    if p in NEUTRAL:
        return 0.5, "neutral"
    return 0.5, "unlisted"


def report(path: str, include_quotes: bool = False):
    text = open(path, encoding="utf-8").read()

    all_parts = [p for p in extract_particles(text)]  # raw, for context
    narr_parts = list(sentence_stream(text, skip_quoted=not include_quotes))
    excluded = len(all_parts) - len(narr_parts)

    print(f"\n════ {path}")
    print(f"sentence-final tokens: {len(all_parts):,} | "
          f"quoted-dialogue excluded: {excluded:,} ({excluded/len(all_parts):.1%}) | "
          f"scored: {len(narr_parts):,}")
    if not narr_parts:
        print("nothing left to score")
        return

    gated = [p for p in narr_parts if p and p[0] in GATE]
    skipped = Counter(p for p in narr_parts if not (p and p[0] in GATE))
    print(f"gate passed: {len(gated):,} ({len(gated)/len(narr_parts):.1%}) | "
          f"skipped: {len(narr_parts)-len(gated):,}")
    print(f"skipped samples: {skipped.most_common(10)}")

    tally = Counter(gated)
    sents = len(gated)

    # score EVERY gated particle (the top table is display-only)
    scores = sorted(writtenness(p)[0] for p in gated)

    print(f"\n── per-particle score (top 25) ──")
    print(f"{'particle':10s} {'count':>8s} {'share':>7s} {'register':10s} {'w':>4s}")
    for p, c in tally.most_common(25):
        w, reg = writtenness(p)
        print(f"{p:10s} {c:8,} {c/sents:7.2%} {reg:10s} {w:4.1f}")

    avg = sum(scores) / len(scores)
    print(f"\nnarration writtenness avg = {avg:.3f}  (1 = pure written, 0 = pure spoken)")

    print("\n── threshold sweep: keep sentences with w ≥ T ──")
    print(f"{'T':>5s} {'kept':>7s} {'avg-of-kept':>12s}")
    lowest = None
    for T in [i / 100 for i in range(100, 0, -1)]:
        kept = [s for s in scores if s >= T - 1e-9]
        if not kept:
            break
        kavg = sum(kept) / len(kept)
        if T in (1.0, 0.9, 0.8, 0.75, 0.6, 0.5, 0.25):
            print(f"{T:5.2f} {len(kept)/len(scores):7.1%} {kavg:12.3f}")
        if kavg >= 0.85:
            lowest = (T, len(kept) / len(scores), kavg)
        else:
            break
    if lowest:
        print(f"\nlowest usable threshold: T={lowest[0]:.2f} "
              f"(keeps {lowest[1]:.1%} of gated particles, avg {lowest[2]:.3f})")

    # export lists
    above = sorted(((p, c) for p, c in tally.items() if writtenness(p)[0] >= 0.25),
                   key=lambda x: -x[1])
    spoken_ex = sorted(((p, c) for p, c in tally.items() if writtenness(p)[0] < 0.25),
                       key=lambda x: -x[1])
    core = [p for p, c in above if c >= 3]
    if core:
        with open("output/final_particles_narration_count3.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(core) + "\n")
        with open("output/final_particles_narration_count3_reduplicated.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(p + p for p in core) + "\n")
        print(f"\nexported: narration wordlist (count>=3): {len(core)} particles "
              f"-> output/final_particles_narration_count3*.txt")
    if spoken_ex:
        print(f"excluded spoken particles ({len(spoken_ex)}): "
              + " ".join(p for p, _ in spoken_ex))


def main(argv):
    include_quotes = "--include-quotes" in argv
    paths = [a for a in argv if not a.startswith("--")]
    if not paths:
        print(__doc__)
        sys.exit(1)
    for path in paths:
        report(path, include_quotes)


if __name__ == "__main__":
    main(sys.argv[1:])
