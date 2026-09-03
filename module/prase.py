"""
Description: PDF text extraction engine with layout preservation.
Reads: PDF files with embedded CID/TrueType fonts.
Processes: PDF object parsing, TTF cmap extraction, content stream tokenization,
           text matrix tracking, multi-font glyph decoding, line reconstruction.
Outputs: list of page strings, metadata dict.
"""

import re
import zlib
import struct

from .pdfobjects import PdfObjectStore


def parse_pdf_objects(raw: bytes) -> PdfObjectStore:
    """Return a lazy, xref-backed object store.

    RESTRUCTURED: this used to regex-scan the entire file and hold every
    object body in a dict (~76 MB / 2.1 s on a 33 MB, 14k-page file). It now
    reads only the trailer and the xref subsection headers; object bodies are
    sliced on demand. The return value still behaves like the old dict
    (`.get`, `.items()`, `in`, `[]`), so every downstream function that takes
    `objects` keeps working unchanged.
    """
    return PdfObjectStore(raw)


def get_stream(objects: dict, num: int, gen: int = 0) -> bytes | None:
    d = objects.get((num, gen), b'')
    i = d.find(b'stream')
    if i == -1:
        return None
    j = i + 6
    if j < len(d) and d[j:j+1] == b'\r':
        j += 1
    if j < len(d) and d[j:j+1] == b'\n':
        j += 1
    k = d.rfind(b'endstream')
    if k == -1:
        return None
    b = d[j:k]
    if b'/FlateDecode' in d[:i]:
        try:
            return zlib.decompress(b)
        except Exception:
            return None
    return b


def _decode_info_blob(info_d: str) -> dict:
    """Pull /Key (value) pairs out of an /Info dictionary body."""
    out = {}
    for kv in re.finditer(r'/([^\s/\[\]<>()]+)\s*\(([^)]*)\)', info_d):
        out[kv.group(1)] = kv.group(2)
    for kv in re.finditer(r'/([^\s/\[\]<>()]+)\s*<([0-9A-Fa-f\s]+)>', info_d):
        raw = re.sub(r'\s', '', kv.group(2))
        try:
            b = bytes.fromhex(raw)
            if b.startswith((b'\xfe\xff', b'\xff\xfe')):
                out[kv.group(1)] = b.decode('utf-16').strip('\ufeff')
            else:
                out.setdefault(kv.group(1), b.decode('latin-1', 'replace'))
        except Exception:
            continue
    return out


def extract_metadata(objects: dict) -> dict:
    """Document /Info dictionary.

    RESTRUCTURED: resolves /Info straight from the trailer (one object read)
    instead of iterating every object looking for /Catalog. Falls back to the
    old scan when the trailer is unusable.
    """
    info_ref = getattr(objects, "info_ref", lambda: None)()
    if info_ref is not None:
        body = objects.get((info_ref, 0))
        if body:
            return _decode_info_blob(body.decode('latin-1', errors='replace'))

    root_ref = getattr(objects, "root_ref", lambda: None)()
    if root_ref is not None:
        cat = objects.get((root_ref, 0), b'').decode('latin-1', errors='replace')
        m = re.search(r'/Info\s+(\d+)\s+\d+\s+R', cat)
        if m:
            body = objects.get((int(m.group(1)), 0), b'')
            return _decode_info_blob(body.decode('latin-1', errors='replace'))

    metadata = {}
    for (n, g), d in objects.items():
        d_str = d.decode('latin-1', errors='replace')
        if re.search(r'/Type\s*/Catalog', d_str):
            info_m = re.search(r'/Info\s+(\d+)\s+\d+\s+R', d_str)
            if info_m:
                info_n = int(info_m.group(1))
                info_d = objects.get((info_n, 0), b'').decode('latin-1', errors='replace')
                metadata = _decode_info_blob(info_d)
            break
    return metadata


def find_font_file2(objects: dict, first_page: tuple[int, int] | None = None) -> tuple[int, int] | None:
    """Locate an embedded /FontFile2 (TrueType) stream.

    RESTRUCTURED: when `first_page` is given, resolves it through that page's
    /Resources -> /Font -> /FontDescriptor (a handful of object reads). Only
    falls back to the whole-document scan if that finds nothing.
    """
    if first_page is not None:
        hit = _font_file2_from_page(objects, first_page[0], first_page[1])
        if hit:
            return hit

    for (n, g), d in objects.items():
        d_str = d.decode('latin-1', errors='replace')
        if '/FontFile2' in d_str:
            m = re.search(r'/FontFile2\s+(\d+)\s+(\d+)\s+R', d_str)
            if m:
                return int(m.group(1)), int(m.group(2))
    return None


def _font_file2_from_page(objects: dict, page_num: int, page_gen: int) -> tuple[int, int] | None:
    """Follow one page's font resources down to a /FontFile2 stream ref."""
    res = _page_resources(objects, page_num, page_gen)
    if not res:
        return None
    fonts = _resolve_indirect(objects, res, b"Font")
    if not fonts:
        return None

    queue: list[bytes] = []
    for ref in re.finditer(rb"/[^\s/\[\]<>()]+\s+(\d+)\s+(\d+)\s+R", fonts):
        body = objects.get((int(ref.group(1)), int(ref.group(2))))
        if body:
            queue.append(body)

    seen = 0
    while queue and seen < 64:
        body = queue.pop(0)
        seen += 1
        m = re.search(rb"/FontFile2\s+(\d+)\s+(\d+)\s+R", body)
        if m:
            return int(m.group(1)), int(m.group(2))
        # Descend: /DescendantFonts (Type0) then /FontDescriptor.
        for key in (b"DescendantFonts", b"FontDescriptor"):
            nxt = _resolve_indirect(objects, body, key)
            if nxt:
                queue.append(nxt)
            for r in re.finditer(rb"/" + key + rb"\s*\[?\s*(\d+)\s+(\d+)\s+R", body):
                nb = objects.get((int(r.group(1)), int(r.group(2))))
                if nb:
                    queue.append(nb)
    return None


def parse_ttf_cmap(data: bytes) -> dict:
    if len(data) < 12:
        return {}
    num_tables = struct.unpack('>H', data[4:6])[0]
    tbls = {}
    off = 12
    for _ in range(num_tables):
        if off + 16 > len(data):
            break
        tag = data[off:off+4].decode('ascii', errors='replace')
        tbl_offset = struct.unpack('>I', data[off+8:off+12])[0]
        tbls[tag] = tbl_offset
        off += 16
    if 'cmap' not in tbls:
        return {}
    cmap_off = tbls['cmap']
    if cmap_off + 4 > len(data):
        return {}
    cmap = data[cmap_off:]
    num_sub = struct.unpack('>H', cmap[2:4])[0]
    mappings = {}
    off = 4
    for _ in range(num_sub):
        if off + 8 > len(cmap):
            break
        plat, enc = struct.unpack('>HH', cmap[off:off+4])
        sub_off = struct.unpack('>I', cmap[off+4:off+8])[0]
        off += 8
        if sub_off + 2 > len(cmap):
            continue
        sub = cmap[sub_off:]
        if len(sub) < 2:
            continue
        fmt = struct.unpack('>H', sub[0:2])[0]
        if fmt == 4:
            if len(sub) < 14:
                continue
            seg_count = struct.unpack('>H', sub[6:8])[0] // 2
            if len(sub) < 14 + seg_count * 8:
                continue
            ends = [struct.unpack('>H', sub[14+i*2:16+i*2])[0] for i in range(seg_count)]
            so = 14 + seg_count * 2 + 2
            starts = [struct.unpack('>H', sub[so+i*2:so+2+i*2])[0] for i in range(seg_count)]
            do = so + seg_count * 2
            deltas = [struct.unpack('>h', sub[do+i*2:do+2+i*2])[0] for i in range(seg_count)]
            ro = do + seg_count * 2
            ranges = [struct.unpack('>H', sub[ro+i*2:ro+2+i*2])[0] for i in range(seg_count)]
            for j in range(seg_count):
                if starts[j] == 0xFFFF:
                    continue
                for c in range(starts[j], ends[j] + 1):
                    if ranges[j] == 0:
                        gid = (c + deltas[j]) & 0xFFFF
                    else:
                        idx = ranges[j] // 2 + (c - starts[j]) + j - seg_count
                        if ro + idx * 2 + 2 > len(sub):
                            continue
                        gid = struct.unpack('>H', sub[ro + idx*2:ro + idx*2 + 2])[0]
                        if gid:
                            gid = (gid + deltas[j]) & 0xFFFF
                    if gid:
                        mappings[c] = gid
        elif fmt == 6:
            if len(sub) < 10:
                continue
            first_code = struct.unpack('>H', sub[6:8])[0]
            entry_count = struct.unpack('>H', sub[8:10])[0]
            for j in range(entry_count):
                if 10 + j * 2 + 2 > len(sub):
                    break
                gid = struct.unpack('>H', sub[10+j*2:12+j*2])[0]
                if gid:
                    mappings[first_code + j] = gid
        elif fmt == 12:
            if len(sub) < 16:
                continue
            num_groups = struct.unpack('>I', sub[12:16])[0]
            go = 16
            for _ in range(num_groups):
                if go + 12 > len(sub):
                    break
                sc, ec, sg = struct.unpack('>III', sub[go:go+12])
                go += 12
                for c in range(sc, ec + 1):
                    mappings[c] = sg + (c - sc)
    return mappings


def parse_ttf_widths(data: bytes) -> tuple[dict, float]:
    """Extract per-codepoint advance widths from a TTF (cmap + hmtx).

    Returns ({unicode_char: advance_in_em_units}, units_per_em).
    Zero-width (combining marks) and missing chars are simply absent/0.
    """
    if len(data) < 12:
        return {}, 1000.0
    num_tables = struct.unpack(">H", data[4:6])[0]
    tbls = {}
    off = 12
    for _ in range(num_tables):
        if off + 16 > len(data):
            break
        tag = data[off:off+4].decode("ascii", errors="replace")
        tbls[tag] = struct.unpack(">I", data[off+8:off+12])[0]
        off += 16
    if "hmtx" not in tbls or "hhea" not in tbls or "maxp" not in tbls:
        return {}, 1000.0
    try:
        upem = struct.unpack(">H", data[tbls["head"] + 18:tbls["head"] + 20])[0] or 1000.0
        num_h = struct.unpack(">H", data[tbls["hhea"] + 34:tbls["hhea"] + 36])[0]
        # gid -> advance (hmtx: numberOfHMetrics longs, then same-width runt)
        gid_adv = []
        p = tbls["hmtx"]
        for i in range(num_h):
            if p + 4 > len(data):
                break
            gid_adv.append(struct.unpack(">H", data[p:p+2])[0])
            p += 4
        # map through the cmap we already parse; key by CHARACTER for fast
        # per-glyph lookup during layout (combining marks excluded: adv == 0)
        uni_to_gid = parse_ttf_cmap(data)
        adv = {}
        for uni, gid in uni_to_gid.items():
            if gid < len(gid_adv):
                a = gid_adv[gid]
                if a:
                    adv[chr(uni)] = a / upem
        return adv, upem
    except Exception:
        return {}, 1000.0


def collect_pages(objects: dict, page_num: int, gen: int = 0) -> list:
    d = objects.get((page_num, gen), b'')
    d_str = d.decode('latin-1', errors='replace')
    if re.search(r'/Type\s*/Page(?!s)', d_str):
        return [(page_num, gen)]
    if re.search(r'/Type\s*/Pages', d_str):
        pages = []
        km = re.search(rb'/Kids\s*\[(.*?)\]', d, re.DOTALL)
        if km:
            kids_text = km.group(1).decode('latin-1', errors='replace')
            for ref in re.findall(r'(\d+)\s+\d+\s+R', kids_text):
                pages.extend(collect_pages(objects, int(ref)))
        return pages
    return []


def find_root_pages(objects: dict) -> int | None:
    """Object number of the page-tree root.

    RESTRUCTURED: goes trailer -> /Root -> /Pages (two object reads) instead
    of scanning every object for /Catalog. Scan kept as a fallback.
    """
    root_ref = getattr(objects, "root_ref", lambda: None)()
    if root_ref is not None:
        cat = objects.get((root_ref, 0), b'').decode('latin-1', errors='replace')
        pm = re.search(r'/Pages\s+(\d+)\s+\d+\s+R', cat)
        if pm:
            return int(pm.group(1))

    for (n, g), d in objects.items():
        d_str = d.decode('latin-1', errors='replace')
        if re.search(r'/Type\s*/Catalog', d_str):
            pm = re.search(r'/Pages\s+(\d+)\s+\d+\s+R', d_str)
            if pm:
                return int(pm.group(1))
    return None


def get_page_count(objects: dict, pages_obj: int | None = None) -> int:
    """Page count from the page-tree root's /Count — no page walk.

    NEW: on a flat Word-exported tree /Count is exactly len(/Kids), so this is
    authoritative and costs one object read.
    """
    if pages_obj is None:
        pages_obj = find_root_pages(objects)
    if not pages_obj:
        return 0
    d = objects.get((pages_obj, 0), b'')
    m = re.search(rb'/Count\s+(\d+)', d)
    return int(m.group(1)) if m else 0


def iter_page_refs(objects: dict, pages_obj: int | None = None):
    """Yield (page_num, gen) per page, lazily, walking /Kids.

    NEW: streaming counterpart to collect_pages(). collect_pages() builds the
    whole list up front; this yields one page at a time so the caller can
    process and release it.
    """
    if pages_obj is None:
        pages_obj = find_root_pages(objects)
    if not pages_obj:
        return
    stack = [(pages_obj, 0)]
    seen = set()
    while stack:
        num, gen = stack.pop(0)
        if (num, gen) in seen:
            continue
        seen.add((num, gen))
        d = objects.get((num, gen), b'')
        if not d:
            continue
        d_str = d.decode('latin-1', errors='replace')
        if re.search(r'/Type\s*/Page(?!s)', d_str):
            yield (num, gen)
            continue
        km = re.search(rb'/Kids\s*\[(.*?)\]', d, re.DOTALL)
        if not km:
            continue
        kids_text = km.group(1).decode('latin-1', errors='replace')
        kids = [(int(r), 0) for r in re.findall(r'(\d+)\s+\d+\s+R', kids_text)]
        stack = kids + stack


def _lookup_font_family(font_ref: str, metadata: dict | None) -> str:
    """Resolve a content-stream font resource name (F1) to a family (Zawgyi-One)."""
    if not font_ref:
        return "Unknown"
    font_map = (metadata or {}).get("font_map") or {}
    candidates = [font_ref, font_ref.lstrip("/")]
    if font_ref and not font_ref.startswith("/"):
        candidates.append("/" + font_ref)
    for key in candidates:
        hit = font_map.get(key)
        if hit:
            return hit.get("family") or font_ref
    return font_ref


# ── Raw-object resolution (no pdfminer needed) ──────────────────────────────
# Page dicts are tiny, so scanning them with regexes is cheap. This lets the
# parser resolve /Resources → /Font → /BaseFont and /MediaBox per page directly
# from `objects`, instead of needing a pdfminer walk over the whole page tree.

_NAME_RE = re.compile(rb"/([A-Za-z0-9_+.\-]+)")
_NUM_RE = re.compile(rb"-?\d+(?:\.\d+)?")
_HEX_ESCAPE_RE = re.compile(r"#([0-9A-Fa-f]{2})")


def _unescape_name(b: bytes) -> str:
    """Decode PDF name hex escapes: b'Times#20New#20Roman' -> 'Times New Roman'."""
    return _HEX_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), b.decode("latin-1"))


def _page_resources(objects: dict, page_num: int, page_gen: int) -> bytes | None:
    """Resolve /Resources for a page, walking /Parent while it is inherited
    (some producers put /Resources on intermediate /Pages nodes)."""
    n, g = page_num, page_gen
    seen = set()
    for _ in range(8):  # max tree depth guard
        key = (n, g)
        if key in seen:
            return None
        seen.add(key)
        d = objects.get(key)
        if not d:
            return None
        res = _resolve_indirect(objects, d, b"Resources")
        if res:
            return res
        par = re.search(rb"/Parent\s+(\d+)\s+(\d+)\s+R", d)
        if not par:
            return None
        n, g = int(par.group(1)), int(par.group(2))
    return None


def _resolve_indirect(objects: dict, d: bytes, key: bytes) -> bytes | None:
    """Resolve /Key to an object body, following one level of indirection."""
    if not d:
        return None
    m = re.search(rb"/" + key + rb"\b", d)
    if not m:
        return None
    after = d[m.end():m.end() + 64]
    ref = re.match(rb"\s+(\d+)\s+(\d+)\s+R", after)
    if ref:
        return objects.get((int(ref.group(1)), int(ref.group(2))))
    dd = re.match(rb"\s*<<", after)
    if dd:
        start = m.end() + dd.end()
        depth = 0
        i = start - 2
        while i < len(d) - 1:
            if d[i:i+2] == b"<<":
                depth += 1
                i += 2
            elif d[i:i+2] == b">>":
                depth -= 1
                i += 2
                if depth == 0:
                    return d[start-2:i]
            else:
                i += 1
        return d[start-2:]
    return None


def resolve_page_fonts(objects: dict, page_num: int, page_gen: int) -> dict:
    """Build a font_map for one page straight from raw objects.

    /Resources is followed through /Parent while inherited. Font names are
    hex-unescaped (Times#20New#20Roman -> Times New Roman).

    Returns {"font_map": {...}, "mediabox": [x0,y0,x1,y1] | None}.
    """
    out = {"font_map": {}, "mediabox": None}

    resources = _page_resources(objects, page_num, page_gen)
    fonts = _resolve_indirect(objects, resources, b"Font") if resources else None
    if not fonts:
        return out

    # Iterate font entries: /F1 12 0 R  or  /F1 << ... >>
    for m in _NAME_RE.finditer(fonts):
        name = _unescape_name(m.group(1))
        after = fonts[m.end():m.end() + 96]
        ref = re.match(rb"\s+(\d+)\s+(\d+)\s+R", after)
        fobj = None
        if ref:
            fobj = objects.get((int(ref.group(1)), int(ref.group(2))))
        elif after.lstrip()[:2] == b"<<":
            start = m.end() + len(after) - len(after.lstrip())
            depth, i = 0, start
            while i < len(fonts) - 1:
                if fonts[i:i+2] == b"<<":
                    depth += 1; i += 2
                elif fonts[i:i+2] == b">>":
                    depth -= 1; i += 2
                    if depth == 0:
                        fobj = fonts[start:i]
                        break
                else:
                    i += 1
        if not fobj:
            continue
        base = re.search(rb"/BaseFont\s*/([^\s/<>\[\]()]+)", fobj)
        sub = re.search(rb"/Subtype\s*/([A-Za-z0-9]+)", fobj)
        enc = re.search(rb"/Encoding\s*(?:/([A-Za-z0-9\-]+)|(?:\d+)\s+\d+\s+R)", fobj)
        full_name = _unescape_name(base.group(1)) if base else name
        family = full_name.split("+")[-1]
        entry = {
            "family": family,
            "full_name": full_name,
            "size": 12,
            "subtype": sub.group(1).decode("latin-1") if sub else "Unknown",
            "encoding": (enc.group(1) or b"Custom").decode("latin-1") if enc else "Unknown",
        }
        keys = {name, "/" + name, family, full_name}
        for k in keys:
            if k:
                out["font_map"][k] = entry
    return out


def parse_mediabox(objects: dict, page_num: int, page_gen: int) -> list[float] | None:
    """MediaBox of a page: from the page dict, walking /Parent if needed."""
    n, g = page_num, page_gen
    for _ in range(8):  # max tree depth guard
        d = objects.get((n, g))
        if not d:
            return None
        mb = re.search(rb"/MediaBox\s*\[([^\]]+)\]", d)
        if mb:
            vals = [float(x) for x in _NUM_RE.findall(mb.group(1))][:4]
            if len(vals) == 4:
                return vals
        par = re.search(rb"/Parent\s+(\d+)\s+(\d+)\s+R", d)
        if not par:
            return None
        n, g = int(par.group(1)), int(par.group(2))
    return None


# Myanmar cluster guards for gap detection. PDF producers split a visual line
# into several positioned runs — sometimes INSIDE a syllable (the Zawgyi
# e-vowel \u1031 is drawn left of its consonant, marks/medials stack around
# the base). A space inserted mid-cluster permanently breaks Zawgyi→Unicode
# conversion (the ေ-move rule can't match across the space), so only insert
# one when both sides can legally start/end a syllable.
_MYA_SAFE_START = frozenset(
    # consonants + independent vowels + digits + section signs
    "\u1000\u1001\u1002\u1003\u1004\u1005\u1006\u1007\u1008\u1009"
    "\u100A\u100B\u100C\u100D\u100E\u100F\u1010\u1011\u1012\u1013"
    "\u1014\u1015\u1016\u1017\u1018\u1019\u101A\u101B\u101C\u101D"
    "\u101E\u101F\u1020\u1021"
    "\u1023\u1024\u1025\u1026\u1027\u1028\u1029\u102A"
    "\u1040\u1041\u1042\u1043\u1044\u1045\u1046\u1047\u1048\u1049"
    "\u104A\u104B\u104C\u104D\u104E\u104F"
    "\u1050\u1051\u1052\u1053\u1054\u1055"
)
# chars that always expect a continuation (never end a word)
_MYA_NO_END = frozenset("\u1031\u1039\u103B\u103C\u103D\u103E")


def _word_gap(prev_text: str, next_text: str) -> bool:
    """True if a space may be inserted between two runs without ever
    splitting a Myanmar syllable cluster."""
    if not prev_text or not next_text:
        return False
    p, n = prev_text[-1], next_text[0]
    in_mya = ("\u1000" <= p <= "\u109F") or ("\u1000" <= n <= "\u109F")
    if not in_mya:
        return True          # latin/digits: plain word boundary
    if p in _MYA_NO_END:     # e-vowel / medials / stacker await their base
        return False
    return n in _MYA_SAFE_START


def extract_page_text_layout(objects: dict, page_num: int, page_gen: int, gid_to_uni: dict, metadata: dict = None, widths: dict = None) -> dict:
    pd = objects.get((page_num, page_gen), b'')
    pd_str = pd.decode('latin-1', errors='replace')
    streams = []
    cm = re.search(r'/Contents\s+(\d+)\s+\d+\s+R', pd_str)
    if cm:
        streams = [int(cm.group(1))]
    else:
        ca = re.search(r'/Contents\s*\[(.*?)\]', pd_str, re.DOTALL)
        if ca:
            streams = [int(x) for x in re.findall(r'(\d+)\s+\d+\s+R', ca.group(1))]

    pieces = []
    current_font = "Unknown"
    current_size = 12.0
    tf_re = re.compile(r'/([A-Za-z0-9_+-]+)\s+([\d.]+)\s+Tf')
    for sn in streams:
        s = get_stream(objects, sn)
        if not s:
            continue
        content = s.decode('latin-1', errors='replace')
        # Walk outside-BT operators and BT/ET blocks so /Tf before BT is not missed.
        parts = re.split(r'(BT\s+.*?\s+ET)', content, flags=re.DOTALL)
        for part in parts:
            is_bt = part.lstrip().startswith('BT')
            if not is_bt:
                for tf in tf_re.finditer(part):
                    current_font = _lookup_font_family(tf.group(1), metadata)
                    current_size = float(tf.group(2))
                continue
            block_m = re.match(r'BT\s+(.*?)\s+ET', part, re.DOTALL)
            block = block_m.group(1) if block_m else part
            tf_match = None
            for tf_match in tf_re.finditer(block):
                pass
            if tf_match:
                current_font = _lookup_font_family(tf_match.group(1), metadata)
                current_size = float(tf_match.group(2))
            font_size = current_size
            font_name = current_font

            tm_match = re.search(r'([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+Tm', block)
            if not tm_match:
                continue
            x = float(tm_match.group(5))
            y = float(tm_match.group(6))

            block_texts = []

            for tj in re.finditer(r'\[(.*?)\]\s*TJ', block):
                arr = tj.group(1)
                i = 0
                arr_text = ""
                while i < len(arr):
                    while i < len(arr) and arr[i] in ' \t\n\r':
                        i += 1
                    if i >= len(arr):
                        break
                    if arr[i] == '<':
                        j = arr.find('>', i)
                        if j == -1:
                            break
                        hex_str = arr[i+1:j]
                        for k in range(0, len(hex_str), 4):
                            cid_hex = hex_str[k:k+4]
                            if len(cid_hex) < 4:
                                continue
                            cid = int(cid_hex, 16)
                            uni = gid_to_uni.get(cid)
                            if uni:
                                arr_text += chr(uni)
                            else:
                                arr_text += f"[{cid_hex}]"
                        i = j + 1
                    elif arr[i] == '(':
                        depth = 1
                        j = i + 1
                        while j < len(arr) and depth > 0:
                            if arr[j] == '\\' and j + 1 < len(arr):
                                j += 2
                                continue
                            if arr[j] == '(':
                                depth += 1
                            elif arr[j] == ')':
                                depth -= 1
                            j += 1
                        s_text = arr[i+1:j-1]
                        s_text = s_text.replace('\\n', '\n').replace('\\r', '\r').replace('\\t', '\t')
                        s_text = s_text.replace('\\(', '(').replace('\\)', ')').replace('\\\\', '\\')
                        arr_text += s_text
                        i = j
                    elif arr[i] == '-':
                        j = i + 1
                        while j < len(arr) and (arr[j].isdigit() or arr[j] == '.'):
                            j += 1
                        i = j
                    else:
                        j = i
                        while j < len(arr) and (arr[j].isdigit() or arr[j] == '.'):
                            j += 1
                        i = j
                block_texts.append(arr_text)

            for tj in re.finditer(r'<([0-9A-Fa-f]+)>\s*Tj', block):
                hex_str = tj.group(1)
                t = ""
                for k in range(0, len(hex_str), 4):
                    cid_hex = hex_str[k:k+4]
                    if len(cid_hex) < 4:
                        continue
                    cid = int(cid_hex, 16)
                    uni = gid_to_uni.get(cid)
                    if uni:
                        t += chr(uni)
                    else:
                        t += f"[{cid_hex}]"
                block_texts.append(t)

            for tj in re.finditer(r'\((.*?)\)\s*Tj', block):
                s_text = tj.group(1)
                s_text = s_text.replace('\\n', '\n').replace('\\r', '\r').replace('\\t', '\t')
                s_text = s_text.replace('\\(', '(').replace('\\)', ')').replace('\\\\', '\\')
                block_texts.append(s_text)

            if block_texts:
                pieces.append((y, x, ''.join(block_texts), font_size, font_name))

    if not pieces:
        return {"text": "", "lines": []}

    pieces.sort(key=lambda p: (-p[0], p[1]))
    # Advance-width table for exact run widths (falls back to ~0.53em/char).
    adv = widths if widths else {}
    adv_get = adv.get

    def _run_width(text, size):
        w = 0.0
        for c in text:
            a = adv_get(c)
            w += a if a is not None else 0.53
        return w * size

    lines_out = []
    current_y = pieces[0][0]
    current_size = pieces[0][3] or 12
    current_line = []  # (x, text, size, font)

    def _flush(line_pieces, y):
        if not line_pieces:
            return
        line_pieces = sorted(line_pieces, key=lambda r: r[0])
        merged = []
        for x, text, size, font in line_pieces:
            if merged and merged[-1]["font"] == font and merged[-1]["size"] == size:
                # Gap detection: a PDF line is often split into several text
                # operators at different x. Without this the words jam together.
                prev = merged[-1]
                prev_size = prev["size"] or size or 12
                prev_end = prev["x"] + prev["w"]
                gap = x - prev_end
                if (gap > 0.3 * prev_size
                        and prev["text"] and not prev["text"][-1].isspace()
                        and text and not text[0].isspace()
                        and _word_gap(prev["text"], text)):
                    prev["text"] += "\t" if gap >= 3 * prev_size else " "
                    prev["w"] += gap  # the inserted space spans the real gap
                prev["text"] += text
                prev["w"] += _run_width(text, prev_size)
            else:
                merged.append({"x": x, "text": text, "size": size, "font": font,
                               "w": _run_width(text, size)})
        lines_out.append({
            "text": "".join(r["text"] for r in merged),
            "x": line_pieces[0][0],
            "y": y,
            "size": merged[0]["size"] if merged else 12,
            "font": merged[0]["font"] if merged else "Unknown",
            "right": max(r["x"] + r["w"] for r in merged),
            "runs": merged,
        })

    for y, x, text, size, font in pieces:
        y_tol = max(3.0, (size or current_size or 12) * 0.2)
        if abs(y - current_y) > y_tol:
            _flush(current_line, current_y)
            current_line = []
            current_y = y
            current_size = size
        current_line.append((x, text, size, font))
    _flush(current_line, current_y)

    return {
        "text": "\n".join(ln["text"] for ln in lines_out),
        "lines": lines_out,
    }


# ---------------------------------------------------------------------------
# Linear, single-page pipeline
#
# RESTRUCTURED: extract_pdf() used to be one monolithic function that walked
# every page, accumulating raw_texts[] and page_layouts[] for the whole
# document before anything downstream ran. It is now a thin driver over small
# functions, each doing exactly one job for exactly one page:
#
#   open_document()      -> once: object store + font tables + page tree
#   read_page_metadata() -> one page: fonts + mediabox
#   read_page_text()     -> one page: text + layout lines
#   extract_one_page()   -> the two above, composed
#   iter_pdf_pages()     -> generator: yields one finished page at a time
#   extract_pdf()        -> same name/signature/return as before, built on
#                           iter_pdf_pages() so existing callers still work
# ---------------------------------------------------------------------------


class PdfDocument:
    """Everything that is genuinely document-wide, resolved once."""

    def __init__(self, objects, meta, gid_to_uni, uni_adv, pages_obj, page_count):
        self.objects = objects
        self.meta = meta
        self.gid_to_uni = gid_to_uni
        self.uni_adv = uni_adv
        self.pages_obj = pages_obj
        self.page_count = page_count
        self.font_map: dict = {}   # grows as pages are visited
        self.mediabox = None       # first page's box

    def page_refs(self):
        return iter_page_refs(self.objects, self.pages_obj)


def open_document(pdf_bytes: bytes, metadata: dict = None) -> PdfDocument:
    """Resolve the document-level context once: objects, /Info, font tables.

    Reads only the trailer, the catalog, the page-tree root and the embedded
    font program — not the page contents.
    """
    objects = parse_pdf_objects(pdf_bytes)

    meta = extract_metadata(objects)
    if metadata:
        meta.update(metadata.get("info", {}))

    pages_obj = find_root_pages(objects)
    if not pages_obj:
        raise ValueError("No page tree found")

    page_count = get_page_count(objects, pages_obj)

    first_page = None
    for ref in iter_page_refs(objects, pages_obj):
        first_page = ref
        break

    ff2_ref = find_font_file2(objects, first_page)
    if not ff2_ref:
        raise ValueError("No embedded FontFile2 found")

    ttf = get_stream(objects, *ff2_ref)
    if not ttf:
        raise ValueError("Failed to extract font stream")

    cmap_uni_to_gid = parse_ttf_cmap(ttf)
    gid_to_uni = {gid: uni for uni, gid in cmap_uni_to_gid.items() if gid}
    try:
        uni_adv, _upem = parse_ttf_widths(ttf)
    except Exception:
        uni_adv = {}
    del ttf, cmap_uni_to_gid

    return PdfDocument(objects, meta, gid_to_uni, uni_adv, pages_obj, page_count)


def read_page_metadata(doc: PdfDocument, page_ref, page_md: dict = None,
                       metadata: dict = None) -> dict:
    """Per-page structure: font_map + mediabox, straight from raw objects."""
    pnum, pgen = page_ref
    raw_fonts = resolve_page_fonts(doc.objects, pnum, pgen)

    if doc.mediabox is None:
        doc.mediabox = parse_mediabox(doc.objects, pnum, pgen)
    for k, v in raw_fonts["font_map"].items():
        doc.font_map.setdefault(k, v)

    local_meta = metadata
    if raw_fonts["font_map"] and metadata is not None:
        local_meta = dict(metadata)
        local_meta["font_map"] = raw_fonts["font_map"]
    elif page_md and metadata is not None:
        local_map = dict(metadata.get("font_map") or {})
        for f in page_md.get("fonts") or []:
            ref = str(f.get("ref") or "").lstrip("/")
            if not ref:
                continue
            entry = {
                "family": f.get("family") or ref,
                "full_name": f.get("name") or ref,
                "size": 12,
                "subtype": f.get("subtype"),
                "encoding": f.get("encoding"),
            }
            local_map[ref] = entry
            local_map["/" + ref] = entry
        local_meta = dict(metadata)
        local_meta["font_map"] = local_map

    return {
        "font_map": raw_fonts["font_map"],
        "mediabox": raw_fonts.get("mediabox"),
        "local_meta": local_meta,
    }


def read_page_text(doc: PdfDocument, page_ref, local_meta: dict = None) -> dict:
    """Per-page text + layout lines."""
    pnum, pgen = page_ref
    extracted = extract_page_text_layout(
        doc.objects, pnum, pgen, doc.gid_to_uni, local_meta, doc.uni_adv)
    if isinstance(extracted, dict):
        return {"text": extracted.get("text", ""), "lines": extracted.get("lines", [])}
    return {"text": extracted or "", "lines": []}


def extract_one_page(doc: PdfDocument, page_ref, index: int,
                     page_md: dict = None, metadata: dict = None) -> dict:
    """Complete result for ONE page: metadata, text and layout."""
    pm = read_page_metadata(doc, page_ref, page_md, metadata)
    body = read_page_text(doc, page_ref, pm["local_meta"])

    layout = {"page_num": index + 1, "lines": body["lines"]}
    mb = pm["mediabox"] or (page_md or {}).get("mediabox")
    if mb:
        layout["mediabox"] = mb
    if page_md:
        layout["fonts"] = page_md.get("fonts")

    return {
        "index": index,
        "text": body["text"],
        "layout": layout,
        "font_map": pm["font_map"],
    }


def iter_pdf_pages(pdf_bytes: bytes, metadata: dict = None, on_progress=None,
                   doc: PdfDocument = None):
    """Yield one fully-processed page at a time. Nothing is accumulated here.

    This is the streaming entry point: the caller decides whether to keep a
    page or write it straight out and drop it.
    """
    if doc is None:
        doc = open_document(pdf_bytes, metadata)
    md_pages = (metadata or {}).get("pages") or []
    total = doc.page_count

    for idx, page_ref in enumerate(doc.page_refs()):
        page_md = md_pages[idx] if idx < len(md_pages) else None
        page = extract_one_page(doc, page_ref, idx, page_md, metadata)
        page["total"] = total
        yield page
        if on_progress:
            on_progress({"done": idx + 1, "total": total})


def page_size_from_mediabox(mediabox) -> dict | None:
    """Convert a mediabox to the writer's page_size dict."""
    if not mediabox:
        return None
    return {
        "width": mediabox[2] - mediabox[0],
        "height": mediabox[3] - mediabox[1],
        "unit": "pt",
    }


def extract_pdf(pdf_bytes: bytes, metadata: dict = None, on_progress=None) -> dict:
    """
    Extract Burmese text from PDF bytes.

    Same name, signature and return shape as before — now a thin linear
    driver over iter_pdf_pages(). Use iter_pdf_pages() directly to stream
    without holding the whole document in memory.

    Parameters
    ----------
    pdf_bytes : bytes
        Raw PDF file bytes.
    metadata : dict | None
        Optional metadata to guide extraction.
    on_progress : callable
        Called with {"done": int, "total": int} per page.
    """
    doc = open_document(pdf_bytes, metadata)

    raw_texts = []
    page_layouts = []
    for page in iter_pdf_pages(pdf_bytes, metadata, on_progress, doc=doc):
        raw_texts.append(page["text"])
        page_layouts.append(page["layout"])

    return {
        "metadata": doc.meta,
        "pages": raw_texts,
        "pageCount": doc.page_count or len(raw_texts),
        "totalCharacters": sum(len(t) for t in raw_texts),
        "page_layouts": page_layouts,
        "page_size": page_size_from_mediabox(doc.mediabox),
        "font_map": doc.font_map,
    }
