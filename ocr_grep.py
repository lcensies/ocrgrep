#!/usr/bin/env python3
"""
ocr_grep.py — parallel OCR search with dedup + checkpointing.

Usage:
    uv run ocr_grep.py [OPTIONS] PATTERN [DIRS...]

Examples:
    uv run ocr_grep.py "Диплом" .
    uv run ocr_grep.py --lang eng --workers 8 "Invoice" ~/docs ~/scans
"""

import argparse
import json
import os
import re
import signal
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import islice
from pathlib import Path

import pytesseract
from tqdm import tqdm

# Tesseract spawns its own threads; prevent N-workers × M-threads explosion
os.environ.setdefault("OMP_THREAD_LIMIT", "1")

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"}
CHECKPOINT_FILE = "/tmp/ocr_grep_checkpoint.json"
CHECKPOINT_FLUSH_EVERY = 50   # write checkpoint after this many completions
SUBMIT_BATCH_SIZE = 256        # max futures in-flight at once (backpressure)


# ---------------------------------------------------------------------------
# File key — (size, mtime_ns) tuple, no read needed
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
    tmp.replace(cp_path)   # atomic on POSIX


# ---------------------------------------------------------------------------
# OCR worker — pass path string directly, no Pillow decode
# ---------------------------------------------------------------------------

def ocr_matches(path: Path, regex: re.Pattern, lang: str, psm: int) -> bool:
    """Return True if OCR text matches regex."""
    try:
        text = pytesseract.image_to_string(str(path), lang=lang, config=f"--psm {psm}")
        return bool(regex.search(text))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# File discovery — generator, no upfront list
# ---------------------------------------------------------------------------

def iter_images(dirs: list[Path]):
    for d in dirs:
        if d.is_file():
            if d.suffix.lower() in IMAGE_EXTS:
                yield d
        else:
            for root, _, files in os.walk(d):
                for f in files:
                    p = Path(root) / f
                    if p.suffix.lower() in IMAGE_EXTS:
                        yield p


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
    parser.add_argument("pattern", help="Text / regex to search for (case-insensitive)")
    parser.add_argument("dirs", nargs="*", default=["."], help="Directories / files to scan")
    parser.add_argument("-l", "--lang", default="rus", help="Tesseract language (default: rus)")
    parser.add_argument("--psm", type=int, default=6, help="Tesseract PSM mode (default: 6)")
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
    args = parser.parse_args()

    regex = re.compile(args.pattern, re.IGNORECASE)
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
    all_images = list(iter_images(dirs))
    print(f"[scan] Found {len(all_images)} image(s)", file=sys.stderr)

    to_process: list[tuple[Path, str]] = []
    cached_matches: list[Path] = []

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
                    cached_matches.append(p)
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

    def _sigint(sig, frame):  # noqa: ANN001
        nonlocal shutdown
        shutdown = True
        print("\n[interrupt] Ctrl+C — finishing current jobs, saving checkpoint...", file=sys.stderr)

    signal.signal(signal.SIGINT, _sigint)

    matches: list[Path] = list(cached_matches)
    completed_since_flush = 0
    pending_iter = iter(to_process)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        # Seed initial batch
        active: dict = {}
        for p, k in islice(pending_iter, SUBMIT_BATCH_SIZE):
            fut = pool.submit(ocr_matches, p, regex, args.lang, args.psm)
            active[fut] = (p, k)

        with tqdm(total=len(to_process), desc="OCR", unit="file", file=sys.stderr) as bar:
            while active:
                if shutdown:
                    for f in active:
                        f.cancel()
                    break

                # Wait for next completion
                done_futs = []
                for fut in list(active):
                    if fut.done():
                        done_futs.append(fut)
                if not done_futs:
                    # block on next ready future
                    import time; time.sleep(0.01)
                    continue

                for fut in done_futs:
                    p, k = active.pop(fut)
                    try:
                        result = fut.result()
                    except Exception:
                        result = False

                    status = "match" if result else "no_match"
                    if not args.no_checkpoint:
                        checkpoint[k] = status
                    if result:
                        matches.append(p)
                    bar.update(1)
                    completed_since_flush += 1

                    # Periodic flush
                    if not args.no_checkpoint and completed_since_flush >= CHECKPOINT_FLUSH_EVERY:
                        save_checkpoint(cp_path, checkpoint)
                        completed_since_flush = 0

                    # Refill pipeline
                    if not shutdown:
                        for p2, k2 in islice(pending_iter, len(done_futs)):
                            f2 = pool.submit(ocr_matches, p2, regex, args.lang, args.psm)
                            active[f2] = (p2, k2)

    if not args.no_checkpoint:
        save_checkpoint(cp_path, checkpoint)
        print(f"[checkpoint] Saved → {cp_path}", file=sys.stderr)

    for p in sorted(matches):
        print(p)


if __name__ == "__main__":
    main()
