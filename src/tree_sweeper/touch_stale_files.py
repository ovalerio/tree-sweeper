#!/usr/bin/env python3
"""
GPFS Stale File Accesser & Logger
Traverses directory trees, identifies files untouched for > 1 year,
safely accesses them based on file type, and logs unhandled files.
Parallelized using ProcessPoolExecutor for multi-core batch processing.
"""

from __future__ import annotations

import argparse
import logging
import mimetypes
from pathlib import Path
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
import os
import sys
from typing import NamedTuple

# Default directories and file patterns to ignore
DEFAULT_IGNORED_DIRS = {
    ".git", ".svn", ".hg", ".vscode", ".idea", ".cache", 
    "__pycache__", ".pytest_cache", "node_modules", "build", 
    "dist", ".conda", ".miniconda", ".anaconda", "venv", 
    ".venv", ".apptainer", ".singularity", ".snapshot", 
    ".snapshots", "lost+found", ".Trash"
}

DEFAULT_IGNORED_EXTENSIONS = {
    ".lock", ".tmp", ".log", ".swp", ".swo"
}

def setup_logging(log_path: str, verbose: bool):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] [PID %(process)d] %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout)
        ]
    )

def default_file_accessor(file_path: Path) -> bool:
    """
    Default access handler: reads the first 4KB of the file to trigger 
    filesystem-level atime update without loading large files into memory.
    """
    try:
        with open(file_path, "rb", buffering=0) as f:
            f.read(4096)
        return True
    except Exception as e:
        logging.error(f"Failed to access {file_path}: {e}")
        return False

# Register custom file type handlers if needed
FILE_TYPE_HANDLERS = {
    # Example: Custom handler for text files or specific binary formats
    # "text": lambda p: ...
}

def get_file_handler(file_path: Path):
    mime, _ = mimetypes.guess_type(str(file_path))
    # Fallback to default chunk-read accessor for recognized types or general files
    if mime or file_path.suffix.lower() not in DEFAULT_IGNORED_EXTENSIONS:
        return default_file_accessor
    return None

def process_directory(dir_path: Path, cutoff_time: float, ignored_dirs: set, dry_run: bool,
                      recursive: bool = True) -> tuple[int, int, list[str]]:
    """
    Walks a single directory subtree, checking file access times.
    With recursive=False only the files directly inside dir_path are checked.
    Returns: (processed_count, skipped_unhandled_count, unhandled_paths)
    """
    processed = 0
    skipped_unhandled = 0
    unhandled_paths = []

    try:
        # Using os.scandir for high-performance traversal
        with os.scandir(dir_path) as entries:
            for entry in entries:
                try:
                    # Skip ignored directories
                    if entry.is_dir(follow_symlinks=False):
                        if not recursive or entry.name in ignored_dirs:
                            continue
                        # Recursively process subdirectories
                        sub_processed, sub_skipped, sub_unhandled = process_directory(
                            Path(entry.path), cutoff_time, ignored_dirs, dry_run
                        )
                        processed += sub_processed
                        skipped_unhandled += sub_skipped
                        unhandled_paths.extend(sub_unhandled)

                    elif entry.is_file(follow_symlinks=False):
                        path_obj = Path(entry.path)
                        
                        # Check file extension ignore list
                        if path_obj.suffix.lower() in DEFAULT_IGNORED_EXTENSIONS:
                            continue

                        # Check access time (atime)
                        stat_info = entry.stat(follow_symlinks=False)
                        if stat_info.st_atime < cutoff_time:
                            accessor = get_file_handler(path_obj)
                            if accessor:
                                if not dry_run:
                                    success = accessor(path_obj)
                                    if success:
                                        processed += 1
                                        logging.debug(f"Accessed stale file: {path_obj}")
                                    else:
                                        unhandled_paths.append(str(path_obj))
                                        skipped_unhandled += 1
                                else:
                                    logging.info(f"[DRY-RUN] Would access stale file: {path_obj}")
                                    processed += 1
                            else:
                                logging.warning(f"Could not determine file type/handler for: {path_obj}")
                                unhandled_paths.append(str(path_obj))
                                skipped_unhandled += 1

                except (PermissionError, FileNotFoundError) as e:
                    logging.warning(f"Access error on {entry.path}: {e}")
                except Exception as e:
                    logging.error(f"Unexpected error processing {entry.path}: {e}")

    except (PermissionError, FileNotFoundError) as e:
        logging.warning(f"Cannot scan directory {dir_path}: {e}")

    return processed, skipped_unhandled, unhandled_paths

class ScanJob(NamedTuple):
    path: Path
    recursive: bool


class SweepResult(NamedTuple):
    processed: int
    skipped: int
    unhandled: list[str]


def deduplicate_roots(paths: list[str]) -> list[Path]:
    """
    Resolves the given top-level paths and drops duplicates and paths nested
    inside another given path, so no subtree is scanned more than once.
    """
    resolved = []
    for root_path in paths:
        root = Path(root_path).resolve()
        if not root.exists():
            logging.error(f"Root path does not exist: {root}")
            continue
        if root not in resolved:
            resolved.append(root)

    roots = []
    for root in resolved:
        enclosing = next((other for other in resolved if other in root.parents), None)
        if enclosing is not None:
            logging.warning(f"Skipping {root}: already covered by {enclosing}")
            continue
        roots.append(root)
    return roots


def collect_scan_jobs(roots: list[Path], ignored_dirs: set) -> list[ScanJob]:
    """
    Splits each root into one recursive job per non-ignored subdirectory, plus a
    single non-recursive job for the files located directly in the root.
    """
    jobs = []
    for root in roots:
        has_top_level_files = False
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name not in ignored_dirs:
                            jobs.append(ScanJob(Path(entry.path), recursive=True))
                    elif entry.is_file(follow_symlinks=False):
                        has_top_level_files = True
        except Exception as e:
            logging.error(f"Error reading root path {root}: {e}")
            continue
        if has_top_level_files:
            jobs.append(ScanJob(root, recursive=False))
    return jobs


def sweep(paths: list[str], days: int, ignored_dirs: set, dry_run: bool, workers: int) -> SweepResult:
    cutoff_time = time.time() - (days * 86400)
    jobs = collect_scan_jobs(deduplicate_roots(paths), ignored_dirs)

    total_processed = 0
    total_skipped = 0
    all_unhandled = []

    # Parallel execution using ProcessPoolExecutor
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(process_directory, job.path, cutoff_time, ignored_dirs, dry_run, job.recursive): job
            for job in jobs
        }

        for future in as_completed(futures):
            job = futures[future]
            try:
                proc, skip, unhandled = future.result()
                total_processed += proc
                total_skipped += skip
                all_unhandled.extend(unhandled)
            except Exception as e:
                logging.error(f"Subtask failed for {job.path}: {e}")

    return SweepResult(total_processed, total_skipped, all_unhandled)


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Exhaustively traverse and touch/access stale files older than 1 year.")
    parser.add_argument("--paths", nargs="+", required=True, help="Top-level folder paths to scan.")
    parser.add_argument("--days", type=int, default=365, help="Access age threshold in days (default: 365).")
    parser.add_argument("--log-file", default="stale_file_access.log", help="Path to log file.")
    parser.add_argument("--ignore-dir", nargs="*", default=[], help="Additional directories to ignore.")
    parser.add_argument("--dry-run", action="store_true", help="Scan and log without actually accessing files.")
    parser.add_argument("--workers", type=int, default=4, help="Number of parallel worker processes.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")

    args = parser.parse_args(argv)

    setup_logging(args.log_file, args.verbose)

    ignored_dirs = DEFAULT_IGNORED_DIRS.union(set(args.ignore_dir))

    logging.info(f"Starting stale file sweep. Threshold: {args.days} days. Dry run: {args.dry_run}")
    logging.info(f"Top-level paths: {args.paths}")
    logging.info(f"Ignored directories: {ignored_dirs}")

    start_time = time.time()
    result = sweep(args.paths, args.days, ignored_dirs, args.dry_run, args.workers)
    duration = time.time() - start_time

    logging.info("=== Sweep Completed ===")
    logging.info(f"Duration: {duration:.2f} seconds")
    logging.info(f"Files successfully accessed: {result.processed}")
    logging.info(f"Unhandled/skipped files logged: {result.skipped}")
    if result.unhandled:
        unhandled_log = "unhandled_stale_files.txt"
        with open(unhandled_log, "w", encoding="utf-8") as f:
            f.write("\n".join(result.unhandled) + "\n")
        logging.info(f"Unhandled file paths written to {unhandled_log}")

if __name__ == "__main__":
    main()
