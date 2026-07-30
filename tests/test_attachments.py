import os
import time
from pathlib import Path
from typing import Optional

import pytest

import attachments


def _touch(path: Path, mtime_offset: float = 0.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    atime = time.time() + mtime_offset
    os.utime(path, (atime, atime))


@pytest.fixture
def tmp_attachments(tmp_path: Path):
    return tmp_path / "multimodal-attachments"


def test_list_attachments_filters_by_suffix(tmp_attachments: Path) -> None:
    _touch(tmp_attachments / "a.png")
    _touch(tmp_attachments / "b.txt")
    _touch(tmp_attachments / "c.jpg")
    _touch(tmp_attachments / "d.webp")
    result = attachments.list_attachments(tmp_attachments)
    assert set(result) == {
        tmp_attachments / "a.png",
        tmp_attachments / "c.jpg",
        tmp_attachments / "d.webp",
    }


def test_list_attachments_ignores_missing_directory(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    assert attachments.list_attachments(missing) == []


def test_select_pasted_images_returns_paste_order(tmp_attachments: Path) -> None:
    # Save in paste order: A, B, C. A has the oldest mtime, C the newest.
    _touch(tmp_attachments / "A.png", mtime_offset=-0.3)
    _touch(tmp_attachments / "B.png", mtime_offset=-0.2)
    _touch(tmp_attachments / "C.png", mtime_offset=-0.1)
    selected, err = attachments.select_pasted_images(3, tmp_attachments)
    assert err is None
    assert [p.name for p in selected] == ["A.png", "B.png", "C.png"]


def test_select_pasted_images_newest_n_only(tmp_attachments: Path) -> None:
    _touch(tmp_attachments / "old.png", mtime_offset=-1.0)
    _touch(tmp_attachments / "A.png", mtime_offset=-0.3)
    _touch(tmp_attachments / "B.png", mtime_offset=-0.2)
    _touch(tmp_attachments / "C.png", mtime_offset=-0.1)
    selected, err = attachments.select_pasted_images(2, tmp_attachments)
    assert err is None
    assert [p.name for p in selected] == ["B.png", "C.png"]


def test_select_pasted_images_tie_break_by_name(tmp_attachments: Path) -> None:
    now = time.time()
    _touch(tmp_attachments / "b.png")
    _touch(tmp_attachments / "a.png")
    # Force identical mtimes to verify deterministic tie-break.
    os.utime(tmp_attachments / "b.png", (now, now))
    os.utime(tmp_attachments / "a.png", (now, now))
    selected, err = attachments.select_pasted_images(2, tmp_attachments)
    assert err is None
    assert [p.name for p in selected] == ["a.png", "b.png"]


def test_select_pasted_images_rejects_invalid_count(tmp_attachments: Path) -> None:
    for bad in (0, 9, -1):
        selected, err = attachments.select_pasted_images(bad, tmp_attachments)
        assert selected == []
        assert "must be between 1 and 8" in (err or "")


def test_select_pasted_images_error_when_empty(tmp_attachments: Path) -> None:
    selected, err = attachments.select_pasted_images(1, tmp_attachments)
    assert selected == []
    assert "no supported image files" in (err or "")


def test_select_pasted_images_error_when_too_few(tmp_attachments: Path) -> None:
    _touch(tmp_attachments / "only.png")
    selected, err = attachments.select_pasted_images(3, tmp_attachments)
    assert selected == []
    assert "only 1 available" in (err or "")
