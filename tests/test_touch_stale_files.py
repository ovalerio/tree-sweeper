import os
import subprocess
import sys
from pathlib import Path

import pytest

import tree_sweeper
from tree_sweeper.touch_stale_files import DEFAULT_IGNORED_DIRS, sweep

from conftest import STALE_ATIME


def run_sweep(paths, dry_run=True, ignored_dirs=DEFAULT_IGNORED_DIRS):
    return sweep([str(p) for p in paths], days=365, ignored_dirs=ignored_dirs, dry_run=dry_run, workers=2)


def test_dry_run_counts_stale_file_without_accessing_it(tmp_path, make_file):
    stale = make_file(tmp_path / "root" / "sub" / "old.txt")
    make_file(tmp_path / "root" / "sub" / "fresh.txt", stale=False)

    result = run_sweep([tmp_path / "root"])

    assert result.processed == 1
    assert result.skipped == 0
    assert stale.stat().st_atime == STALE_ATIME


def test_files_in_root_and_subdirs_are_counted_once(tmp_path, make_file):
    root = tmp_path / "root"
    make_file(root / "top_a.txt")
    make_file(root / "top_b.txt")
    for name in ("sub1", "sub2", "sub3"):
        make_file(root / name / "a.txt")
        make_file(root / name / "nested" / "b.txt")

    result = run_sweep([root])

    assert result.processed == 2 + 3 * 2


def test_overlapping_and_duplicate_paths_are_counted_once(tmp_path, make_file):
    root = tmp_path / "root"
    make_file(root / "top.txt")
    make_file(root / "sub" / "a.txt")
    make_file(root / "sub" / "nested" / "b.txt")

    result = run_sweep([root / "sub", root, root / "sub" / "nested", root])

    assert result.processed == 3


def test_ignored_dirs_and_extensions_are_skipped(tmp_path, make_file):
    root = tmp_path / "root"
    make_file(root / ".git" / "objects" / "x")
    make_file(root / "sub" / "node_modules" / "pkg.js")
    make_file(root / "sub" / "custom_scratch" / "y.dat")
    make_file(root / "sub" / "run.log")
    make_file(root / "sub" / "keep.dat")

    result = run_sweep([root], ignored_dirs=DEFAULT_IGNORED_DIRS | {"custom_scratch"})

    assert result.processed == 1


def test_access_reads_stale_files_and_reports_unreadable_ones(tmp_path, make_file):
    if os.geteuid() == 0:
        pytest.skip("root can read files regardless of permissions")
    root = tmp_path / "root"
    make_file(root / "sub" / "readable.txt")
    unreadable = make_file(root / "sub" / "unreadable.txt")
    unreadable.chmod(0)

    try:
        result = run_sweep([root], dry_run=False)
    finally:
        unreadable.chmod(0o644)

    assert result.processed == 1
    assert result.skipped == 1
    assert result.unhandled == [str(unreadable.resolve())]


def test_cli_dry_run(tmp_path, make_file):
    root = tmp_path / "root"
    make_file(root / "top.txt")
    make_file(root / "sub" / "a.txt")
    log_file = tmp_path / "sweep.log"
    src_dir = Path(tree_sweeper.__file__).resolve().parent.parent
    env = {**os.environ, "PYTHONPATH": str(src_dir)}

    completed = subprocess.run(
        [sys.executable, "-m", "tree_sweeper.touch_stale_files",
         "--paths", str(root), "--days", "365", "--workers", "2", "--dry-run", "--log-file", str(log_file)],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )

    assert completed.returncode == 0, completed.stderr
    log = log_file.read_text()
    assert log.count("[DRY-RUN] Would access stale file") == 2
    assert "Files successfully accessed: 2" in log
