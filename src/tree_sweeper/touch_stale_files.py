#!/usr/bin/env python3
"""
GPFS Stale File Accesser & Logger
Traverses directory trees, identifies files untouched for > 1 year,
safely accesses them based on file type, and logs unhandled files.
Parallelized using ProcessPoolExecutor for multi-core batch processing.
"""

import argparse
import logging
import mimetypes
from pathlib import Path
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
import os
import sys

# Default directories and file patterns to ignore
DEFAULT_IGNORED_DIRS = {
    ".git", ".svn", ".hg", ".vscode", ".idea", ".cache", 
    "__pycache__", ".pytest_cache", "node_modules", "build", 
    "dist", ".conda", ".miniconda", ".anaconda", "venv", 
    ".venv", ".apptainer", ".singularity", ".snapshot", "lost+found"
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

def process_directory(dir_path: Path, cutoff_time: float, ignored_dirs: set, dry_run: bool) -> tuple[int, int, list[str]]:
    """
    Walks a single directory subtree, checking file access times.
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
                        if entry.name in ignored_dirs:
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

def main():
    parser = argparse.ArgumentParser(description="Exhaustively traverse and touch/access stale files older than 1 year.")
    parser.add_argument("--paths", nargs="+", required=True, help="Top-level folder paths to scan.")
    parser.add_argument("--days", type=int, format=int, default=365, help="Access age threshold in days (default: 365).")
    parser.add_argument("--log-file", default="stale_file_access.log", help="Path to log file.")
    parser.add_argument("--ignore-dir", nargs="*", default=[], help="Additional directories to ignore.")
    parser.add_argument("--dry-run", action="store_true", help="Scan and log without actually accessing files.")
    parser.add_argument("--workers", type=int, default=4, help="Number of parallel worker processes.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")

    args = parser.parse_args()

    setup_logging(args.log_file, args.verbose)

    ignored_dirs = DEFAULT_IGNORED_DIRS.union(set(args.ignore_dir))
    cutoff_time = time.time() - (args.days * 86400)

    logging.info(f"Starting stale file sweep. Threshold: {args.days} days. Dry run: {args.dry_run}")
    logging.info(f"Top-level paths: {args.paths}")
    logging.info(f"Ignored directories: {ignored_dirs}")

    # Gather top-level subdirectories to distribute work across worker processes
    subdirectories_to_scan = []
    for root_path in args.paths:
        root = Path(root_path).resolve()
        if not root.exists():
            logging.error(f"Root path does not exist: {root}")
            continue
        
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name not in ignored_dirs:
                            subdirectories_to_scan.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        # Handle top-level files directly in root
                        subdirectories_to_scan.append(root) # Handled collectively or as pseudo-batch
                        break
        except Exception as e:
            logging.error(f"Error reading root path {root}: {e}")

    total_processed = 0
    total_skipped = 0
    all_unhandled = []

    # Parallel execution using ProcessPoolExecutor
    start_time = time.time()
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(process_directory, subdir, cutoff_time, ignored_dirs, args.dry_run): subdir
            for subdir in subdirectories_to_scan
        }

        for future in as_completed(futures):
            subdir = futures[future]
            try:
                proc, skip, unhandled = future.result()
                total_processed += proc
                total_skipped += skip
                all_unhandled.extend(unhandled)
            except Exception as e:
                logging.error(f"Subtask failed for {subdir}: {e}")

    duration = time.time() - start_time
    logging.info("=== Sweep Completed ===")
    logging.info(f"Duration: {duration:.2f} seconds")
    logging.info(f"Files successfully accessed: {total_processed}")
    logging.info(f"Unhandled/skipped files logged: {total_skipped}")
    if all_unhandled:
        unhandled_log = "unhandled_stale_files.txt"
        with open(unhandled_log, "w", encoding="utf-8") as f:
            f.write("\n".join(all_unhandled) + "\n")
        logging.info(f"Unhandled file paths written to {unhandled_log}")

if __name__ == "__main__":
    main()