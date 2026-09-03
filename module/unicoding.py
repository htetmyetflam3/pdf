# Description: Zawgyi -> Unicode converter (Rabbit rules), precompiled.
# Rules live at module level and are compiled ONCE — zg2uni used to
# rebuild the whole rule list and re-run 81 re.sub() passes per call,
# which was brutal at 14k pages (called per page AND per line AND per run).
#
# RESYNCED WITH UPSTREAM RABBIT
# -----------------------------
# The table below is a verbatim port of the official rule set,
# Rabbit-Converter/Rabbit  source/rule/zg2uni.json  (118 rules, in order).
# The only transformation applied is JS `$1` backreferences -> Python `\1`;
# every pattern compiles under Python `re` unchanged, with no JS-only
# constructs. Rule ORDER IS SIGNIFICANT — these are sequential rewrites, not
# alternatives, so do not sort, dedupe or regroup them.
#
# It had previously drifted to 81 hand-maintained rules, which silently
# corrupted output in ways that survive visual inspection:
#
#   * `(ၻ|႓)` mapped to `်ဘ` (U+103A ASAT) instead of `္ဘ` (U+1039 VIRAMA).
#     Pali stacks rendered nearly identically but carried the wrong
#     codepoint: ကမၻာ -> ကမ်ဘာ instead of ကမ္ဘာ.
#   * `႑` mapped to `ဏ္႑`, leaving the raw Zawgyi codepoint in the output,
#     instead of `ဏ္ဍ`.
#   * `'/(ၳ|ၴ)/g'` was a JS regex literal pasted in as a Python string, so it
#     matched literal slashes and ၳ/ၴ never converted at all.
#   * The single `၀(ါ|ာ|ံ)` rule replaced upstream's eight context-sensitive
#     rules that disambiguate digit zero U+1040 from letter wa U+101D.
#
# If these ever need updating, re-port the JSON rather than editing entries
# by hand — hand-editing is what caused the drift.
import re

# (pattern, replacement) pairs — ported verbatim from Rabbit zg2uni.json.
_RULES = [
    ('([ိီွု့႕])\\1+', '\\1'),
    ('\u200b', ''),
    ('ွြ', 'ႊ'),
    ('(ွ|ႇ)', 'ှ'),
    ('ြ', 'ွ'),
    ('(ျ|ၾ|ၿ|ႀ|ႁ|ႂ|ႃ|ႄ)', 'ြ'),
    ('(်|ၽ)', 'ျ'),
    ('္', '်'),
    ('(ၦ|ၧ)', '္ဆ'),
    ('ၪ', 'ဉ'),
    ('ၫ', 'ည'),
    ('ၬ', '္ဋ'),
    ('ၭ', '္ဌ'),
    ('ၮ', 'ဍ္ဍ'),
    ('ၯ', 'ဍ္ဎ'),
    ('ၰ', '္ဏ'),
    ('(ၱ|ၲ)', '္တ'),
    ('ၠ', '္က'),
    ('ၡ', '္ခ'),
    ('ၢ', '္ဂ'),
    ('ၣ', '္ဃ'),
    ('ၥ', '္စ'),
    ('ၨ', '္ဇ'),
    ('ၩ', '္ဈ'),
    ('(ၳ|ၴ)', '္ထ'),
    ('ၵ', '္ဒ'),
    ('ၶ', '္ဓ'),
    ('ၷ', '္န'),
    ('ၸ', '္ပ'),
    ('ၹ', '္ဖ'),
    ('ၺ', '္ဗ'),
    ('ၼ', '္မ'),
    ('ႅ', '္လ'),
    ('ဳ', 'ု'),
    ('ဴ', 'ူ'),
    ('ဿ', 'ူ'),
    ('ႆ', 'ဿ'),
    ('ံႈ', 'ႈံ'),
    ('ႈ', 'ှု'),
    ('ႉ', 'ှူ'),
    ('ႊ', 'ွှ'),
    ('ျၤ', 'ၤျ'),
    ('ြ([က-အ])([ၤႋႍ])', '\\1ြ\\2'),
    ('(ေ)?([က-အ၀-၉])(ြ)?ၤ', 'င်္\\1\\2\\3'),
    ('(ေ)?([က-အ])(ျ|ြ)?ႋ', 'င်္\\1\\2\\3ိ'),
    ('(ေ)?([က-အ])(ျ)?ႌ', 'င်္\\1\\2\\3ီ'),
    ('(ေ)?([က-အ])([ျြ])?ႍ', 'င်္\\1\\2\\3ံ'),
    ('ႎ', 'ိံ'),
    ('ႏ', 'န'),
    ('႐', 'ရ'),
    ('႑', 'ဏ္ဍ'),
    ('႒', 'ဋ္ဌ'),
    ('မာ(ၻ|႓)', 'မ္ဘာ'),
    ('(ၻ|႓)', '္ဘ'),
    ('(႔|႕)', '့'),
    ('([က-အ])့ဲ', '\\1ဲ့'),
    ('႖', '္တွ'),
    ('႗', 'ဋ္ဋ'),
    ('ြ([က-အ])([က-အ])?', '\\1ြ\\2'),
    ('([က-အ])ြ်', 'ြ\\1်'),
    ('၇(?=[ာ-ူဲံ-းွး])', 'ရ'),
    ('ေ၇', 'ေရ'),
    ('၀(ီ|ု|ို|ူ|ံ|ွ|ှ)', 'ဝ\\1'),
    ('([^၀၁၂၃၄၅၆၇၈၉])၀ါ', '\\1ဝါ'),
    ('([၀၁၂၃၄၅၆၇၈၉])၀ါ(?!း)', '\\1ဝါ'),
    ('^၀(?=ါ)', 'ဝ'),
    ('၀ိ(?! ?/)', 'ဝိ'),
    ('([^၀-၉])၀([^၀-၉ ]|[၊။])', '\\1ဝ\\2'),
    ('([^၀-၉])၀(?=[\\f\\n\\r])', '\\1ဝ'),
    ('([^၀-၉])၀$', '\\1ဝ'),
    ('ေ([က-အဿ])(ှ)?(ျ)?', '\\1\\2\\3ေ'),
    ('([က-အ])ေ([ျြွှ]+)', '\\1\\2ေ'),
    ('ဲွ', 'ွဲ'),
    ('([ိီ])ျ', 'ျ\\1'),
    ('ွျ', 'ျွ'),
    ('့်', '့်'),
    ('ု(ိ|ီ|ံ|့)ု', 'ု\\1'),
    ('(ု|ူ)(ိ|ီ)', '\\2\\1'),
    ('(ှ)(ျ|ြ)', '\\2\\1'),
    ('ဥ(?=[့]?[်ာ])', 'ဉ'),
    ('ဦ', 'ဦ'),
    ('စျ', 'ဈ'),
    ('ံ(ု|ူ)', '\\1ံ'),
    ('ေ့ှ', 'ှေ့'),
    ('ေှာ', 'ှော'),
    ('ၚ', 'ါ်'),
    ('ေျှ', 'ျှေ'),
    ('(ိ|ီ)(ွ|ှ)', '\\2\\1'),
    ('ာ္([က-အ])', '္\\1ာ'),
    ('္ြ်္([က-အ])', '်္\\1ြ'),
    ('ြ္([က-အ])', '္\\1ြ'),
    ('ံ္([က-အ])', '္\\1ံ'),
    ('၎', '၎င်း'),
    ('၀(ါ|ာ|ံ)', 'ဝ\\1'),
    ('ဥ္', 'ဉ္'),
    ('([က-အ])ြေွ', '\\1ြွေ'),
    ('([က-အ])ျေွ(ှ)?', '\\1ျွ\\2ေ'),
    ('([က-အ])ွေျ', '\\1ျွေ'),
    ('([က-အ])ေ(္[က-အ]ွ?)', '\\1\\2ေ'),
    ('း်', '်း'),
    ('ိ်|်ိ', 'ိ'),
    ('ို်', 'ို'),
    (' ့', '့'),
    ('့ံ', 'ံ့'),
    ('[ိ]+', 'ိ'),
    ('[်]+', '်'),
    ('[ွ]+', 'ွ'),
    ('[့]+', '့'),
    ('[ီ]+', 'ီ'),
    ('ိီ|ီိ', 'ီ'),
    ('ုိ', 'ို'),
    ('့့', '့'),
    ('ဲဲ', 'ဲ'),
    ('၄င်း', '၎င်း'),
    ('([ိီ])္([က-အ])', '္\\2\\1'),
    ('(ြေ)္([က-အ])', '္\\2\\1'),
    ('ံွ', 'ွံ'),
    ('၇((?=[က-အ]်)|(?=[ာ-ူဲံ-းွှ]))', 'ရ'),
]

# Precompiled once at import time (hot path).
_COMPILED = [(re.compile(p), r) for p, r in _RULES]


class Rabbit:
    @staticmethod
    def zg2uni(text):
        """Convert Zawgyi text to Unicode by applying every rule in order."""
        for pattern, repl in _COMPILED:
            text = pattern.sub(repl, text)
        return text
