"""Claude Code pasted-image attachment resolution.

Claude Code stores pasted images per session:

    ~/.claude/image-cache/<session-id>/1.png
    ~/.claude/image-cache/<session-id>/2.png
    ...

The numeric filename matches the `[Image N]` placeholder in the prompt, so
ascending numeric order IS the original paste order. The MCP server process
spawned by Claude Code carries the exact session id in the
``CLAUDE_CODE_SESSION_ID`` environment variable, which removes any guessing
about which session directory belongs to the current conversation.

This module is intentionally separate from ``attachments.py`` (OpenCode's
flat mtime-ordered cache) so the two layouts never mix.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from attachments import SUPPORTED_SUFFIXES

CLAUDE_IMAGE_CACHE = Path.home() / ".claude" / "image-cache"

SESSION_ID_ENV = "CLAUDE_CODE_SESSION_ID"


def _list_session_images(directory: Path) -> list[Path]:
    """Return supported image files directly inside a session directory."""
    if not directory.exists() or not directory.is_dir():
        return []
    try:
        entries = list(directory.iterdir())
    except OSError:
        return []

    results: list[Path] = []
    for entry in entries:
        try:
            if not entry.is_file():
                continue
            if entry.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            results.append(entry)
        except OSError:
            continue
    return results


def resolve_session_dir(
    session_id: Optional[str] = None,
    cache_root: Optional[Path] = None,
) -> tuple[Optional[Path], Optional[str]]:
    """Resolve the Claude Code session directory holding pasted images.

    Resolution order:
      1. explicit ``session_id`` argument,
      2. ``CLAUDE_CODE_SESSION_ID`` environment variable (set by Claude Code
         for the MCP server process),
      3. the most recently modified subdirectory of the cache root (fallback
         in case the undocumented env var disappears).

    Returns (directory, error_message). ``directory`` is None when
    ``error_message`` is set.
    """
    root = cache_root or CLAUDE_IMAGE_CACHE

    candidate = session_id or os.environ.get(SESSION_ID_ENV) or None
    if candidate:
        # Guard against path traversal: a session id must be a single
        # directory name, never a path.
        if candidate != Path(candidate).name:
            return None, f"invalid session id: {candidate!r}"
        directory = root / candidate
        if directory.is_dir():
            return directory, None
        return None, f"session directory not found: {directory}"

    if not root.is_dir():
        return None, f"Claude Code image cache not found: {root}"

    try:
        subdirs = [p for p in root.iterdir() if p.is_dir()]
    except OSError:
        return None, f"cannot read Claude Code image cache: {root}"
    if not subdirs:
        return None, f"no session directories in {root}"

    keyed = [(p.stat().st_mtime, p.name, p) for p in subdirs]
    keyed.sort(key=lambda item: (item[0], item[1]))
    return keyed[-1][2], None


def select_claude_pasted_images(
    count: int,
    session_id: Optional[str] = None,
    cache_root: Optional[Path] = None,
) -> tuple[list[Path], Optional[str]]:
    """Select the newest `count` Claude Code pasted images in paste order.

    When every filename has an integer stem (`1.png`, `2.png`, ...), files
    are ordered by that number, which matches the `[Image N]` placeholder
    order. Otherwise ordering falls back to (mtime, name). The last `count`
    entries (newest) are returned in ascending order, so the first returned
    file is the oldest of the selection.

    Returns (paths, error_message). `paths` is empty when `error_message` is
    set.
    """
    if count < 1 or count > 8:
        return [], f"count must be between 1 and 8, got {count}"

    directory, err = resolve_session_dir(session_id, cache_root)
    if err:
        return [], err

    files = _list_session_images(directory)
    if not files:
        return [], f"no supported image files in {directory}"

    if len(files) < count:
        return [], (
            f"requested {count} images but only {len(files)} available in {directory}"
        )

    stems = [p.stem for p in files]
    if all(s.isdigit() for s in stems):
        # Placeholder order is authoritative: [Image 1] -> 1.png, etc.
        files = sorted(files, key=lambda p: int(p.stem))
    else:
        keyed = [(p.stat().st_mtime, p.name, p) for p in files]
        keyed.sort(key=lambda item: (item[0], item[1]))
        files = [p for _mtime, _name, p in keyed]

    return files[-count:], None
