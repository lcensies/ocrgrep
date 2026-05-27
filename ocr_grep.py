#!/usr/bin/env python3
"""
ocr_grep.py — parallel OCR search with dedup + checkpointing.

Usage:
    uv run ocr_grep.py [OPTIONS] [PATTERN] [DIRS...]
    uv run ocr_grep.py [OPTIONS] -e PATTERN [-e PATTERN ...] [DIRS...]

Examples:
    uv run ocr_grep.py "Invoice" .
    uv run ocr_grep.py --lang eng --workers 8 "Invoice" ~/docs ~/scans
    uv run ocr_grep.py -e "Invoice" -e "Receipt" ~/docs
    uv run ocr_grep.py -v "Draft" .                   # files NOT matching
    uv run ocr_grep.py --files-without-match "Draft" .
    uv run ocr_grep.py -c "Total" .                   # print filename:count
    uv run ocr_grep.py -i "hello" .                   # case-insensitive (default)
    uv run ocr_grep.py --no-ignore-case "Hello" .     # case-sensitive
    uv run ocr_grep.py -F "hello.world" .             # literal string, not regex
    uv run ocr_grep.py --include "*.png" "foo" .      # only .png files
    uv run ocr_grep.py --exclude "thumb_*" "foo" .    # skip thumb_* files
    uv run ocr_grep.py -q "foo" . && echo found       # quiet, exit code only
    uv run ocr_grep.py -m 5 "foo" .                   # stop after 5 matching files

grep-parity flags implemented:
    -e PATTERN          Add a pattern (OR'd with others; repeatable)
    -v / --files-without-match
                        Invert: print files that do NOT match
    --files-with-matches
                        Print files that match (default; compat alias)
    -c / --count        Print filename:N (N = number of regex matches in file)
    -i / --ignore-case  Case-insensitive match (on by default)
    --no-ignore-case    Case-sensitive match
    -F / --fixed-strings
                        Treat pattern as literal string, not regex
    --include GLOB      Only scan filenames matching GLOB (repeatable)
    --exclude GLOB      Skip filenames matching GLOB (repeatable)
    -q / --quiet        Suppress output; exit 0 if any match, 1 if none
    -m N / --max-count  Stop after N matching files found
    -r (implicit)       Always recurses into subdirectories via os.walk

tesseract-specific flags:
    -l / --lang LANG    Tesseract language code (default: eng)
    --psm N             Tesseract page segmentation mode (default: 6)
    -w / --workers N    Parallel OCR worker threads (default: min(cpu_count, 4))
                        NOTE: conflicts with grep's -w (word-regexp), not implemented

checkpointing flags (not in grep):
    --checkpoint PATH   Path to checkpoint file (default: /tmp/ocr_grep_checkpoint.json)
    --no-checkpoint     Disable read+write of checkpoint
    --reset             Delete existing checkpoint before scanning
"""

import argparse
import fnmatch
import json
import os
import re
import signal
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import islice
from pathlib import Path

import tesserocr
from tqdm import tqdm

# Prevent N-workers × M-threads explosion inside tesseract
os.environ.setdefault("OMP_THREAD_LIMIT", "1")

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"}
CHECKPOINT_FILE = "/tmp/ocr_grep_checkpoint.json"
CHECKPOINT_FLUSH_EVERY = 50
SUBMIT_BATCH_SIZE = 256

# ---------------------------------------------------------------------------
# Thread-local tesseract API — model loaded once per thread, reused per image
# ---------------------------------------------------------------------------

_thread_local = threading.local()


def _get_api(lang: str, psm: int) -> tesserocr.PyTessBaseAPI:
    key = (lang, psm)
    if getattr(_thread_local, "api_key", None) != key:
        if hasattr(_thread_local, "api"):
            _thread_local.api.End()
        _thread_local.api = tesserocr.PyTessBaseAPI(lang=lang, psm=psm)
        _thread_local.api_key = key
    return _thread_local.api


# ---------------------------------------------------------------------------
# File key — (size, mtime_ns), no read needed
# ---------------------------------------------------------------------------

def file_key(path: Path) -> str:
    s = path.stat()
    return f"{s.st_size}:{s.st_mtime_ns}"


# ---------------------------------------------------------------------------
# Checkpoint  {key: "match" | "no_match"}
# ---------------------------------------------------------------------------

def load_checkpoint(cp_path: Path) -> dict[str, str]:
    if cp_path.exists():
        try:
            return json.loads(cp_path.read_text())
        except Exception:
            return {}
    return {}


def save_checkpoint(cp_path: Path, data: dict[str, str]) -> None:
    tmp = cp_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(cp_path)  # atomic on POSIX


# ---------------------------------------------------------------------------
# OCR worker — returns match count (0 = no match)
# ---------------------------------------------------------------------------

def ocr_matches(path: Path, regex: re.Pattern, lang: str, psm: int) -> int:
    try:
        from PIL import Image
        api = _get_api(lang, psm)
        api.SetImage(Image.open(path))
        text = api.GetUTF8Text()
        return len(regex.findall(text))
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# File discovery — generator
# ---------------------------------------------------------------------------

def iter_images(dirs: list[Path], include_globs: list[str], exclude_globs: list[str]):
    for d in dirs:
        if d.is_file():
            if d.suffix.lower() in IMAGE_EXTS:
                if _glob_filter(d, include_globs, exclude_globs):
                    yield d
        else:
            for root, _, files in os.walk(d):
                for f in files:
                    p = Path(root) / f
                    if p.suffix.lower() in IMAGE_EXTS:
                        if _glob_filter(p, include_globs, exclude_globs):
                            yield p


def _glob_filter(p: Path, include_globs: list[str], exclude_globs: list[str]) -> bool:
    """Return True if file should be included."""
    name = p.name
    if include_globs and not any(fnmatch.fnmatch(name, g) for g in include_globs):
        return False
    if exclude_globs and any(fnmatch.fnmatch(name, g) for g in exclude_globs):
        return False
    return True


# ---------------------------------------------------------------------------
# Parallel key computation
# ---------------------------------------------------------------------------

def _key_worker(path: Path) -> tuple[Path, str] | None:
    try:
        return path, file_key(path)
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="OCR-grep images in parallel with dedup + checkpointing."
    )
    # Pattern args
    parser.add_argument(
        "pattern", nargs="?", default=None,
        help="Text / regex to search for. Optional when -e is used.",
    )
    parser.add_argument("dirs", nargs="*", default=["."], help="Directories / files to scan")

    # grep-parity: multiple patterns
    parser.add_argument(
        "-e", "--regexp", action="append", dest="patterns", metavar="PATTERN",
        help="Pattern to search for (can be repeated; OR'd together). "
             "When used, positional PATTERN is also added if given.",
    )

    # Tesseract options
    parser.add_argument("-l", "--lang", default="eng", help="Tesseract language (default: eng)")
    parser.add_argument("--psm", type=int, default=6, help="Tesseract PSM mode (default: 6)")

    # Workers / checkpoint
    parser.add_argument(
        "-w", "--workers", type=int,
        default=min(os.cpu_count() or 4, 4),
        help="Parallel OCR workers (default: min(cpu_count, 4))",
    )
    parser.add_argument(
        "--checkpoint", default=CHECKPOINT_FILE,
        help=f"Checkpoint file path (default: {CHECKPOINT_FILE})",
    )
    parser.add_argument(
        "--no-checkpoint", action="store_true",
        help="Disable checkpointing (ignore + don't write)",
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="Delete existing checkpoint and start fresh",
    )

    # grep-parity: match control
    parser.add_argument(
        "-v", "--invert-match", action="store_true",
        help="Print files that do NOT match.",
    )
    parser.add_argument(
        "--files-without-match", action="store_true",
        help="Alias for --invert-match (grep -L compatible name).",
    )
    parser.add_argument(
        "--files-with-matches", action="store_true",
        help="Print files that match (default behavior, no-op flag for compat).",
    )
    parser.add_argument(
        "-c", "--count", action="store_true",
        help="Print filename:N instead of just filename (N = regex match count).",
    )
    parser.add_argument(
        "-i", "--ignore-case", action="store_true", default=True,
        help="Case-insensitive matching (default: on).",
    )
    parser.add_argument(
        "--no-ignore-case", "-s", dest="ignore_case", action="store_false",
        help="Case-sensitive matching.",
    )
    parser.add_argument(
        "-F", "--fixed-strings", action="store_true",
        help="Treat pattern as a literal string (re.escape), not a regex.",
    )

    # grep-parity: file filtering
    parser.add_argument(
        "--include", action="append", dest="include_globs", metavar="GLOB", default=[],
        help="Only scan files matching GLOB (e.g. *.png). Can be repeated.",
    )
    parser.add_argument(
        "--exclude", action="append", dest="exclude_globs", metavar="GLOB", default=[],
        help="Skip files matching GLOB. Can be repeated.",
    )

    # grep-parity: output control
    parser.add_argument(
        "-q", "--quiet", "--silent", action="store_true",
        help="Print nothing; exit 0 if any match found, 1 if none.",
    )
    parser.add_argument(
        "-m", "--max-count", type=int, default=None, metavar="N",
        help="Stop after finding N matching files.",
    )

    args = parser.parse_args()

    # --- Build patterns list ---
    all_patterns: list[str] = list(args.patterns or [])
    if args.pattern is not None:
        all_patterns.append(args.pattern)
    if not all_patterns:
        parser.error("at least one pattern required (positional or via -e)")

    if args.fixed_strings:
        all_patterns = [re.escape(p) for p in all_patterns]

    combined = "|".join(f"(?:{p})" for p in all_patterns)
    re_flags = re.IGNORECASE if args.ignore_case else 0
    regex = re.compile(combined, re_flags)

    # Invert: either -v or --files-without-match
    invert = args.invert_match or args.files_without_match

    cp_path = Path(args.checkpoint)

    if args.reset and cp_path.exists():
        cp_path.unlink()
        print(f"[reset] Removed {cp_path}", file=sys.stderr)

    checkpoint: dict[str, str] = {} if args.no_checkpoint else load_checkpoint(cp_path)

    dirs = [Path(d) for d in args.dirs]
    for d in dirs:
        if not d.exists():
            print(f"[warn] {d} does not exist, skipping", file=sys.stderr)

    # --- Phase 1: discover + key files in parallel ---
    print("[scan] Discovering and keying images...", file=sys.stderr)
    all_images = list(iter_images(dirs, args.include_globs, args.exclude_globs))
    print(f"[scan] Found {len(all_images)} image(s)", file=sys.stderr)

    to_process: list[tuple[Path, str]] = []
    cached_matches: list[tuple[Path, int]] = []  # (path, count)

    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 4)) as key_pool:
        key_futures = {key_pool.submit(_key_worker, p): p for p in all_images}
        for fut in tqdm(as_completed(key_futures), total=len(all_images),
                        desc="keying", unit="file", file=sys.stderr):
            result = fut.result()
            if result is None:
                continue
            p, k = result
            if k in checkpoint:
                if checkpoint[k] == "match":
                    # Cached hits: count unknown, store 1 as sentinel (non-zero = match)
                    cached_matches.append((p, 1))
            else:
                to_process.append((p, k))

    print(
        f"[dedup] {len(cached_matches)} cached match(es), "
        f"{len(checkpoint) - len(cached_matches)} cached no-match(es), "
        f"{len(to_process)} to process",
        file=sys.stderr,
    )

    # --- Phase 2: OCR with backpressure + periodic checkpoint flush ---
    shutdown = False
    max_count = args.max_count

    def _sigint(sig, frame):  # noqa: ANN001
        nonlocal shutdown
        shutdown = True
        print("\n[interrupt] Ctrl+C — finishing current jobs, saving checkpoint...", file=sys.stderr)

    signal.signal(signal.SIGINT, _sigint)

    # matches: list of (path, count)
    matches: list[tuple[Path, int]] = list(cached_matches)

    # Respect max_count on cached results
    if max_count is not None and len(matches) >= max_count:
        matches = matches[:max_count]
        shutdown = True

    completed_since_flush = 0
    pending_iter = iter(to_process)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        active: dict = {}
        for p, k in islice(pending_iter, SUBMIT_BATCH_SIZE):
            if shutdown:
                break
            fut = pool.submit(ocr_matches, p, regex, args.lang, args.psm)
            active[fut] = (p, k)

        with tqdm(total=len(to_process), desc="OCR", unit="file", file=sys.stderr) as bar:
            while active:
                if shutdown:
                    for f in active:
                        f.cancel()
                    break

                done_futs = [f for f in active if f.done()]
                if not done_futs:
                    import time; time.sleep(0.01)
                    continue

                for fut in done_futs:
                    p, k = active.pop(fut)
                    try:
                        count = fut.result()
                    except Exception:
                        count = 0

                    status = "match" if count > 0 else "no_match"
                    if not args.no_checkpoint:
                        checkpoint[k] = status
                    if count > 0:
                        matches.append((p, count))
                        if max_count is not None and len(matches) >= max_count:
                            shutdown = True
                    bar.update(1)
                    completed_since_flush += 1

                    if not args.no_checkpoint and completed_since_flush >= CHECKPOINT_FLUSH_EVERY:
                        save_checkpoint(cp_path, checkpoint)
                        completed_since_flush = 0

                    if not shutdown:
                        for p2, k2 in islice(pending_iter, len(done_futs)):
                            f2 = pool.submit(ocr_matches, p2, regex, args.lang, args.psm)
                            active[f2] = (p2, k2)

    if not args.no_checkpoint:
        save_checkpoint(cp_path, checkpoint)
        print(f"[checkpoint] Saved → {cp_path}", file=sys.stderr)

    # --- Output ---
    if invert:
        # Files that did NOT match: all_images minus matched paths
        matched_paths = {p for p, _ in matches}
        output_paths = [p for p in all_images if p not in matched_paths]
        if args.quiet:
            sys.exit(0 if output_paths else 1)
        for p in sorted(output_paths):
            print(p)
        sys.exit(0)

    if args.quiet:
        sys.exit(0 if matches else 1)

    for p, count in sorted(matches, key=lambda x: x[0]):
        if args.count:
            print(f"{p}:{count}")
        else:
            print(p)


if __name__ == "__main__":
    main()
