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


def parse_pdf_objects(raw: bytes) -> dict:
    obj_pat = re.compile(rb'(\d+)\s+(\d+)\s+obj')
    objects = {}
    for m in obj_pat.finditer(raw):
        n, g = int(m.group(1)), int(m.group(2))
        start = m.end()
        end = raw.find(b'endobj', start)
        if end == -1:
            continue
        objects[(n, g)] = raw[start:end].strip()
    return objects


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


def extract_metadata(objects: dict) -> dict:
    metadata = {}
    for (n, g), d in objects.items():
        d_str = d.decode('latin-1', errors='replace')
        if re.search(r'/Type\s*/Catalog', d_str):
            info_m = re.search(r'/Info\s+(\d+)\s+\d+\s+R', d_str)
            if info_m:
                info_n = int(info_m.group(1))
                info_d = objects.get((info_n, 0), b'').decode('latin-1', errors='replace')
                for kv in re.finditer(r'/([^\s/\[\]<>()]+)\s*\(([^)]*)\)', info_d):
                    metadata[kv.group(1)] = kv.group(2)
            break
    return metadata


def find_font_file2(objects: dict) -> tuple[int, int] | None:
    for (n, g), d in objects.items():
        d_str = d.decode('latin-1', errors='replace')
        if '/FontFile2' in d_str:
            m = re.search(r'/FontFile2\s+(\d+)\s+(\d+)\s+R', d_str)
            if m:
                return int(m.group(1)), int(m.group(2))
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
    for (n, g), d in objects.items():
        d_str = d.decode('latin-1', errors='replace')
        if re.search(r'/Type\s*/Catalog', d_str):
            pm = re.search(r'/Pages\s+(\d+)\s+\d+\s+R', d_str)
            if pm:
                return int(pm.group(1))
    return None


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


def extract_page_text_layout(objects: dict, page_num: int, page_gen: int, gid_to_uni: dict, metadata: dict = None) -> dict:
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
                merged[-1]["text"] += text
            else:
                merged.append({"x": x, "text": text, "size": size, "font": font})
        lines_out.append({
            "text": "".join(r["text"] for r in merged),
            "x": line_pieces[0][0],
            "y": y,
            "size": merged[0]["size"] if merged else 12,
            "font": merged[0]["font"] if merged else "Unknown",
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


def extract_pdf(pdf_bytes: bytes, metadata: dict = None, on_progress=None) -> dict:
    """
    Extract Burmese text from PDF bytes.
    Sequential extraction, memory-flat.
    
    Parameters
    ----------
    pdf_bytes : bytes
        Raw PDF file bytes.
    metadata : dict | None
        Optional metadata from pdfminer to guide extraction.
    on_progress : callable
        Called with {"done": int, "total": int} per page.
    """
    raw = pdf_bytes

    objects = parse_pdf_objects(raw)

    meta = extract_metadata(objects)
    if metadata:
        meta.update(metadata.get("info", {}))

    ff2_ref = find_font_file2(objects)
    if not ff2_ref:
        raise ValueError("No embedded FontFile2 found")

    ttf = get_stream(objects, *ff2_ref)
    if not ttf:
        raise ValueError("Failed to extract font stream")

    cmap_uni_to_gid = parse_ttf_cmap(ttf)
    gid_to_uni = {gid: uni for uni, gid in cmap_uni_to_gid.items() if gid}

    pages_obj = find_root_pages(objects)
    if not pages_obj:
        raise ValueError("No page tree found")

    all_pages = collect_pages(objects, pages_obj)
    total = len(all_pages)

    md_pages = (metadata or {}).get("pages") or []
    raw_texts = []
    page_layouts = []
    for idx, (pnum, pgen) in enumerate(all_pages):
        page_md = md_pages[idx] if idx < len(md_pages) else None
        local_meta = metadata
        if page_md and metadata is not None:
            # Prefer this page's /F1→family mapping over the document-wide last-write.
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
        extracted = extract_page_text_layout(objects, pnum, pgen, gid_to_uni, local_meta)
        txt = extracted.get("text", "") if isinstance(extracted, dict) else (extracted or "")
        lines = extracted.get("lines", []) if isinstance(extracted, dict) else []
        raw_texts.append(txt)
        layout = {
            "page_num": idx + 1,
            "lines": lines,
        }
        if page_md:
            layout["mediabox"] = page_md.get("mediabox")
            layout["fonts"] = page_md.get("fonts")
        page_layouts.append(layout)
        if on_progress:
            on_progress({"done": idx + 1, "total": total})

    return {
        "metadata": meta,
        "pages": raw_texts,
        "pageCount": total,
        "totalCharacters": sum(len(t) for t in raw_texts),
        "page_layouts": page_layouts,
    }
