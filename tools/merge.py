#!/usr/bin/env python3
# Description: Memory-efficient merge of three large .txt and .docx files.
# Reads c1to700, c701to1k, ending in order. Streams line-by-line for txt.
# For docx: uses incremental XML parsing with UTF-8 safe chunking to stream
# body elements without loading the full document.xml into RAM.
# Outputs to an "output" subdirectory in the source directory.

import os
import sys
import shutil
import zipfile
from pathlib import Path
from xml.sax import make_parser, ContentHandler
from xml.sax.handler import feature_external_ges, feature_external_pes

# --- TXT: stream line by line ---
def merge_txt_files(source_dir, output_dir):
    txt_files = ["c1to700.txt", "c701to1k.txt", "ending.txt"]
    output_path = output_dir / "merged.txt"

    with open(output_path, "w", encoding="utf-8") as outfile:
        for fname in txt_files:
            src = source_dir / fname
            if not src.exists():
                print(f"Warning: {fname} not found, skipping.")
                continue
            print(f"  Streaming {fname} ...")
            with open(src, "r", encoding="utf-8") as infile:
                shutil.copyfileobj(infile, outfile)
                outfile.write("\n")
    print(f"Merged .txt → {output_path}")


# --- SAX Handler: captures everything inside <w:body> ... </w:body> ---
class BodyExtractor(ContentHandler):
    def __init__(self, out_buffer):
        self.out = out_buffer
        self.depth = 0
        self.in_body = False
        self.body_closed = False
        self._buf = []

    def _flush_buf(self):
        if self._buf:
            self.out.write("".join(self._buf).encode("utf-8"))
            self._buf.clear()

    def startElement(self, name, attrs):
        if self.body_closed:
            return
        if name == "w:body":
            self.in_body = True
            self.depth = 1
            return
        if not self.in_body:
            return
        self.depth += 1
        tag = ["<", name]
        for k, v in attrs.items():
            tag.append(' ')
            tag.append(k)
            tag.append('="')
            tag.append(v.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;"))
            tag.append('"')
        tag.append(">")
        self._buf.append("".join(tag))

    def endElement(self, name):
        if self.body_closed:
            return
        if name == "w:body":
            self.in_body = False
            self.body_closed = True
            self._flush_buf()
            return
        if not self.in_body:
            return
        self.depth -= 1
        self._buf.append("</")
        self._buf.append(name)
        self._buf.append(">")
        if self.depth == 1:
            self._flush_buf()

    def characters(self, content):
        if not self.in_body or self.body_closed:
            return
        self._buf.append(
            content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        if len(self._buf) > 500:
            self._flush_buf()


# --- UTF-8 safe chunked decoder ---
class Utf8ChunkDecoder:
    """Feeds a SAX parser from a byte stream in chunks, handling split multi-byte chars."""
    def __init__(self, byte_stream, chunk_size=65536):
        self.stream = byte_stream
        self.chunk_size = chunk_size
        self._carry = b""

    def __iter__(self):
        while True:
            chunk = self.stream.read(self.chunk_size)
            if not chunk:
                if self._carry:
                    # Last bytes — try to decode, drop incomplete ones
                    try:
                        yield self._carry.decode("utf-8")
                    except UnicodeDecodeError:
                        # Drop trailing incomplete bytes
                        for i in range(1, 5):
                            try:
                                yield self._carry[:-i].decode("utf-8")
                                break
                            except UnicodeDecodeError:
                                continue
                break

            data = self._carry + chunk
            # Find the last complete UTF-8 char boundary
            # A valid UTF-8 start byte is 0xxxxxxx or 11xxxxxx
            # Continuation bytes are 10xxxxxx
            cutoff = len(data)
            for i in range(min(4, len(data)), 0, -1):
                b = data[-i]
                # Check if this byte could be a start of a multi-byte sequence
                if b < 0x80 or b >= 0xC0:
                    # It's a start byte or ASCII — everything before is complete
                    cutoff = len(data) - i + 1
                    try:
                        data[:cutoff].decode("utf-8")
                        self._carry = data[cutoff:]
                        break
                    except UnicodeDecodeError:
                        continue
            else:
                # All trailing bytes look like continuation bytes — keep them all
                self._carry = data
                continue

            yield data[:cutoff].decode("utf-8")


def extract_body_streaming(docx_path, out_buffer):
    """Open docx, stream-parse document.xml, write body content to out_buffer."""
    with zipfile.ZipFile(docx_path, "r") as zin:
        with zin.open("word/document.xml") as xml_stream:
            parser = make_parser()
            parser.setFeature(feature_external_ges, False)
            parser.setFeature(feature_external_pes, False)
            handler = BodyExtractor(out_buffer)
            parser.setContentHandler(handler)

            decoder = Utf8ChunkDecoder(xml_stream, chunk_size=65536)
            for text_chunk in decoder:
                parser.feed(text_chunk)
            parser.close()


def merge_docx_files(source_dir, output_dir):
    docx_files = ["c1to700.docx", "c701to1k.docx", "ending.docx"]
    output_path = output_dir / "merged.docx"

    template_src = None
    for fname in docx_files:
        candidate = source_dir / fname
        if candidate.exists():
            template_src = candidate
            break
    if template_src is None:
        print("No template .docx found.")
        return

    print(f"  Building merged.docx ...")
    temp_body_file = output_dir / ".merged_body_temp.xml"

    with open(temp_body_file, "wb") as body_out:
        for fname in docx_files:
            src = source_dir / fname
            if not src.exists():
                print(f"Warning: {fname} not found, skipping.")
                continue
            print(f"  Streaming body from {fname} ...")
            extract_body_streaming(src, body_out)

    # Build merged document.xml as temp file
    temp_docxml = output_dir / ".merged_document.xml"
    with open(temp_docxml, "wb") as dout:
        with zipfile.ZipFile(template_src, "r") as zin:
            template_xml = zin.read("word/document.xml").decode("utf-8", errors="replace")
            start = template_xml.find("<w:body>")
            end = template_xml.find("</w:body>")

            dout.write(template_xml[:start + 8].encode("utf-8"))
            with open(temp_body_file, "rb") as body_in:
                shutil.copyfileobj(body_in, dout)
            dout.write(template_xml[end:].encode("utf-8"))

    # Assemble final docx
    final_path = output_dir / "merged.docx"
    with zipfile.ZipFile(template_src, "r") as zin:
        with zipfile.ZipFile(final_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename == "word/document.xml":
                    with open(temp_docxml, "rb") as f:
                        zout.writestr(item, f.read())
                else:
                    with zin.open(item) as fsrc:
                        zout.writestr(item, fsrc.read())

    temp_body_file.unlink(missing_ok=True)
    temp_docxml.unlink(missing_ok=True)

    print(f"Merged .docx → {final_path}")


def main():
    if len(sys.argv) > 1:
        source_dir = Path(sys.argv[1]).resolve()
    else:
        source_dir = Path.cwd()

    output_dir = source_dir / "output"
    output_dir.mkdir(exist_ok=True)

    print(f"Source: {source_dir}")
    print(f"Output: {output_dir}")

    merge_txt_files(source_dir, output_dir)
    merge_docx_files(source_dir, output_dir)
    print("Done.")

if __name__ == "__main__":
    main()
