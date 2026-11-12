import csv
import time

from tree_sweeper.analyze_stale_files import (
    generate_report,
    is_stale_list,
    parse_paths_from_file,
    print_markdown_report,
    read_stale_list,
    summarize_records,
)

DAY = 86400


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


def write_stale_list(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["path", "size_bytes", "atime_epoch", "atime_iso", "status"])
        writer.writerows(rows)


def test_stale_list_report_uses_recorded_size_and_atime(tmp_path):
    now = time.time()
    stale_list = tmp_path / "stale.csv"
    write_stale_list(stale_list, [
        ["/gone/a.h5", 100, now - 100 * DAY, "", "accessed"],
        ["/gone/b.h5", 200, now - 400 * DAY, "", "accessed"],
        ["/gone/with, comma.mp4", 300, now - 6 * 365 * DAY, "", "failed"],
    ])

    assert is_stale_list(stale_list)
    report = summarize_records(read_stale_list(stale_list))

    assert report["total_files"] == 3
    assert report["total_missing"] == 0
    assert report["grand_total_size"] == 600
    assert {k: v["count"] for k, v in report["age_brackets"].items()} == {
        "< 1 year": 1, "1 - 2 years": 1, "> 5 years": 1,
    }
    assert report["ext_stats"][".mp4"]["total_size"] == 300


def test_log_file_is_not_detected_as_stale_list(tmp_path):
    log = tmp_path / "sweep.log"
    log.write_text("2026-11-11 23:20:08,920 [INFO] [PID 1] Starting stale file sweep.\n")

    assert not is_stale_list(log)


def test_age_brackets_are_listed_from_youngest_to_oldest(tmp_path):
    now = time.time()
    records = read_stale_list_rows(tmp_path, [now - d * DAY for d in (6 * 365, 100, 2.5 * 365, 400, 4 * 365)])
    output = tmp_path / "report.md"

    print_markdown_report(summarize_records(records), str(output))

    lines = output.read_text().splitlines()
    start = lines.index("## Breakdown by Age Bracket") + 3
    assert [line.split(" | ")[0].lstrip("| ") for line in lines[start:start + 5]] == [
        "< 1 year", "1 - 2 years", "2 - 3 years", "3 - 5 years", "> 5 years",
    ]


def read_stale_list_rows(tmp_path, atimes):
    stale_list = tmp_path / "stale.csv"
    write_stale_list(stale_list, [[f"/x/{i}.dat", 1, atime, "", "accessed"] for i, atime in enumerate(atimes)])
    return list(read_stale_list(stale_list))
