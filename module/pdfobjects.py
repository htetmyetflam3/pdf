"""
Description: Lazy, xref-backed PDF object access.
Reads: raw PDF bytes.
Processes: trailer -> startxref -> xref subsection headers. Object bodies are
           sliced on demand, never all at once.
Outputs: PdfObjectStore, a read-only mapping of (objnum, gen) -> object bytes.

Why this exists
---------------
The previous `parse_pdf_objects()` regex-scanned the whole file and built a
dict holding every object body in RAM (~76 MB for a 33 MB / 14k-page file,
~2.1 s). Nothing downstream needs all objects at once: the pipeline walks one
page at a time. This store keeps the same mapping protocol (`.get`, `.items`,
`in`, `[]`) so every existing consumer works unchanged, but resolves an object
only when it is actually asked for.

Classic xref tables use fixed 20-byte records, so a single object's offset is
pure arithmetic plus one slice. Falls back to a full scan only for damaged
files or xref streams.
"""

from __future__ import annotations

import re

_OBJ_HEADER = re.compile(rb"\s*(\d+)\s+(\d+)\s+obj")
_XREF_ENTRY = re.compile(rb"(\d{10})\s(\d{5})\s([nf])")
_OBJ_PAT = re.compile(rb"(\d+)\s+(\d+)\s+obj")


class PdfObjectStore:
    """Mapping-like lazy view over the indirect objects in a PDF."""

    def __init__(self, raw: bytes, cache_size: int = 4096):
        self._raw = raw
        self._cache: dict[tuple[int, int], bytes] = {}
        self._cache_size = cache_size
        self._subs: list[tuple[int, int, int]] = []
        self._trailer: dict = {}
        self._scan_index: dict[tuple[int, int], tuple[int, int]] | None = None
        self.mode = "xref"
        try:
            self._load_xref()
            if not self._subs:
                raise ValueError("no xref subsections")
        except Exception:
            self.mode = "scan"
            self._build_scan_index()

    # -- construction -----------------------------------------------------

    def _load_xref(self) -> None:
        raw = self._raw
        tail = raw[-2048:] if len(raw) > 2048 else raw
        m = None
        for m in re.finditer(rb"startxref\s+(\d+)", tail):
            pass
        if not m:
            raise ValueError("no startxref")
        offset = int(m.group(1))

        if not raw[offset : offset + 32].lstrip().startswith(b"xref"):
            raise ValueError("not a classic xref table")

        pos = raw.index(b"xref", offset, offset + 32) + 4
        while True:
            hm = re.match(rb"\s*(\d+)\s+(\d+)\s*", raw[pos : pos + 64])
            if not hm:
                ti = raw.find(b"trailer", pos, pos + 4096)
                if ti != -1:
                    self._trailer = _parse_trailer(raw[ti : ti + 4096])
                break
            first, count = int(hm.group(1)), int(hm.group(2))
            entries_start = pos + hm.end()
            self._subs.append((first, count, entries_start))
            pos = entries_start + count * 20

    def _build_scan_index(self) -> None:
        """Damaged / xref-stream file: index object *offsets* only (not bodies)."""
        raw = self._raw
        self._scan_index = {}
        for m in _OBJ_PAT.finditer(raw):
            self._scan_index[(int(m.group(1)), int(m.group(2)))] = (m.end(), 0)
        ti = raw.rfind(b"trailer")
        if ti != -1:
            self._trailer = _parse_trailer(raw[ti : ti + 4096])

    # -- offsets ----------------------------------------------------------

    def _entry_offset(self, num: int) -> int | None:
        for first, count, start in self._subs:
            if first <= num < first + count:
                rec = self._raw[start + (num - first) * 20 :][:20]
                em = _XREF_ENTRY.search(rec)
                if em and em.group(3) == b"n":
                    return int(em.group(1))
                return None
        return None

    # -- mapping protocol -------------------------------------------------

    def get(self, key, default=None):
        try:
            num, gen = key
        except (TypeError, ValueError):
            return default
        hit = self._cache.get((num, gen))
        if hit is not None:
            return hit

        body = None
        if self._scan_index is not None:
            found = self._scan_index.get((num, gen))
            if found is None and gen == 0:
                for (n2, g2), v in self._scan_index.items():
                    if n2 == num:
                        found = v
                        break
            if found is not None:
                body = self._slice_from(found[0])
        else:
            off = self._entry_offset(num)
            if off is not None:
                body = self._body_at(off, num)

        if body is None:
            return default
        if len(self._cache) >= self._cache_size:
            self._cache.clear()
        self._cache[(num, gen)] = body
        return body

    def _body_at(self, off: int, expect: int | None = None) -> bytes | None:
        hm = _OBJ_HEADER.match(self._raw, off)
        if not hm:
            near = self._raw.find(b"obj", off, off + 64)
            if near == -1:
                return None
            return self._slice_from(near + 3)
        if expect is not None and int(hm.group(1)) != expect:
            return None
        return self._slice_from(hm.end())

    def _slice_from(self, start: int) -> bytes:
        end = self._raw.find(b"endobj", start)
        if end == -1:
            end = len(self._raw)
        return self._raw[start:end].strip()

    def __getitem__(self, key):
        v = self.get(key)
        if v is None:
            raise KeyError(key)
        return v

    def __contains__(self, key) -> bool:
        return self.get(key) is not None

    def keys(self):
        if self._scan_index is not None:
            yield from self._scan_index.keys()
            return
        for first, count, _ in self._subs:
            for num in range(first, first + count):
                if self._entry_offset(num) is not None:
                    yield (num, 0)

    def items(self):
        """Lazy — bodies are sliced as the caller consumes the generator."""
        for key in self.keys():
            body = self.get(key)
            if body is not None:
                yield key, body

    def __iter__(self):
        return self.keys()

    def __len__(self) -> int:
        if self._scan_index is not None:
            return len(self._scan_index)
        return sum(count for _, count, _ in self._subs)

    # -- trailer helpers ---------------------------------------------------

    @property
    def trailer(self) -> dict:
        return self._trailer

    def root_ref(self) -> int | None:
        return self._trailer.get("Root")

    def info_ref(self) -> int | None:
        return self._trailer.get("Info")


def _parse_trailer(blob: bytes) -> dict:
    out: dict = {}
    for key in (b"Root", b"Info"):
        m = re.search(rb"/" + key + rb"\s+(\d+)\s+(\d+)\s+R", blob)
        if m:
            out[key.decode()] = int(m.group(1))
    m = re.search(rb"/Size\s+(\d+)", blob)
    if m:
        out["Size"] = int(m.group(1))
    m = re.search(rb"/Prev\s+(\d+)", blob)
    if m:
        out["Prev"] = int(m.group(1))
    return out
