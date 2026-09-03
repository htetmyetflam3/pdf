"""
Description: Get a PDF's page count (and page-object offsets) without parsing
the whole file.

Strategy: seek to EOF -> read `startxref` -> read only the xref section ->
resolve /Root -> /Pages -> /Count. Reads a few KB instead of tens of MB.

Falls back to a bounded scan for damaged / xref-stream PDFs.
"""

from __future__ import annotations

import re
import zlib


class PdfStructureError(Exception):
    pass


# ---------------------------------------------------------------------------
# Low-level helpers: read small windows out of a file object
# ---------------------------------------------------------------------------

def _read_at(fh, offset: int, length: int) -> bytes:
    fh.seek(offset)
    return fh.read(length)


def _file_size(fh) -> int:
    fh.seek(0, 2)
    return fh.tell()


def find_startxref(fh, tail: int = 2048) -> int:
    """Read the last `tail` bytes and return the startxref offset."""
    size = _file_size(fh)
    window = _read_at(fh, max(0, size - tail), min(tail, size))
    m = None
    for m in re.finditer(rb"startxref\s+(\d+)", window):
        pass  # keep the last one
    if not m:
        raise PdfStructureError("no startxref in trailer window")
    return int(m.group(1))


def _parse_trailer_dict(blob: bytes) -> dict:
    """Pull /Root and /Prev out of a trailer (or xref-stream) dict blob."""
    out = {}
    m = re.search(rb"/Root\s+(\d+)\s+(\d+)\s+R", blob)
    if m:
        out["Root"] = (int(m.group(1)), int(m.group(2)))
    m = re.search(rb"/Prev\s+(\d+)", blob)
    if m:
        out["Prev"] = int(m.group(1))
    m = re.search(rb"/Size\s+(\d+)", blob)
    if m:
        out["Size"] = int(m.group(1))
    return out


# ---------------------------------------------------------------------------
# Classic xref table
# ---------------------------------------------------------------------------

_XREF_ENTRY = re.compile(rb"(\d{10})\s(\d{5})\s([nf])")


def read_xref_table(fh, offset: int) -> tuple[dict, dict]:
    """
    Parse a classic `xref` table at `offset`.

    Returns (xref, trailer) where xref maps objnum -> byte offset.
    Reads only the xref section plus the trailer, not the whole file.
    """
    head = _read_at(fh, offset, 32)
    if not head.lstrip().startswith(b"xref"):
        raise PdfStructureError("not a classic xref table")

    xref: dict[int, int] = {}
    pos = offset + head.index(b"xref") + 4
    trailer_blob = b""

    while True:
        chunk = _read_at(fh, pos, 64)
        m = re.match(rb"\s*(\d+)\s+(\d+)\s*", chunk)
        if not m:
            # Should be the trailer keyword now.
            tchunk = _read_at(fh, pos, 4096)
            ti = tchunk.find(b"trailer")
            if ti != -1:
                trailer_blob = tchunk[ti:]
            break
        start_obj, count = int(m.group(1)), int(m.group(2))
        pos += m.end()
        # Each entry is exactly 20 bytes, but some writers use 19 or 20.
        span = count * 20 + 64
        body = _read_at(fh, pos, span)
        consumed = 0
        n = 0
        for em in _XREF_ENTRY.finditer(body):
            if n >= count:
                break
            if em.group(3) == b"n":
                xref[start_obj + n] = int(em.group(1))
            consumed = em.end()
            n += 1
        pos += consumed

    trailer = _parse_trailer_dict(trailer_blob)
    return xref, trailer


# ---------------------------------------------------------------------------
# Cross-reference streams (PDF 1.5+) — /Type /XRef
# ---------------------------------------------------------------------------

def read_xref_stream(fh, offset: int) -> tuple[dict, dict]:
    """Parse an xref *stream* object at `offset`. Reads only that object."""
    # Grab a generous window; xref streams are compact relative to the file.
    blob = _read_at(fh, offset, 1 << 20)
    dict_end = blob.find(b"stream")
    if dict_end == -1:
        raise PdfStructureError("no stream keyword in xref stream object")
    header = blob[:dict_end]

    if b"/XRef" not in header:
        raise PdfStructureError("object at startxref is not /Type /XRef")

    m = re.search(rb"/W\s*\[\s*(\d+)\s+(\d+)\s+(\d+)\s*\]", header)
    if not m:
        raise PdfStructureError("xref stream missing /W")
    w = [int(m.group(1)), int(m.group(2)), int(m.group(3))]

    size_m = re.search(rb"/Size\s+(\d+)", header)
    size = int(size_m.group(1)) if size_m else 0

    index_m = re.search(rb"/Index\s*\[([\d\s]+)\]", header)
    if index_m:
        nums = [int(x) for x in index_m.group(1).split()]
        index = list(zip(nums[0::2], nums[1::2]))
    else:
        index = [(0, size)]

    # Extract the stream payload.
    s = dict_end + len(b"stream")
    if blob[s : s + 2] == b"\r\n":
        s += 2
    elif blob[s : s + 1] in (b"\n", b"\r"):
        s += 1
    e = blob.find(b"endstream", s)
    if e == -1:
        # Need a bigger window.
        blob = _read_at(fh, offset, 1 << 24)
        e = blob.find(b"endstream", s)
        if e == -1:
            raise PdfStructureError("unterminated xref stream")
    data = blob[s:e]

    if b"/FlateDecode" in header:
        data = zlib.decompress(data)

    # /DecodeParms predictor handling (PNG predictors are common here).
    pm = re.search(rb"/Predictor\s+(\d+)", header)
    if pm and int(pm.group(1)) >= 10:
        cm = re.search(rb"/Columns\s+(\d+)", header)
        columns = int(cm.group(1)) if cm else 1
        data = _png_undo(data, columns)

    rowlen = sum(w)
    xref: dict[int, int] = {}
    compressed: dict[int, tuple[int, int]] = {}
    pos = 0

    def field(row: bytes, off: int, width: int, default: int) -> int:
        if width == 0:
            return default
        return int.from_bytes(row[off : off + width], "big")

    for first, count in index:
        for i in range(count):
            if pos + rowlen > len(data):
                break
            row = data[pos : pos + rowlen]
            pos += rowlen
            ftype = field(row, 0, w[0], 1)
            f2 = field(row, w[0], w[1], 0)
            f3 = field(row, w[0] + w[1], w[2], 0)
            objnum = first + i
            if ftype == 1:
                xref[objnum] = f2
            elif ftype == 2:
                compressed[objnum] = (f2, f3)  # (container objnum, index)

    trailer = _parse_trailer_dict(header)
    trailer["_compressed"] = compressed
    return xref, trailer


def _png_undo(data: bytes, columns: int) -> bytes:
    """Reverse PNG row predictors used by xref streams."""
    rowlen = columns + 1
    prev = bytearray(columns)
    out = bytearray()
    for i in range(0, len(data) - rowlen + 1, rowlen):
        ft = data[i]
        row = bytearray(data[i + 1 : i + rowlen])
        if ft == 2:  # Up — the only one xref streams realistically use
            for j in range(columns):
                row[j] = (row[j] + prev[j]) & 0xFF
        elif ft == 1:  # Sub
            for j in range(1, columns):
                row[j] = (row[j] + row[j - 1]) & 0xFF
        elif ft == 3:  # Average
            for j in range(columns):
                left = row[j - 1] if j else 0
                row[j] = (row[j] + ((left + prev[j]) >> 1)) & 0xFF
        elif ft == 4:  # Paeth
            for j in range(columns):
                a = row[j - 1] if j else 0
                b = prev[j]
                c = prev[j - 1] if j else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                row[j] = (row[j] + pr) & 0xFF
        out += row
        prev = row
    return bytes(out)


# ---------------------------------------------------------------------------
# Object fetch
# ---------------------------------------------------------------------------

def fetch_object(fh, xref: dict, objnum: int, window: int = 8192) -> bytes:
    """Read a single indirect object's body by seeking to its xref offset."""
    off = xref.get(objnum)
    if off is None:
        raise PdfStructureError(f"object {objnum} not in xref")
    blob = _read_at(fh, off, window)
    m = re.match(rb"\s*\d+\s+\d+\s+obj", blob)
    if not m:
        raise PdfStructureError(f"object {objnum} header mismatch at {off}")
    body = blob[m.end():]
    e = body.find(b"endobj")
    if e != -1:
        return body[:e]
    # Object bigger than the window — widen once.
    blob = _read_at(fh, off, window * 32)
    body = blob[blob.index(b"obj") + 3:]
    e = body.find(b"endobj")
    return body[:e] if e != -1 else body


def _ref(blob: bytes, key: bytes) -> tuple[int, int] | None:
    m = re.search(rb"/" + key + rb"\s+(\d+)\s+(\d+)\s+R", blob)
    return (int(m.group(1)), int(m.group(2))) if m else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _xref_subsections(fh, offset: int) -> tuple[list[tuple[int, int, int]], dict]:
    """
    Map a classic xref table's subsection headers WITHOUT reading the entries.

    Returns ([(first_obj, count, entries_start_offset)], trailer).
    Entries are fixed 20-byte records, so a single object's offset can be
    computed arithmetically and read with one 20-byte seek.
    """
    head = _read_at(fh, offset, 32)
    if not head.lstrip().startswith(b"xref"):
        raise PdfStructureError("not a classic xref table")

    subs: list[tuple[int, int, int]] = []
    pos = offset + head.index(b"xref") + 4
    trailer_blob = b""

    while True:
        chunk = _read_at(fh, pos, 64)
        m = re.match(rb"\s*(\d+)\s+(\d+)\s*", chunk)
        if not m:
            tchunk = _read_at(fh, pos, 4096)
            ti = tchunk.find(b"trailer")
            if ti != -1:
                trailer_blob = tchunk[ti:]
            break
        first, count = int(m.group(1)), int(m.group(2))
        entries_start = pos + m.end()
        subs.append((first, count, entries_start))
        # Skip the entries entirely: fixed 20 bytes each.
        pos = entries_start + count * 20
    return subs, _parse_trailer_dict(trailer_blob)


def _seek_entry(fh, subs, objnum: int) -> int | None:
    """Read the single 20-byte xref record for `objnum`."""
    for first, count, start in subs:
        if first <= objnum < first + count:
            rec = _read_at(fh, start + (objnum - first) * 20, 20)
            m = _XREF_ENTRY.search(rec)
            if m and m.group(3) == b"n":
                return int(m.group(1))
            return None
    return None


def get_page_count_fast(path: str) -> dict:
    """
    Page count reading only a handful of KB: trailer -> two 20-byte xref
    records -> catalog -> page-tree root /Count. Never reads the xref body.
    """
    with open(path, "rb") as fh:
        c = _CountingReader(fh)
        offset = find_startxref(c)
        subs, trailer = _xref_subsections(c, offset)
        root_ref = trailer.get("Root")
        if not root_ref:
            raise PdfStructureError("no /Root in trailer")

        off = _seek_entry(c, subs, root_ref[0])
        if off is None:
            raise PdfStructureError("root not in xref")
        catalog = fetch_object(c, {root_ref[0]: off}, root_ref[0])

        pages_ref = _ref(catalog, b"Pages")
        if not pages_ref:
            raise PdfStructureError("catalog has no /Pages")

        off = _seek_entry(c, subs, pages_ref[0])
        if off is None:
            raise PdfStructureError("page tree root not in xref")

        # /Pages holds a /Kids array with one ref per page — on a 14k-page file
        # that object is ~280 KB. /Count is in the same dict though, so read a
        # small window first and only widen if the key straddles the edge.
        m = None
        for win in (512, 4096, 1 << 20):
            pages = _read_at(c, off, win)
            m = re.search(rb"/Count\s+(\d+)", pages)
            if m:
                break
        if not m:
            raise PdfStructureError("page tree root has no /Count")
        return {
            "page_count": int(m.group(1)),
            "method": "seek-only",
            "bytes_read": c.bytes_read,
        }


def get_page_count(path: str) -> dict:
    """
    Return {"page_count": int, "method": str, "bytes_read": int}.

    Seeks EOF -> startxref -> xref -> /Root -> /Pages -> /Count.
    """
    try:
        return get_page_count_fast(path)
    except (PdfStructureError, OSError, ValueError):
        pass
    with open(path, "rb") as fh:
        counter = _CountingReader(fh)
        try:
            offset = find_startxref(counter)
            try:
                xref, trailer = read_xref_table(counter, offset)
                method = "xref-table"
            except PdfStructureError:
                xref, trailer = read_xref_stream(counter, offset)
                method = "xref-stream"

            # Hybrid / incremental files: follow /Prev chain for a full map.
            seen = {offset}
            prev = trailer.get("Prev")
            while prev and prev not in seen and len(seen) < 64:
                seen.add(prev)
                try:
                    x2, t2 = read_xref_table(counter, prev)
                except PdfStructureError:
                    try:
                        x2, t2 = read_xref_stream(counter, prev)
                    except PdfStructureError:
                        break
                for k, v in x2.items():
                    xref.setdefault(k, v)
                if "Root" not in trailer and "Root" in t2:
                    trailer["Root"] = t2["Root"]
                prev = t2.get("Prev")

            root_ref = trailer.get("Root")
            if not root_ref:
                raise PdfStructureError("no /Root in trailer")

            catalog = fetch_object(counter, xref, root_ref[0])
            pages_ref = _ref(catalog, b"Pages")
            if not pages_ref:
                raise PdfStructureError("catalog has no /Pages")

            pages = fetch_object(counter, xref, pages_ref[0])
            m = re.search(rb"/Count\s+(\d+)", pages)
            if not m:
                raise PdfStructureError("page tree root has no /Count")

            return {
                "page_count": int(m.group(1)),
                "method": method,
                "bytes_read": counter.bytes_read,
            }
        except PdfStructureError:
            return _fallback_scan(path, counter.bytes_read)


def _fallback_scan(path: str, already: int) -> dict:
    """Damaged PDF: stream the file in chunks counting /Type /Page."""
    pat = re.compile(rb"/Type\s*/Page[^s]")
    total = 0
    read = 0
    with open(path, "rb") as fh:
        carry = b""
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            read += len(chunk)
            buf = carry + chunk
            total += len(pat.findall(buf))
            carry = buf[-32:]
    return {
        "page_count": total,
        "method": "scan-fallback",
        "bytes_read": already + read,
    }


class _CountingReader:
    """Wraps a file object to track how many bytes we actually touched."""

    def __init__(self, fh):
        self._fh = fh
        self.bytes_read = 0

    def seek(self, *a):
        return self._fh.seek(*a)

    def tell(self):
        return self._fh.tell()

    def read(self, n=-1):
        data = self._fh.read(n)
        self.bytes_read += len(data)
        return data


def iter_page_refs(path: str):
    """
    Lazily yield (page_index, objnum, gennum, byte_offset) for each page by
    walking /Kids from the page-tree root — no whole-file parse.

    Pair with `read_page_object()` to pull exactly one page's bytes at a time,
    which is what a per-page streaming pipeline needs.
    """
    fh = open(path, "rb")
    c = _CountingReader(fh)
    try:
        offset = find_startxref(c)
        subs, trailer = _xref_subsections(c, offset)
        root_ref = trailer.get("Root")
        if not root_ref:
            raise PdfStructureError("no /Root in trailer")

        def obj_bytes(num: int, window: int = 4096) -> bytes:
            off = _seek_entry(c, subs, num)
            if off is None:
                raise PdfStructureError(f"object {num} not in xref")
            blob = _read_at(c, off, window)
            e = blob.find(b"endobj")
            if e == -1 and window < (1 << 22):
                blob = _read_at(c, off, window * 64)
                e = blob.find(b"endobj")
            return blob[:e] if e != -1 else blob

        catalog = obj_bytes(root_ref[0], 1024)
        pages_ref = _ref(catalog, b"Pages")
        if not pages_ref:
            raise PdfStructureError("catalog has no /Pages")

        kid_pat = re.compile(rb"(\d+)\s+(\d+)\s+R")
        idx = 0
        stack = [pages_ref[0]]
        seen = set()

        while stack:
            node_num = stack.pop(0)
            if node_num in seen:
                continue
            seen.add(node_num)
            node = obj_bytes(node_num)
            km = re.search(rb"/Kids\s*\[", node)
            if km is None:
                # Leaf: an actual /Page.
                off = _seek_entry(c, subs, node_num)
                yield idx, node_num, 0, off
                idx += 1
                continue
            depth, i = 1, km.end()
            while i < len(node) and depth:
                ch = node[i : i + 1]
                if ch == b"[":
                    depth += 1
                elif ch == b"]":
                    depth -= 1
                i += 1
            kids = [int(m.group(1)) for m in kid_pat.finditer(node[km.end() : i])]
            stack = kids + stack
    finally:
        fh.close()


def read_page_object(path: str, objnum: int, window: int = 4096) -> bytes:
    """Read a single page object's dict bytes, by object number."""
    with open(path, "rb") as fh:
        c = _CountingReader(fh)
        offset = find_startxref(c)
        subs, _ = _xref_subsections(c, offset)
        off = _seek_entry(c, subs, objnum)
        if off is None:
            raise PdfStructureError(f"object {objnum} not in xref")
        blob = _read_at(c, off, window)
        e = blob.find(b"endobj")
        return blob[:e] if e != -1 else blob


if __name__ == "__main__":
    import sys
    import time

    for p in sys.argv[1:]:
        t0 = time.perf_counter()
        r = get_page_count(p)
        dt = time.perf_counter() - t0
        print(f"{p}: {r['page_count']} pages  "
              f"[{r['method']}, {r['bytes_read']:,} bytes, {dt*1000:.1f} ms]")
