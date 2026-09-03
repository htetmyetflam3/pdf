# Per-run Zawgyi guard — notes for the next session

## What changed

Encoding detection used to be **per page**. Pages in this corpus mix
encodings, so a page the model called Zawgyi would have its already-Unicode
runs converted a second time and destroyed. Detection is now **per run**,
with a structural guard deciding each run.

- `module/postprocessor.py` — `has_zawgyi_evidence()`, `has_unicode_evidence()`,
  `looks_like_zawgyi()`.
- `module/main.py` — `_convert_if_zawgyi()`, called per run from
  `convert_page()`; `_apply_to_layout_page()` rebuilds `line["text"]` from
  the transformed runs rather than transforming the joined line.
- `tests/test_zawgyi_guard.py` — 20 tests.

## The design in one line

**The guard is a veto, not a detector.** The page verdict still decides; the
guard only stops conversion when the run carries positive proof of already
being Unicode.

The asymmetry is deliberate. Converting Unicode twice is irreversible
(`၀င်ရောက်` → `၀ငျရောကျ`). Leaving a Zawgyi run alone is visible and
recoverable. So **ambiguous runs convert**.

## The signals

Zawgyi evidence (any one ⇒ convert), all configurations illegal in Unicode:

1. Zawgyi-only codepoints — U+1060–1097, U+1033/1034, U+105A, U+108B–1090.
2. A **dangling virama** — U+1039 not followed by a consonant. Zawgyi uses it
   as a visible asat, so it lands before spaces and punctuation.
3. An **e-vowel with no base behind it**. Unicode writes `base [medials] ေ`;
   Zawgyi writes `ေ base`.

Unicode evidence (any one ⇒ veto):

1. **U+103E HA HTO** — the embedded Zawgyi-One font has no glyph for it (its
   ha hto is U+103D; the whole medial block is off by one), so a run
   containing it cannot have been typed as Zawgyi.
2. A **well-formed homorganic stack** — `ကမ္ဘာ`, `ခန္ဓာ`, `သတ္တဝါ`.
3. **U+103A asat** with no malformed stack in the run.

Zawgyi evidence outranks Unicode evidence.

## Why rule 3 of the e-vowel test looks backward

The obvious test — "is U+1031 *followed* by a consonant?" — is wrong and must
not be reintroduced. In `မြေ|အောက်` the vowel ends one syllable and a
consonant begins the next, which is byte-identical to Zawgyi ordering. It
false-positives on valid Unicode.

Looking **backward** for a base consonant needs no syllable segmentation,
because the base *is* the boundary.

## Why the homorganic rule exists

`ကမ္ဘာ` (Unicode stack) and `မ်က္လုံး` (Zawgyi asat) are structurally
identical: consonant + U+1039 + consonant. No amount of context-free regex
separates them.

Pali stacks are **homorganic** — both consonants come from one articulation
group, or are a geminate. Harvested from converted output, this corpus uses
exactly **21 distinct stack pairs** and every one obeys the rule. `မ္ဘ`
(labial+labial) is legal; `က္လ` (velar+lateral) is not. That is the whole
discriminator.

## Measurements (fixture `input/pdf/test.pdf`, 75,959 Myanmar runs)

| metric | value |
| --- | --- |
| runs vetoed as already-Unicode | 1,378 (1.81%) |
| converted runs left alone on a second pass | ~86% |
| page-level agreement with the Google model | 2,289 / 2,289 |

Character counts moved (page counts did not): `test.pdf` 1,217,550 →
**1,217,642**; `c701to1k.pdf` 3,806,649 → **3,806,796**. Both increases are
the guard declining to collapse asat into medial ya.

## Known limits

- **A run that mixes both encodings internally cannot be handled.** The guard
  decides per run; splitting one needs per-syllable detection. Pinned by
  `test_mixed_runs_are_a_known_limit` (currently <5% of runs) so a regression
  is visible. This is where the owner's syllable segmenter would help.
- **The guard has never been tested against a genuinely Unicode document.**
  The corpus is Zawgyi-only, so the 100% model agreement proves little. It
  must stay layered on the model, not replace it.
- **~86%, not 100%, of converted runs survive a second pass.** The residue is
  short markerless fragments (`ကက`) that carry no positive Unicode signal and
  so re-trip convert-by-default. Harmless — the pipeline converts once.

## Dead ends — do not retry

- Forward-scanning e-vowel rule (`\u1031[CONS]`). Ambiguous; false-positives.
- "Virama present but no U+103A ⇒ Zawgyi". Scored 12/13 on units but breaks
  `ကမ္ဘာ`, which is Unicode with a legitimate stack and no asat.
- Requiring the asat not be followed by a vowel. Breaks `ဒိန်း`, `ကျောင်းသား`.
- Per-character detection. Impossible: U+103D is Zawgyi ha hto *and* Unicode
  medial wa. The minimum decidable unit is a 2-character ordered string.
- Using the font name. Every run in the fixture is `ABCDEE+Zawgyi-One`,
  including the Unicode ones — it carries no signal.
