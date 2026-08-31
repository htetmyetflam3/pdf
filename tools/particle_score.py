#!/usr/bin/env python3
"""
Description: Reads syllable-segmented Burmese text (double-space delimited
syllables, sentences end with Burmese fullstop ။ U+104B). Extracts sentence-final
particles, skips quoted speech, gates by first character, and writes two output
files: one with occurrence counts and one with pure final syllables.

Input:  Syllable text file(s) — syllables separated by double spaces,
        sentences end with " ။" (space + Burmese fullstop U+104B).
Output: output/final_particles_with_counts.txt  (particle<tab>count)
        output/final_particles_pure.txt          (one particle per line)

Usage:  python tools/final_particle_score.py book.txt [more.txt ...]
        python tools/final_particle_score.py book.txt --include-quotes
"""

import sys
import os
import re
from collections import Counter

# ── gate: first char must be one of these ────────────────────────────────────
BASE = set(
    'က ခ ဂ ဃ င စ ဇ ည ဋ ဉ ဍ ဏ တ ထ ဒ န ပ ဖ ဗ ဘ မ ယ ရ လ သ ဟ အ '
    'ဥ ဧ ဈ ဝ ဓ ဩ ဿ ဣ ဦ ဠ ဌ ဆ ဎ ဪ ၎'.split()
)
STANDALONE_FIRST = {'၍', '၌', '၏', 'ဤ', 'ဪ', '၎', 'ဦ'}
GATE = BASE | STANDALONE_FIRST

# ── regex patterns ───────────────────────────────────────────────────────────
# Quote marks that indicate quoted speech to skip.
_QUOTE_CHARS_RE = re.compile(r'["\u201C\u201D«»`\u2018\u2019\']')

# Trailing tokens made only of quotes, punctuation, ellipsis, spaces.
# Handles multiple dots: ...  . . .  …  etc.
_TAIL_PUNCT_RE = re.compile(
    r'^[\u201C\u201D«»"`\u2018\u2019\''
    r'\.…,!?()\-—– ]+$'
)


def strip_tail_punct(tokens):
    """Drop trailing tokens that are only quotes/punctuation/dots/ellipsis."""
    while tokens and _TAIL_PUNCT_RE.match(tokens[-1]):
        tokens.pop()
    return tokens


def sentence_stream(text, skip_quoted=True):
    """
    Yield the final-particle candidate token of each sentence.
    Sentences are split on " ။" (space + Burmese fullstop U+104B).
    Chunks containing quote marks are skipped (quoted speech).
    Trailing quote/punct-only tokens are stripped before reading the particle.
    Handles sentences ending with multiple dots (ellipsis) before ။.
    """
    # Split on " ။" — exactly like the original working script
    for chunk in text.split(" ။"):
        chunk = chunk.strip()
        if not chunk:
            continue

        if skip_quoted and _QUOTE_CHARS_RE.search(chunk):
            continue

        # Split on whitespace to get syllable tokens
        tokens = chunk.split()

        # Strip trailing punctuation/quote-only tokens (handles ...  "  ”  etc.)
        tokens = strip_tail_punct(tokens)

        if tokens:
            yield tokens[-1]


def extract_all_particles(text):
    """Extract every final particle (no quote filtering) for reporting."""
    for chunk in text.split(" ။"):
        chunk = chunk.strip()
        if not chunk:
            continue
        tokens = chunk.split()
        tokens = strip_tail_punct(tokens)
        if tokens:
            yield tokens[-1]


def report(path, include_quotes=False):
    text = open(path, encoding="utf-8").read()

    # Raw extraction (all sentences, no quote skip)
    all_parts = list(extract_all_particles(text))

    # Narration-only extraction (skip quoted speech)
    narr_parts = list(sentence_stream(text, skip_quoted=not include_quotes))
    excluded = len(all_parts) - len(narr_parts)

    print(f"\n════ {path}")
    print(f"sentence-final tokens: {len(all_parts):,} | "
          f"quoted-dialogue excluded: {excluded:,} ({excluded/len(all_parts):.1%}) | "
          f"scored: {len(narr_parts):,}")

    if not narr_parts:
        print("nothing left to score")
        return

    # Gate: first char must be in GATE set
    gated = [p for p in narr_parts if p and p[0] in GATE]
    skipped = Counter(p for p in narr_parts if not (p and p[0] in GATE))

    print(f"gate passed: {len(gated):,} ({len(gated)/len(narr_parts):.1%}) | "
          f"skipped: {len(narr_parts)-len(gated):,}")
    if skipped:
        print(f"skipped samples: {skipped.most_common(10)}")

    if not gated:
        print("no gated particles to tally")
        return

    tally = Counter(gated)
    sents = len(gated)

    print(f"\n── per-particle tally (top 25) ──")
    print(f"{'particle':<12s} {'count':>8s}")
    for p, c in tally.most_common(25):
        print(f"{p:<12s} {c:8,}")

    # ── write output files ────────────────────────────────────────────────
    os.makedirs("output", exist_ok=True)

    # File 1: particle + occurrence count (tab-separated)
    counts_path = "output/final_particles_with_counts.txt"
    with open(counts_path, "w", encoding="utf-8") as f:
        for p, c in tally.most_common():
            f.write(f"{p}\t{c}\n")
    print(f"\n-> wrote {counts_path}  ({len(tally)} particles)")

    # File 2: pure final syllables (one per line, no counts)
    pure_path = "output/final_particles_pure.txt"
    with open(pure_path, "w", encoding="utf-8") as f:
        for p, _ in tally.most_common():
            f.write(f"{p}\n")
    print(f"-> wrote {pure_path}  ({len(tally)} particles)")

    # Summary
    print(f"\nbook total: {sents:,} gated particles | {len(tally):,} unique")


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
