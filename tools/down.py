#!/usr/bin/env python3
"""
fetch_hf_myanmar_wiki.py
Downloads both 'text' and 'syllable' columns, no dedup, 5MB per file.
"""

import sys
import io
import os
import urllib.request
import urllib.error
import time
import pandas as pd

URL = "https://huggingface.co/datasets/DatarrX/myanmar-Wikipedia/resolve/main/data.parquet"
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
CHUNK_SIZE = 65536
MAX_RETRIES = 5
RETRY_DELAY = 2

def download_with_resume(url, max_retries=MAX_RETRIES):
    buffer = io.BytesIO()
    downloaded = 0
    for attempt in range(max_retries):
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; BurmeseFSM/1.0)',
            'Range': f'bytes={downloaded}-'
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                total = downloaded + int(response.headers.get('Content-Length', 0) or 0)
                while True:
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    buffer.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        percent = (downloaded / total) * 100
                        sys.stderr.write(f"\rDownload: {percent:.1f}% ({downloaded/1024/1024:.1f}MB)")
                    else:
                        sys.stderr.write(f"\rDownloaded: {downloaded/1024/1024:.1f}MB")
            sys.stderr.write("\n")
            buffer.seek(0)
            return buffer
        except urllib.error.HTTPError as e:
            if e.code == 416:
                sys.stderr.write("\nResuming: file already complete\n")
                buffer.seek(0)
                return buffer
            raise
        except Exception as e:
            wait = RETRY_DELAY * (2 ** attempt)
            sys.stderr.write(f"\nRetry {attempt+1}/{max_retries} after {wait}s: {e}\n")
            time.sleep(wait)
    raise RuntimeError(f"Failed after {max_retries} retries")

def get_output_file(prefix, part_num):
    return f"{prefix}_{part_num:03d}.txt"

def write_lines(lines, prefix, max_bytes=MAX_FILE_SIZE):
    """Write iterable of strings to files capped at max_bytes. No dedup."""
    part_num = 1
    current_file = get_output_file(prefix, part_num)
    f = open(current_file, 'w', encoding='utf-8')
    files_created = [current_file]
    current_size = 0
    total_written = 0

    for line_content in lines:
        line = f"{line_content}\n"
        line_bytes = len(line.encode('utf-8'))

        if current_size > 0 and current_size + line_bytes > max_bytes:
            f.close()
            part_num += 1
            current_file = get_output_file(prefix, part_num)
            f = open(current_file, 'w', encoding='utf-8')
            files_created.append(current_file)
            current_size = 0
            print(f"\nRolling over to {current_file}...", file=sys.stderr)

        f.write(line)
        current_size += line_bytes
        total_written += 1

    f.close()
    return files_created, total_written

def main():
    print("Downloading from Hugging Face...", file=sys.stderr)
    buffer = download_with_resume(URL)

    print("Reading parquet...", file=sys.stderr)
    df = pd.read_parquet(buffer, columns=['text', 'syllable'])

    print(f"Total rows: {len(df)}", file=sys.stderr)

    # --- TEXT column: one row per line ---
    print("Writing text column...", file=sys.stderr)
    text_lines = df['text'].astype(str).replace('nan', '').replace('None', '')
    text_files, text_count = write_lines(text_lines, "text", MAX_FILE_SIZE)

    # --- SYLLABLE column: split on spaces, one syllable per line, NO DEDUP ---
    print("Writing syllable column...", file=sys.stderr)
    s = df['syllable'].astype(str).replace('nan', '')
    # explode splits into one row per syllable, no dedup
    syllable_lines = s.str.split().explode()
    syllable_lines = syllable_lines[syllable_lines.str.len() > 0]
    syllable_files, syllable_count = write_lines(syllable_lines, "syllable", MAX_FILE_SIZE)

    print(f"\nDone!", file=sys.stderr)
    print(f"Text: {text_count} lines across {len(text_files)} files", file=sys.stderr)
    for fname in text_files:
        print(f"  {fname}: {os.path.getsize(fname)/1024/1024:.1f}MB", file=sys.stderr)
    print(f"Syllable: {syllable_count} lines across {len(syllable_files)} files", file=sys.stderr)
    for fname in syllable_files:
        print(f"  {fname}: {os.path.getsize(fname)/1024/1024:.1f}MB", file=sys.stderr)

if __name__ == '__main__':
    main()
