#!/usr/bin/env python3
"""
Description: Reads syllable-segmented Burmese text (double-space delimited
syllables, sentences end with Burmese fullstop ။ U+104B). Extracts sentence-final
particles with configurable syllable count. Special handling for base+U+103A (asat)
syllables — walks backwards to include preceding syllables up to the nearest one
that starts with a base consonant.

Input:  Syllable text file(s) — syllables separated by double spaces,
        sentences end with " ။" (space + Burmese fullstop U+104B).
Output: output/final_particles_with_counts.txt  (particle<tab>count)
        output/final_particles_pure.txt          (one particle per line)

Config:  Set FINAL_SYLLABLE_COUNT below (default: 2)

Usage:  python tools/final_particle_score.py book.txt [more.txt ...]
        python tools/final_particle_score.py book.txt --include-quotes
"""

import sys
import os
import re
from collections import Counter

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG — change this number to set how many final syllables to extract
# ═══════════════════════════════════════════════════════════════════════════════
FINAL_SYLLABLE_COUNT = 1  # <-- hardcode this number. e.g. 1, 2, 3, ...

# ═══════════════════════════════════════════════════════════════════════════════

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
_TAIL_PUNCT_RE = re.compile(
    r'^[\u201C\u201D«»"`\u2018\u2019\''
    r'\.…,!?()\-—– ]+$'
)

# Base + U+103A (asat): exactly one base consonant followed by asat (်).
# Built dynamically from the BASE set so it stays in sync.
_BASE_ASAT_RE = re.compile(
    r'^[' + ''.join(BASE) + r']\u103A$'
)


def strip_tail_punct(tokens):
    """Drop trailing tokens that are only quotes/punctuation/dots/ellipsis."""
    while tokens and _TAIL_PUNCT_RE.match(tokens[-1]):
        tokens.pop()
    return tokens


def is_inside_quotes(chunk):
    """Check if a sentence chunk contains any quote mark."""
    return bool(_QUOTE_CHARS_RE.search(chunk))


def extract_final_syllables(tokens, count):
    """
    Extract the last `count` syllables from the token list.

    Special rule: if the last token is exactly base+U+103A (asat, e.g. က်, တ်,
    ပ်, ည်), walk backwards through preceding tokens and include them until we
    hit a token that starts with a base consonant. Include that base-starting
    token too. This captures patterns like:

        base  [some syllables]  base+asat
        သွား  သည်
        ပြော  သည်
        foo  bar  baz  ပ်   -> if baz doesn't start with base, keep going back
    """
    if not tokens:
        return None

    # Start with the last `count` tokens
    start = max(0, len(tokens) - count)
    selected = tokens[start:]

    # Special rule: if the very last token matches base+asat (e.g. ည် in သည်)
    if selected and _BASE_ASAT_RE.match(selected[-1]):
        # Walk backwards to find the nearest token starting with a base consonant
        idx = start - 1
        while idx >= 0:
            if tokens[idx] and tokens[idx][0] in BASE:
                # Include from this base-starting syllable up to the end
                selected = tokens[idx:]
                break
            idx -= 1
        # If no base-starting syllable found, keep the original `count` selection

    return '  '.join(selected)


def sentence_stream(text, skip_quoted=True):
    """
    Yield the final-particle candidate of each sentence.
    Sentences split on " ။". Quoted chunks skipped.
    Configurable syllable count + base+asat handling.
    """
    for chunk in text.split(" ။"):
        chunk = chunk.strip()
        if not chunk:
            continue

        if skip_quoted and is_inside_quotes(chunk):
            continue

        tokens = chunk.split()
        tokens = strip_tail_punct(tokens)

        particle = extract_final_syllables(tokens, FINAL_SYLLABLE_COUNT)
        if particle:
            yield particle


def extract_all_particles(text):
    """Extract every final particle (no quote filtering) for reporting."""
    for chunk in text.split(" ။"):
        chunk = chunk.strip()
        if not chunk:
            continue
        tokens = chunk.split()
        tokens = strip_tail_punct(tokens)
        particle = extract_final_syllables(tokens, FINAL_SYLLABLE_COUNT)
        if particle:
            yield particle


def report(path, include_quotes=False):
    text = open(path, encoding="utf-8").read()

    all_parts = list(extract_all_particles(text))
    narr_parts = list(sentence_stream(text, skip_quoted=not include_quotes))
    excluded = len(all_parts) - len(narr_parts)

    print(f"\n════ {path}")
    print(f"final-syllable count setting: {FINAL_SYLLABLE_COUNT}")
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
    if skipped:
        print(f"skipped samples: {skipped.most_common(10)}")

    if not gated:
        print("no gated particles to tally")
        return

    tally = Counter(gated)
    sents = len(gated)

    print(f"\n── per-particle tally (top 25) ──")
    print(f"{'particle':<20s} {'count':>8s}")
    for p, c in tally.most_common(25):
        print(f"{p:<20s} {c:8,}")

    # ── write output files ────────────────────────────────────────────────
    os.makedirs("output", exist_ok=True)

    counts_path = "output/final_particles_with_counts.txt"
    with open(counts_path, "w", encoding="utf-8") as f:
        for p, c in tally.most_common():
            f.write(f"{p}\t{c}\n")
    print(f"\n-> wrote {counts_path}  ({len(tally)} particles)")

    pure_path = "output/final_particles_pure.txt"
    with open(pure_path, "w", encoding="utf-8") as f:
        for p, _ in tally.most_common():
            f.write(f"{p}\n")
    print(f"-> wrote {pure_path}  ({len(tally)} particles)")

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
