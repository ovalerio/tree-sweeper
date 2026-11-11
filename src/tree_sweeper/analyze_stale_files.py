#!/usr/bin/env python3
"""
Stale File Analysis & Report Generator
Parses log files or unhandled file lists, inspects file metadata (size, atime),
and outputs a comprehensive breakdown report by extension, size bracket, and age bracket.
"""

import argparse
from collections import defaultdict
import datetime
from pathlib import Path
import re
import sys
import time

def parse_paths_from_file(file_path: Path) -> list[Path]:
    """
    Extracts file paths from either an unhandled files list or general log output.
    Supports plain lists of paths or log lines containing paths.
    """
    paths = []
    path_pattern = re.compile(r'(?:Accessed stale file:|Would access stale file:|Could not determine file type/handler for:)\s+(/.+)')

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                # Check if it's a direct path line
                if line.startswith("/"):
                    paths.append(Path(line))
                else:
                    # Try matching log format
                    match = path_pattern.search(line)
                    if match:
                        paths.append(Path(match.group(1)))
    except Exception as e:
        print(f"Error reading input file {file_path}: {e}", file=sys.stderr)
        sys.exit(1)

    return paths

def categorize_size(size_bytes: int) -> str:
    mb = size_bytes / (1024 * 1024)
    gb = mb / 1024
    if gb >= 10:
        return "> 10 GB"
    elif gb >= 1:
        return "1 GB - 10 GB"
    elif mb >= 100:
        return "100 MB - 1 GB"
    elif mb >= 1:
        return "1 MB - 100 MB"
    else:
        return "< 1 MB"

def categorize_age(days: float) -> str:
    years = days / 365.25
    if years >= 5:
        return "> 5 years"
    elif years >= 3:
        return "3 - 5 years"
    elif years >= 2:
        return "2 - 3 years"
    else:
        return "1 - 2 years"

def format_size(size_bytes: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"

def generate_report(paths: list[Path]) -> dict:
    ext_stats = defaultdict(lambda: {"count": 0, "total_size": 0})
    size_brackets = defaultdict(lambda: {"count": 0, "total_size": 0})
    age_brackets = defaultdict(lambda: {"count": 0, "total_size": 0})
    
    total_files = 0
    total_missing = 0
    grand_total_size = 0
    current_time = time.time()

    for p in paths:
        try:
            stat = p.stat(follow_symlinks=False)
            size = stat.st_size
            atime = stat.st_atime
            age_days = (current_time - atime) / 86400

            total_files += 1
            grand_total_size += size

            # Extension
            ext = p.suffix.lower() if p.suffix else "(no extension)"
            ext_stats[ext]["count"] += 1
            ext_stats[ext]["total_size"] += size

            # Size Bracket
            s_bracket = categorize_size(size)
            size_brackets[s_bracket]["count"] += 1
            size_brackets[s_bracket]["total_size"] += size

            # Age Bracket
            a_bracket = categorize_age(age_days)
            age_brackets[a_bracket]["count"] += 1
            age_brackets[a_bracket]["total_size"] += size

        except (FileNotFoundError, PermissionError):
            total_missing += 1
        except Exception:
            total_missing += 1

    return {
        "total_files": total_files,
        "total_missing": total_missing,
        "grand_total_size": grand_total_size,
        "ext_stats": dict(ext_stats),
        "size_brackets": dict(size_brackets),
        "age_brackets": dict(age_brackets),
    }

def print_markdown_report(report: dict, output_file: str = None):
    output = []
    output.append("# Stale File Analysis Report\n")
    output.append(f"**Generated on:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    output.append(f"- **Successfully Analyzed Files:** {report['total_files']:,}")
    output.append(f"- **Missing / Inaccessible Files:** {report['total_missing']:,}")
    output.append(f"- **Total Cumulative Size:** {format_size(report['grand_total_size'])}\n")

    # Age Brackets
    output.append("## Breakdown by Age Bracket")
    output.append("| Age Bracket | File Count | Percentage | Total Size |")
    output.append("| :--- | ---: | ---: | ---: |")
    total_files = report["total_files"] or 1
    for bracket in sorted(report["age_brackets"].keys()):
        data = report["age_brackets"][bracket]
        pct = (data["count"] / total_files) * 100
        output.append(f"| {bracket} | {data['count']:,} | {pct:.1f}% | {format_size(data['total_size'])} |")
    output.append("")

    # Size Brackets
    output.append("## Breakdown by Size Bracket")
    output.append("| Size Bracket | File Count | Percentage | Total Size |")
    output.append("| :--- | ---: | ---: | ---: |")
    size_order = ["< 1 MB", "1 MB - 100 MB", "100 MB - 1 GB", "1 GB - 10 GB", "> 10 GB"]
    for bracket in size_order:
        if bracket in report["size_brackets"]:
            data = report["size_brackets"][bracket]
            pct = (data["count"] / total_files) * 100
            output.append(f"| {bracket} | {data['count']:,} | {pct:.1f}% | {format_size(data['total_size'])} |")
    output.append("")

    # Extension Breakdown (Top 20)
    output.append("## Top File Extensions by Count")
    output.append("| Extension | File Count | Total Size |")
    output.append("| :--- | ---: | ---: |")
    sorted_exts = sorted(report["ext_stats"].items(), key=lambda x: x[1]["count"], reverse=True)[:20]
    for ext, data in sorted_exts:
        output.append(f"| `{ext}` | {data['count']:,} | {format_size(data['total_size'])} |")
    output.append("")

    report_text = "\n".join(output)
    print(report_text)

    if output_file:
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(report_text)
        print(f"\nReport successfully written to {output_file}")

def main():
    parser = argparse.ArgumentParser(description="Analyze stale files from sweeper logs or path lists.")
    parser.add_argument("--input", required=True, help="Path to unhandled_stale_files.txt or sweep log file.")
    parser.add_argument("--output", default="stale_files_report.md", help="Path to save Markdown report.")

    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Parsing paths from {input_path}...")
    paths = parse_paths_from_file(input_path)
    print(f"Extracted {len(paths):,} paths. Inspecting file system metadata...")

    report = generate_report(paths)
    print_markdown_report(report, args.output)

if __name__ == "__main__":
    main()