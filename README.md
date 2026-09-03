# PDF Burmese Text Extractor

Extract and normalize Burmese (Myanmar) text from PDFs. Handles Zawgyi-encoded
embedded fonts that standard extractors fail on, plus Unicode PDFs where fonts
use non-standard character substitutions.

This README documents the flags / switches that change the behaviour of the
code on the `dev` branch.

---

## Requirements

- Python >= 3.10
- Dependencies listed in `pyproject.toml`
- `python-docx` (optional for some tool modes; most fallbacks work without it)

---

## Main CLI: `cli.py`

```bash
python cli.py <pdf> [out] [flags]
```

| Flag / argument | Default | Description |
| --- | --- | --- |
| `pdf` | required | Input PDF file path |
| `out` | `.` (current dir) | Output file or directory path (`.txt` / `.docx`) |
| `--no-convert` | off | Skip Zawgyi → Unicode conversion |
| `--no-postprocess` | off | Skip imposter cleanup + mark reordering |
| `-v`, `--verbose` | off | Print per-page progress detail |

Examples:

```bash
python cli.py input/pdf/c1to700.pdf
python cli.py input/pdf/c1to700.pdf output/book.txt --no-convert
python cli.py input/pdf/c1to700.pdf output/book.docx --no-postprocess
python cli.py input/pdf/c1to700.pdf output/ -v
```

### Output-format flag (by extension)

The output format is chosen by the output file extension (there is no
separate format flag):

| Extension | Writer | Notes |
| --- | --- | --- |
| `.txt` | `write_txt` | Plain text with metadata header + page markers |
| `.docx` | `write_docx` | Word document, falls back to manual OOXML writer if python-docx is unavailable |
| any other | n/a | Raises `Unsupported output format` |

---

## Pipeline API: `module/main.py`

```python
from module.main import run_pipeline

result = run_pipeline(
    pdf_bytes,
    out_path,
    pdf_name="input.pdf",
    no_convert=False,
    no_postprocess=False,
    on_progress=callback,
)
```

| Keyword flag | Default | Description |
| --- | --- | --- |
| `no_convert` | `False` | If `True`, skip Zawgyi detection + conversion |
| `no_postprocess` | `False` | If `True`, skip imposter cleanup + mark reordering |
| `on_progress` | `None` | Callback called with `{"done": int, "total": int}` per page |

---

## Tools

### `tools/merge.py` — merge TXT / DOCX chunk files

```bash
python tools/merge.py [SOURCE_DIR] [flags]
```

| Flag / argument | Default | Description |
| --- | --- | --- |
| `source_dir` | `.` | Directory containing `c1to700`, `c701to1k`, `ending` files |
| `--inspect DOCX ...` | off | Inspect DOCX paragraph/line counts instead of merging |
| `--verify` | off | After merging, reopen `merged.docx` with python-docx |
| `--txt-only` | off | Merge TXT files only |
| `--docx-only` | off | Merge DOCX files only |
| `--use-python-docx` | off | Use python-docx merge instead of the streaming writer (small files / files with images or hyperlinks) |

### `tools/particle_score.py` — final particle scoring

```bash
python tools/particle_score.py <book.txt> [more.txt ...] [flags]
```

| Flag / argument | Default | Description |
| --- | --- | --- |
| `book.txt ...` | required | Syllable-segmented text files |
| `--include-quotes` | off | Include quoted text in particle extraction |

Config flag is a module-level constant, not a CLI flag:
`FINAL_SYLLABLE_COUNT` in `tools/particle_score.py` (default `1`).

### `tools/profile_pdf.py` — pipeline profiling

```bash
python tools/profile_pdf.py <pdf> [flags]
```

| Flag / argument | Default | Description |
| --- | --- | --- |
| `pdf` | required | Input PDF path |
| `--pages N` | `200` | How many pages to benchmark |
| `--cprofile` | off | Run under cProfile and print the hot functions |
| `--out PATH` | `output/profile_bench.docx` | Output path for the benchmark run |

### `tools/inspect_docx.py` — inspect DOCX files

```bash
python tools/inspect_docx.py file1.docx [file2.docx ...]
```

Positional DOCX paths only; no flags. Used by `tools/merge.py --inspect`.

### `tools/repair_docx.py` — repair / modernize DOCX files

```bash
python tools/repair_docx.py file1.docx [file2.docx ...]
```

Positional DOCX paths only; no flags.

---

## Git helpers

### `git.sh` — daily commit script

```bash
./git.sh [--r] [commit-message]
```

| Flag / argument | Default | Description |
| --- | --- | --- |
| `--r` | off | Also push to the git remote after committing |
| `commit-message` | auto | Custom commit message; default is generated from the bumped version + date/time |

The script also auto-bumps the version in `pyproject.toml`.

### `gitinit.sh` — interactive git init / identity setup

Interaction is driven by prompt answers rather than CLI flags. These are the
configurable defaults:

| Variable | Default |
| --- | --- |
| `DEFAULT_REMOTE` | `git@github.com:htetmyetflam3/pdf.git` |
| `DEFAULT_USER` | `htetmyetflam3` |
| `DEFAULT_EMAIL` | `149076482+htetmyetflam3@users.noreply.github.com` |
| `DEFAULT_BRANCH` | `main` |
| `DEFAULT_SSH_KEY` | `${HOME}/.ssh/PDF` |

---

## Quick reference

The flags that vary main-pipeline behaviour are the two switches in the CLI /
API:

| Flag | Effect |
| --- | --- |
| `--no-convert` / `no_convert` | Keep raw Zawgyi text; no detection/conversion |
| `--no-postprocess` / `no_postprocess` | Keep unclean/imposter text; no cleanup/reorder |

Everything else is a tool-level flag or output-format selection.
