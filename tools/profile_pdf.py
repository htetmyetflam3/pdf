#!/usr/bin/env python3
"""
Stage-by-stage timing + optional cProfile for the extraction pipeline.

Answers "is there a hot loop or is it just slow?" on YOUR machine:

    python tools/profile_pdf.py input/pdf/c1-700.pdf --pages 300
    python tools/profile_pdf.py input/pdf/c1-700.pdf --pages 300 --cprofile

Extrapolates the measured per-page cost to the full page count.
"""

import argparse
import cProfile
import pstats
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", help="Input PDF path")
    ap.add_argument("--pages", type=int, default=200,
                    help="How many pages to benchmark (default 200)")
    ap.add_argument("--cprofile", action="store_true",
                    help="Run under cProfile and print the hot functions")
    ap.add_argument("--out", default="output/profile_bench.docx")
    args = ap.parse_args()

    from module.prase import parse_pdf_objects, collect_pages, find_root_pages

    raw = Path(args.pdf).read_bytes()
    print(f"[i] {args.pdf}: {len(raw):,} bytes")

    t0 = time.perf_counter()
    objects = parse_pdf_objects(raw)
    t_objects = time.perf_counter() - t0

    t0 = time.perf_counter()
    pages = collect_pages(objects, find_root_pages(objects))
    t_tree = time.perf_counter() - t0
    total_pages = len(pages)
    print(f"[i] document page count: {total_pages:,}")
    print(f"[i] parse_pdf_objects: {t_objects:.2f}s   page tree walk: {t_tree:.3f}s")
    if args.pages < total_pages:
        print(f"[i] benchmarking on the first {args.pages:,} pages ...")

    from module.main import run_pipeline
    orig_collect = collect_pages

    def limited(*a, **k):
        return orig_collect(*a, **k)[: args.pages]

    import module.prase as prase
    prase.collect_pages = limited

    t0 = time.perf_counter()
    if args.cprofile:
        pr = cProfile.Profile()
        pr.enable()
    res = run_pipeline(raw, args.out, pdf_name=Path(args.pdf).name,
                       on_progress=lambda s: print(f"\r    page {s['done']}/{s['total']}",
                                                   end="", flush=True)
                       if s["done"] % 25 == 0 else None)
    if args.cprofile:
        pr.disable()
    elapsed = time.perf_counter() - t0
    print()

    per_page = elapsed / args.pages
    print(f"[+] {args.pages:,} pages in {elapsed:.2f}s  ({per_page * 1000:.1f} ms/page)")
    if total_pages > args.pages:
        eta = per_page * total_pages
        print(f"[i] extrapolated full run ({total_pages:,} pages): "
              f"{eta / 60:.1f} min")
    print(f"[+] wrote {args.out}")

    if args.cprofile:
        print("\n─── top functions by cumulative time ───")
        st = pstats.Stats(pr)
        st.sort_stats("cumulative").print_stats(25)


if __name__ == "__main__":
    main()
