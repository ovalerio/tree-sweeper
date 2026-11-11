from tree_sweeper.analyze_stale_files import generate_report, parse_paths_from_file


def test_parse_paths_from_list_and_log_lines(tmp_path):
    input_file = tmp_path / "input.txt"
    input_file.write_text(
        "/data/plain/path.txt\n"
        "2026-11-11 13:17:59,215 [INFO] [PID 1] [DRY-RUN] Would access stale file: /data/dry run.h5\n"
        "2026-11-11 13:17:59,216 [WARNING] [PID 1] Could not determine file type/handler for: /data/x.bin\n"
        "2026-11-11 13:17:59,218 [INFO] [PID 1] === Sweep Completed ===\n"
    )

    paths = parse_paths_from_file(input_file)

    assert [str(p) for p in paths] == ["/data/plain/path.txt", "/data/dry run.h5", "/data/x.bin"]


def test_generate_report_counts_existing_and_missing_files(tmp_path, make_file):
    existing = [make_file(tmp_path / "a.h5", content=b"x" * 10), make_file(tmp_path / "b.h5", content=b"y" * 5)]

    report = generate_report(existing + [tmp_path / "missing.txt"])

    assert report["total_files"] == 2
    assert report["total_missing"] == 1
    assert report["grand_total_size"] == 15
    assert report["ext_stats"][".h5"]["count"] == 2
    assert sum(b["count"] for b in report["age_brackets"].values()) == 2
