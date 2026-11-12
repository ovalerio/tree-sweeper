# GPFS Stale File Sweeper & Analyzer

A high-performance Python toolkit designed for high-performance computing (HPC) and GPFS environments to traverse large directory structures, identify files untouched over a specified threshold (e.g., 1 year), safely refresh their access times (`atime`) depending on file type, and generate detailed analytical reports.

---

## Architecture & How It Works

The toolkit is split into two complementary pipelines:

1. **The Sweeper (`touch_stale_files.py`)**:
* Recursively traverses top-level directory trees using high-performance system calls (`os.scandir`).
* Distributes subdirectories across multiple worker processes (`ProcessPoolExecutor`) for parallel execution. Files located directly in a top-level path are handled by a separate non-recursive job, and duplicate or nested `--paths` are collapsed, so every file is visited exactly once.
* Filters out common version control directories, build caches, and virtual environments.
* Checks file access time (`st_atime`) against a configurable age threshold (default: 365 days).
* Safely "touches" stale files by reading a small 4KB chunk (`buffering=0`) to trigger filesystem-level `atime` updates without loading heavy files into memory.
* Logs unhandled or inaccessible files to a dedicated output file.
* Optionally (`--stale-list`) saves every stale file found to a CSV file, together with its **original** size and access time recorded *before* the file is touched, so a single sweep both refreshes the files and provides the data for an accurate report.


2. **The Analyzer (`analyze_stale_files.py`)**:
* Reads the stale file list CSV written by the sweeper with `--stale-list`, using the original sizes and access times recorded in it.
* Alternatively parses the logs or unhandled file lists generated during the sweep, and queries live file system metadata (`os.stat`) to extract file sizes and last-accessed timestamps.
* Aggregates and categorizes the data into **Age Brackets**, **Size Brackets**, and **Top File Extensions**.
* Outputs a clean, structured Markdown report (`stale_files_report.md`).



---

## Recommended Default Exclusions

To protect internal tool states, virtual environments, and build artifacts, the sweeper ignores the following directories and file patterns by default:

* **Version Control & IDEs:** `.git`, `.svn`, `.hg`, `.vscode`, `.idea`
* **Caches & Build Artifacts:** `.cache`, `__pycache__`, `.pytest_cache`, `node_modules`, `build`, `dist`
* **Environments & Containers:** `.conda`, `miniconda`, `anaconda`, `venv`, `.venv`, `.apptainer`, `.singularity`
* **System & Snapshots:** `.snapshot`, `.snapshots`, `.Trash-*`, `lost+found`
* **Temporary Files:** `.lock`, `.tmp`, `.log`, `.swp`, `.swo`

---

## Installation & Requirements

* **Python:** 3.8 or higher
* **Dependencies:** Standard library only (`argparse`, `pathlib`, `concurrent.futures`, `os`, `logging`, `mimetypes`, `collections`). No external pip packages required.

The scripts can be run directly from a clone of the repository, or the toolkit can be installed as a package, which provides the `touch-stale-files` and `analyze-stale-files` commands:

```bash
pip install .                                            # from a local clone
pip install git+https://github.com/ovalerio/tree-sweeper.git   # without cloning
```

### Running the Tests

The test suite uses `pytest` and can be run from the repository root, with or without installing the package:

```bash
pip install -e ".[test]"   # optional: editable install including pytest
python -m pytest
```

---

## Script Parameters & Usage

### 1. The Sweeper Script (`touch_stale_files.py`)

```bash
python3 touch_stale_files.py --paths <PATH_1> [PATH_2 ...] [OPTIONS]
# or, when installed as a package:
touch-stale-files --paths <PATH_1> [PATH_2 ...] [OPTIONS]

```

* `--paths`: (Required) One or more top-level directory paths to scan.
* `--days`: Access age threshold in days (default: `365`).
* `--log-file`: Path to save the general execution log (default: `stale_file_access.log`).
* `--ignore-dir`: Additional directory names to exclude from the traversal.
* `--dry-run`: Scan and log matching files without modifying their access times.
* `--workers`: Number of parallel worker processes for multi-core execution (default: `4`).
* `--stale-list`: Write every stale file found to this CSV file, with its original size and access time (recorded before the file is accessed). The file is overwritten on each run. See [Stale File List Format](#stale-file-list-format).
* `--verbose`: Enable debug-level logging.

**Example (Dry Run with 16 Workers):**

```bash
python3 touch_stale_files.py --paths /gpfs/home/user /gpfs/shared/project --days 365 --workers 16 --dry-run

```

### 2. Running as a Slurm Batch Job

For large storage pools, you can submit the sweeper as a batch job on an HPC cluster:

```bash
#!/bin/bash
#SBATCH --job-name=stale-file-sweeper
#SBATCH --output=stale-sweeper-%j.out
#SBATCH --cpus-per-task=16
#SBATCH --time=04:00:00
#SBATCH --mem=8G

python3 touch_stale_files.py \
    --paths /gpfs/home/user \
    --days 365 \
    --workers 16 \
    --log-file stale_sweep.log \
    --stale-list stale_files.csv

```

### 3. The Analyzer Script (`analyze_stale_files.py`)

```bash
python3 analyze_stale_files.py --input <LOG_OR_TXT_FILE> [OPTIONS]
# or, when installed as a package:
analyze-stale-files --input <LOG_OR_TXT_FILE> [OPTIONS]

```

* `--input`: (Required) Path to a stale file list CSV (`--stale-list`), `unhandled_stale_files.txt`, or an execution log generated by the sweeper. The CSV format is detected automatically from its header line.
* `--output`: Path to save the generated Markdown report (default: `stale_files_report.md`).

**Example:**

```bash
python3 analyze_stale_files.py --input stale_files.csv --output stale_files_report.md

```

---

## Recommended Workflow

### Single run: refresh the files and report on them

Run the real sweep with `--stale-list`, then build the report from that CSV file:

```bash
touch-stale-files --paths /gpfs/shared/project --days 365 --workers 16 \
    --log-file project_sweep.log --stale-list project_stale_files.csv
analyze-stale-files --input project_stale_files.csv --output project_stale_report.md
```

The CSV file holds the size and access time of each stale file as they were *before* the sweep touched it, so the report shows how old the data really was.

### Report only, without touching anything

Use `--dry-run` when you want to see what would be refreshed without modifying any access time. The dry-run log lists every stale file and can be analyzed directly (or combine `--dry-run` with `--stale-list`):

```bash
touch-stale-files --paths /gpfs/shared/project --days 365 --workers 16 \
    --dry-run --log-file project_dryrun.log
analyze-stale-files --input project_dryrun.log --output project_dryrun_report.md
```

### Things to keep in mind

* **Do not analyze the log of a real sweep.** Without `--dry-run`, the files that were successfully accessed are only logged at debug level (`--verbose`), so the log contains no paths to analyze. Even with `--verbose`, the analyzer would read the *refreshed* access times from the file system and report every file as less than 1 year old. Use `--stale-list` instead.
* **Log files are appended to, not overwritten.** Running the sweeper several times with the same `--log-file` (including the default `stale_file_access.log`) accumulates the entries of all runs in one file, and the analyzer will process all of them. Use a different `--log-file` for each run, e.g. one for the dry run and another for the real sweep.
* **`unhandled_stale_files.txt` is overwritten** on every run and written to the current working directory. It lists the stale files that could not be accessed (e.g. `Permission denied`); since those files were not touched, it can be analyzed after a real sweep. The `--stale-list` CSV is also overwritten on each run, and marks these files with the status `failed`.

### Stale File List Format

The `--stale-list` file is a CSV file with a header line and one row per stale file:

| Column | Description |
| :--- | :--- |
| `path` | Absolute path of the file. |
| `size_bytes` | File size in bytes. |
| `atime_epoch` | Original access time, in seconds since the epoch. |
| `atime_iso` | Original access time, in local time (`YYYY-MM-DD HH:MM:SS`). |
| `status` | `accessed`, `failed` (could not be read), `dry_run`, or `no_handler` (no access handler for the file type). |

---

## Interpreting the Results

The generated Markdown report (`stale_files_report.md`) provides actionable insights into your storage usage:

* **Age Breakdown:** Groups files into `< 1 year`, `1 - 2 years`, `2 - 3 years`, `3 - 5 years` and `> 5 years` brackets, identifying whether stale data is recently abandoned or contains multi-year abandoned artifacts. Files in the `< 1 year` bracket only appear when using a `--days` threshold below 365, or when analyzing paths whose access time was already refreshed.
* **Size Brackets:** Highlights heavy data hogs (e.g., files greater than 10 GB) compared to tiny log or config files.
* **Extension Distribution:** Pinpoints specific file formats (e.g., `.h5`, `.nii`, `.tiff`, `.bag`) that dominate cold storage, helping you decide whether to migrate them to tape or object storage tiers.

---

## Roadmap & Future Extensions

* [ ] **Slurm Array Job Wrapper:** Develop an orchestration wrapper script that automatically splits top-level subdirectories across parallel Slurm array tasks to traverse petabyte-scale GPFS directory trees efficiently.
* [ ] **JSON Metrics Export:** Add a `--json-export` flag to the analyzer script to output structured JSON metrics alongside the Markdown report for automated ingestion into monitoring dashboards (e.g., Grafana / Prometheus).
* [ ] **Directory-Level Aggregation:** Extend the analysis engine to group stale files by top-level subfolders to instantly pinpoint which user projects or shared directories consume the most archival storage.

---