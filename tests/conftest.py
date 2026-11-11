import os
import time
from pathlib import Path

import pytest

STALE_ATIME = time.mktime((2020, 1, 1, 0, 0, 0, 0, 0, -1))


@pytest.fixture
def make_file():
    """Creates a file (and its parents); stale files get an atime of 2020-01-01."""

    def _make_file(path: Path, stale: bool = True, content: bytes = b"data") -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        if stale:
            os.utime(path, (STALE_ATIME, path.stat().st_mtime))
        return path

    return _make_file
