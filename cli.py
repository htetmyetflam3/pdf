#!/usr/bin/env python3
"""
CLI entry point. Parses args, reads file, calls main.run_pipeline().
"""

import sys
import os
import argparse

from module.main import run_pipeline



def parse_args(argv):
    parser = argparse.ArgumentParser(description="Extract Burmese text from PDF")
    parser.add_argument("pdf", help="Input PDF file path")
    parser.add_argument("out", nargs="?", default="", help="Output file/dir path (.txt/.docx/.pdf)")
    parser.add_argument("--no-convert", action="store_true", help="Skip Zawgyi->Unicode conversion")
    parser.add_argument("--no-postprocess", action="store_true", help="Skip imposter cleanup + mark reordering")
    parser.add_argument("-v", "--verbose", action="store_true", help="Extra detail")
    return parser.parse_args(argv)


def main():
    args = parse_args(sys.argv[1:])

    pdf_path = args.pdf
    if not os.path.exists(pdf_path):
        print(f"[-] Input PDF not found: {pdf_path}")
        sys.exit(1)

    # Resolve output path
    out_arg = args.out or '.'
    out_ext = os.path.splitext(out_arg)[1].lower()
    base_name = os.path.splitext(os.path.basename(pdf_path))[0]

    if out_ext in ('.txt', '.docx'):
        out_path = out_arg
    else:
        os.makedirs(out_arg, exist_ok=True)
        out_path = os.path.join(out_arg, base_name + '.txt')
        out_ext = '.txt'

    # Read PDF
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()
    print(f"[+] PDF size: {len(pdf_bytes):,} bytes")

    # Run pipeline
    print("[+] Extracting sequentially...")

    def on_progress(s):
        if args.verbose:
            print(f"    ... {s['done']}/{s['total']} pages done")

    result = run_pipeline(
        pdf_bytes,
        out_path,
        pdf_name=pdf_path,
        no_convert=args.no_convert,
        no_postprocess=args.no_postprocess,
        on_progress=on_progress if args.verbose else None,
    )

    print(f"[+] Pages: {result.page_count}, characters: {result.total_characters:,}")
    if not args.no_convert:
        print(f"[+] Detection: {result.zawgyi_count} Zawgyi, "
              f"{result.unicode_count} Unicode, {result.other_count} other")
        print("[+] Conversion done")
    print(f"[+] Saved: {result.output_path}")
    print("[+] Done.")


if __name__ == "__main__":
    main()
