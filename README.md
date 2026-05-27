# ocrgrep

`grep` for image text. Runs Tesseract OCR in parallel across a directory tree and prints paths of images whose text matches a pattern.

## Dependencies

```bash
# Debian / Ubuntu
sudo apt install tesseract-ocr libtesseract-dev libleptonica-dev

# Arch
sudo pacman -S tesseract tesseract-data-eng

# macOS
brew install tesseract
```

Language packs (install as needed):
```bash
sudo apt install tesseract-ocr-deu   # German
sudo apt install tesseract-ocr-fra   # French
# full list: apt search tesseract-ocr-
```

## Install

```bash
uv sync
```

## Usage

```
ocrgrep [OPTIONS] PATTERN [DIRS...]
ocrgrep [OPTIONS] -e PATTERN [-e PATTERN ...] [DIRS...]
```

```bash
ocrgrep "Invoice" ~/scans
ocrgrep -e "Invoice" -e "Receipt" ~/docs ~/archive
ocrgrep -v "Draft" .                      # files NOT containing "Draft"
ocrgrep -c "Total" .                      # print filename:match_count
ocrgrep --lang deu "Rechnung" ~/scans     # German OCR
ocrgrep --workers 8 "signature" .
ocrgrep "Invoice" . | xargs -I{} cp {} ~/invoices/
```

## Options

**Pattern**

| Flag | Description |
|------|-------------|
| `PATTERN` | Regex (or literal with `-F`), case-insensitive by default |
| `-e PATTERN` | Add a pattern; OR'd with others; repeatable |
| `-F` | Treat pattern as literal string |
| `-i` / `--no-ignore-case` | Case-insensitive (default on) / case-sensitive |

**Output**

| Flag | Description |
|------|-------------|
| `-v` | Print files that do NOT match |
| `-c` | Print `filename:N` (match count per file) |
| `-q` | No output; exit 0 if any match, 1 if none |
| `-m N` | Stop after N matching files |

**File filtering**

| Flag | Description |
|------|-------------|
| `--include GLOB` | Only scan filenames matching GLOB (repeatable) |
| `--exclude GLOB` | Skip filenames matching GLOB (repeatable) |

**Tesseract**

| Flag | Default | Description |
|------|---------|-------------|
| `-l` / `--lang` | `eng` | Tesseract language code |
| `--psm` | `6` | Page segmentation mode |
| `-w` / `--workers` | `min(cpu_count, 4)` | Parallel OCR threads |

**Checkpoint**

| Flag | Default | Description |
|------|---------|-------------|
| `--checkpoint` | `/tmp/ocr_grep_checkpoint.json` | Checkpoint file path |
| `--no-checkpoint` | — | Disable checkpointing |
| `--reset` | — | Delete checkpoint and start fresh |

Resuming: re-run the same command after interruption — already-processed files are skipped instantly using `(size, mtime_ns)` as a cache key.

## Supported formats

`.png` `.jpg` `.jpeg` `.tiff` `.tif` `.bmp` `.webp`

## Packaging

See [`packaging/`](packaging/) for Arch (PKGBUILD), RPM spec, Debian rules, Nix derivation, and Dockerfile.
