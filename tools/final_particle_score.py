#!/usr/bin/env python3
"""
Final-particle style scoring for syllable-segmented Burmese text.

Input: the segmenter's output (syllables double-space delimited,
sentences end with the Burmese fullstop U+104F).

Rule (data-driven, no linguistics needed):
  the whitespace-token directly before " ။" is the sentence-final particle.
  If its FIRST character is not in BASE ∪ STANDALONE -> skip (broken vowel,
  latin, digits, headers...). Otherwise tally it.

Output:
  - gate skip report (what was rejected and why)
  - per-particle table: count, share of sentences, register, writtenness
  - book average writtenness + threshold sweep (lowest usable threshold)

Usage: python tools/final_particle_score.py <syllable_txt> [more.txt ...]
"""

import sys
from collections import Counter

# ── gate: first char must be one of these (verbatim user lists) ──────────────
BASE = set(
    'က ခ ဂ ဃ င စ ဇ ည ဋ ဉ ဍ ဏ တ ထ ဒ န ပ ဖ ဗ ဘ မ ယ ရ လ သ ဟ အ '
    'ဥ ဧ ဈ ဝ ဓ ဩ ဿ ဣ ဦ ဠ ဌ ဆ ဎ ဪ ၎'.split()
)
STANDALONE_FIRST = set('၍ ၌ ၏ ဤ ဪ ၎င်း ဦး'.split())  # first chars taken below
STANDALONE_FIRST = {s[0] for s in STANDALONE_FIRST}
GATE = BASE | STANDALONE_FIRST

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


def extract_particles(text: str):
    """Final particle = last whitespace token before each ' ။'."""
    for chunk in text.split(" ။"):
        parts = chunk.split()
        if parts:
            yield parts[-1]


def main(paths):
    for path in paths:
        text = open(path, encoding="utf-8").read()
        raw = list(extract_particles(text))
        gated = [p for p in raw if p and p[0] in GATE]
        skipped = Counter(p for p in raw if not (p and p[0] in GATE))

        n = len(raw)
        print(f"\n════ {path}")
        print(f"sentence-final tokens: {n:,} | passed gate: {len(gated):,} "
              f"({len(gated)/n:.1%}) | skipped: {n-len(gated):,}")
        print(f"skipped samples: {skipped.most_common(10)}")

        tally = Counter(gated)
        sents = len(gated)
        scores = []
        print(f"\n── per-particle score (top 30) ──")
        print(f"{'particle':10s} {'count':>8s} {'share':>7s} {'register':10s} {'w':>4s}")
        for p, c in tally.most_common(30):
            w, reg = writtenness(p)
            scores.extend([w] * c)
            print(f"{p:10s} {c:8,} {c/sents:7.2%} {reg:10s} {w:4.1f}")
        # score ALL particles (not just top30) for the averages below
        if len(tally) > 30:
            for p, c in tally.most_common()[30:]:
                w, _ = writtenness(p)
                scores.extend([w] * c)

        scores.sort()
        avg = sum(scores) / len(scores)
        print(f"\nbook writtenness avg = {avg:.3f}  (1 = pure written, 0 = pure spoken)")

        print("\n── threshold sweep: keep sentences with w ≥ T ──")
        print(f"{'T':>5s} {'kept':>7s} {'avg-of-kept':>12s}")
        for T in (1.0, 0.9, 0.8, 0.75, 0.6, 0.5, 0.25):
            kept = [s for s in scores if s >= T - 1e-9]
            if kept:
                print(f"{T:5.2f} {len(kept)/len(scores):7.1%} {sum(kept)/len(kept):12.3f}")
        # lowest threshold whose kept-average still reads "written" (≥0.85)
        lowest = None
        for T in [i / 100 for i in range(100, 0, -1)]:
            kept = [s for s in scores if s >= T - 1e-9]
            if kept and sum(kept) / len(kept) >= 0.85:
                lowest = (T, len(kept) / len(scores), sum(kept) / len(kept))
            else:
                break
        if lowest:
            print(f"\nlowest usable threshold: T={lowest[0]:.2f} "
                  f"(keeps {lowest[1]:.1%} of gated particles, avg {lowest[2]:.3f})")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1:])
