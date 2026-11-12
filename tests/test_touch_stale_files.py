import csv
import os
import subprocess
import sys
from pathlib import Path

import pytest

import tree_sweeper
from tree_sweeper.touch_stale_files import DEFAULT_IGNORED_DIRS, format_name_list, sweep

from conftest import STALE_ATIME


def run_sweep(paths, dry_run=True, ignored_dirs=DEFAULT_IGNORED_DIRS, stale_list=None):
    return sweep([str(p) for p in paths], days=365, ignored_dirs=ignored_dirs, dry_run=dry_run, workers=2,
                 stale_list=stale_list)


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_format_name_list_ignores_case_and_special_characters():
    names = {"venv", ".venv", "__pycache__", ".Trash", "build", "lost+found", ".snapshots", ".snapshot"}

    assert format_name_list(names) == (
        "build, lost+found, __pycache__, .snapshot, .snapshots, .Trash, .venv, venv"
    )


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


def test_stale_list_records_original_size_and_atime_before_access(tmp_path, make_file):
    root = tmp_path / "root"
    make_file(root / "top.dat", content=b"x" * 10_000)
    make_file(root / "sub1" / "a.txt", content=b"abc")
    make_file(root / "sub2" / "nested" / "b.txt")
    make_file(root / "sub2" / "fresh.txt", stale=False)
    make_file(root / "sub2" / "data.tmp")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    stale_list = out_dir / "stale.csv"

    result = run_sweep([root], dry_run=False, stale_list=str(stale_list))

    rows = {Path(r["path"]).name: r for r in read_csv(stale_list)}
    assert result.processed == 3
    assert set(rows) == {"top.dat", "a.txt", "b.txt"}
    assert {r["status"] for r in rows.values()} == {"accessed"}
    assert all(float(r["atime_epoch"]) == STALE_ATIME for r in rows.values())
    assert rows["top.dat"]["size_bytes"] == "10000"
    assert rows["a.txt"]["size_bytes"] == "3"
    assert [p.name for p in out_dir.iterdir()] == ["stale.csv"]


def test_stale_list_marks_files_that_could_not_be_accessed(tmp_path, make_file):
    if os.geteuid() == 0:
        pytest.skip("root can read files regardless of permissions")
    unreadable = make_file(tmp_path / "root" / "sub" / "unreadable.txt")
    unreadable.chmod(0)
    stale_list = tmp_path / "stale.csv"

    try:
        run_sweep([tmp_path / "root"], dry_run=False, stale_list=str(stale_list))
    finally:
        unreadable.chmod(0o644)

    rows = read_csv(stale_list)
    assert [(Path(r["path"]).name, r["status"]) for r in rows] == [("unreadable.txt", "failed")]


def test_cli_dry_run(tmp_path, make_file):
    root = tmp_path / "root"
    make_file(root / "top.txt")
    make_file(root / "sub" / "a.txt")
    log_file = tmp_path / "sweep.log"
    src_dir = Path(tree_sweeper.__file__).resolve().parent.parent
    env = {**os.environ, "PYTHONPATH": str(src_dir)}

    completed = subprocess.run(
        [sys.executable, "-m", "tree_sweeper.touch_stale_files",
         "--paths", str(root), "--days", "365", "--workers", "2", "--dry-run", "--log-file", str(log_file),
         "--stale-list", "stale.csv"],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )

    assert completed.returncode == 0, completed.stderr
    log = log_file.read_text()
    assert log.count("[DRY-RUN] Would access stale file") == 2
    assert "Files successfully accessed: 2" in log
    assert [r["status"] for r in read_csv(tmp_path / "stale.csv")] == ["dry_run", "dry_run"]


def test_cli_rejects_stale_list_in_missing_directory(tmp_path):
    src_dir = Path(tree_sweeper.__file__).resolve().parent.parent
    env = {**os.environ, "PYTHONPATH": str(src_dir)}

    completed = subprocess.run(
        [sys.executable, "-m", "tree_sweeper.touch_stale_files",
         "--paths", str(tmp_path), "--stale-list", str(tmp_path / "missing" / "stale.csv")],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )

    assert completed.returncode == 2
    assert "--stale-list" in completed.stderr
    assert not (tmp_path / "stale_file_access.log").exists()
