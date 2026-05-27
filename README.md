# tsgrep

Parallel OCR grep over images. Finds images containing a text pattern using
Tesseract. Supports deduplication, checkpointing, and resuming interrupted scans.

## System dependencies

Install before `uv sync`:

```bash
# Debian / Ubuntu
sudo apt install tesseract-ocr libtesseract-dev libleptonica-dev

# Language packs (add what you need)
sudo apt install tesseract-ocr-rus   # Russian
sudo apt install tesseract-ocr-eng   # English (usually pre-installed)

# Arch
sudo pacman -S tesseract tesseract-data-rus

# macOS
brew install tesseract tesseract-lang
```

> `tesserocr` compiles a C extension against `libtesseract-dev` at install time.
> The system headers must be present before running `uv sync`.

## Install

```bash
uv sync
```

## Usage

```bash
# Basic — search for "Диплом" (case-insensitive) in current directory
uv run tsgrep "Диплом" .

# After uv sync installs the entrypoint
tsgrep "Диплом" .

# Multiple directories
tsgrep "Диплом" ~/scans /mnt/archive

# English, more workers
tsgrep --lang eng --workers 8 "Invoice" ~/docs

# With sudo (use venv python directly)
sudo .venv/bin/python ocr_grep.py "Диплом" /root/scans
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `PATTERN` | — | Text or regex, case-insensitive |
| `DIRS` | `.` | Directories or files to scan |
| `-l`, `--lang` | `rus` | Tesseract language code |
| `--psm` | `6` | Tesseract page segmentation mode |
| `-w`, `--workers` | `min(cpu_count, 4)` | Parallel OCR threads |
| `--checkpoint` | `/tmp/ocr_grep_checkpoint.json` | Checkpoint file path |
| `--no-checkpoint` | off | Disable checkpointing entirely |
| `--reset` | off | Delete checkpoint and start fresh |

## Features

- **Parallel OCR** — `ThreadPoolExecutor` with backpressure (max 256 in-flight futures)
- **Deduplication** — skips files already seen by `(size, mtime_ns)` key
- **Checkpointing** — flushed every 50 completions; survives Ctrl+C and crashes
- **Resume** — re-run the same command, already-processed files are skipped instantly
- **Regex patterns** — `PATTERN` is a full Python regex (`re.IGNORECASE`)
- **tesserocr** — direct C API binding; Tesseract LSTM model loaded once per thread

## Supported image formats

`.png` `.jpg` `.jpeg` `.tiff` `.tif` `.bmp` `.webp`

## Output

Matching file paths are written to stdout (one per line), suitable for piping:

```bash
tsgrep "Диплом" . | xargs -I{} cp {} ~/diploms/
```
