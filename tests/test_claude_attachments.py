import os
import time
from pathlib import Path

import pytest

import claude_attachments


def _write_image(path: Path, data: bytes = b"png", mtime_offset: float = 0.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    atime = time.time() + mtime_offset
    os.utime(path, (atime, atime))


@pytest.fixture(autouse=True)
def _clear_session_env(monkeypatch) -> None:
    """Keep tests deterministic: this env var is set under real Claude Code."""
    monkeypatch.delenv(claude_attachments.SESSION_ID_ENV, raising=False)


@pytest.fixture
def cache_root(tmp_path: Path) -> Path:
    return tmp_path / "image-cache"


# --------------------------------------------------------------------------- #
# resolve_session_dir                                                         #
# --------------------------------------------------------------------------- #
def test_resolve_session_dir_prefers_explicit_session_id(cache_root: Path) -> None:
    _write_image(cache_root / "session-a" / "1.png")
    _write_image(cache_root / "session-b" / "1.png")

    directory, err = claude_attachments.resolve_session_dir("session-a", cache_root)
    assert err is None
    assert directory == cache_root / "session-a"


def test_resolve_session_dir_uses_env_var(monkeypatch, cache_root: Path) -> None:
    _write_image(cache_root / "env-session" / "1.png")
    monkeypatch.setenv(claude_attachments.SESSION_ID_ENV, "env-session")

    directory, err = claude_attachments.resolve_session_dir(None, cache_root)
    assert err is None
    assert directory == cache_root / "env-session"


def test_resolve_session_dir_falls_back_to_newest_subdir(cache_root: Path) -> None:
    _write_image(cache_root / "old-session" / "1.png")
    newest = cache_root / "new-session"
    newest.mkdir(parents=True)
    # Make the directory itself the newest entry.
    future = time.time() + 10
    os.utime(newest, (future, future))

    directory, err = claude_attachments.resolve_session_dir(None, cache_root)
    assert err is None
    assert directory == newest


def test_resolve_session_dir_rejects_path_traversal(cache_root: Path) -> None:
    directory, err = claude_attachments.resolve_session_dir("../etc", cache_root)
    assert directory is None
    assert "invalid session id" in err


def test_resolve_session_dir_errors_when_session_missing(cache_root: Path) -> None:
    cache_root.mkdir(parents=True)
    directory, err = claude_attachments.resolve_session_dir("nope", cache_root)
    assert directory is None
    assert "session directory not found" in err


def test_resolve_session_dir_errors_when_cache_missing(tmp_path: Path) -> None:
    directory, err = claude_attachments.resolve_session_dir(None, tmp_path / "missing")
    assert directory is None
    assert "image cache not found" in err


# --------------------------------------------------------------------------- #
# select_claude_pasted_images                                                 #
# --------------------------------------------------------------------------- #
def test_select_orders_by_numeric_name_not_mtime(cache_root: Path) -> None:
    session = cache_root / "s1"
    # 2.png has the OLDEST mtime but must still come after 1.png.
    _write_image(session / "2.png", mtime_offset=-0.3)
    _write_image(session / "1.png", mtime_offset=-0.1)

    selected, err = claude_attachments.select_claude_pasted_images(
        2, session_id="s1", cache_root=cache_root
    )
    assert err is None
    assert selected == [session / "1.png", session / "2.png"]


def test_select_takes_newest_n_in_ascending_order(cache_root: Path) -> None:
    session = cache_root / "s1"
    for i in (1, 2, 3, 4):
        _write_image(session / f"{i}.png")

    selected, err = claude_attachments.select_claude_pasted_images(
        2, session_id="s1", cache_root=cache_root
    )
    assert err is None
    assert selected == [session / "3.png", session / "4.png"]


def test_select_falls_back_to_mtime_for_non_numeric_names(cache_root: Path) -> None:
    session = cache_root / "s1"
    _write_image(session / "a.png", mtime_offset=-0.2)
    _write_image(session / "b.png", mtime_offset=-0.1)

    selected, err = claude_attachments.select_claude_pasted_images(
        2, session_id="s1", cache_root=cache_root
    )
    assert err is None
    assert selected == [session / "a.png", session / "b.png"]


def test_select_rejects_invalid_count(cache_root: Path) -> None:
    for bad in (0, 9, -1):
        selected, err = claude_attachments.select_claude_pasted_images(
            bad, session_id="s1", cache_root=cache_root
        )
        assert selected == []
        assert "between 1 and 8" in err


def test_select_errors_when_session_empty(cache_root: Path) -> None:
    (cache_root / "s1").mkdir(parents=True)
    selected, err = claude_attachments.select_claude_pasted_images(
        1, session_id="s1", cache_root=cache_root
    )
    assert selected == []
    assert "no supported image files" in err


def test_select_errors_when_too_few(cache_root: Path) -> None:
    _write_image(cache_root / "s1" / "1.png")
    selected, err = claude_attachments.select_claude_pasted_images(
        3, session_id="s1", cache_root=cache_root
    )
    assert selected == []
    assert "only 1 available" in err


def test_select_ignores_unsupported_files(cache_root: Path) -> None:
    session = cache_root / "s1"
    _write_image(session / "1.png")
    (session / "notes.txt").write_text("not an image")

    selected, err = claude_attachments.select_claude_pasted_images(
        1, session_id="s1", cache_root=cache_root
    )
    assert err is None
    assert selected == [session / "1.png"]
