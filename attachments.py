from __future__ import annotations

from pathlib import Path
from typing import Optional

CACHE_DIR = Path.home() / ".cache" / "opencode" / "multimodal-attachments"

SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def list_attachments(directory: Optional[Path] = None) -> list[Path]:
    """Return all supported image files in the cache directory.

    Ignores non-existent directories, unreadable entries, and unsupported
    file types. Only regular files are returned.
    """
    root = directory or CACHE_DIR
    if not root.exists() or not root.is_dir():
        return []

    results: list[Path] = []
    try:
        entries = list(root.iterdir())
    except OSError:
        return []

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


def select_pasted_images(count: int, directory: Optional[Path] = None) -> tuple[list[Path], Optional[str]]:
    """Select the newest `count` attachment files in paste order.

    Files are sorted by modification time ascending, then filename ascending.
    The last `count` entries (newest) are returned, preserving chronological
    paste order: the first returned file is the oldest of the selection.

    Returns (paths, error_message). `paths` is empty when `error_message` is set.
    """
    if count < 1 or count > 8:
        return [], f"count must be between 1 and 8, got {count}"

    files = list_attachments(directory)
    if not files:
        root = directory or CACHE_DIR
        return [], f"no supported image files in {root}"

    if len(files) < count:
        root = directory or CACHE_DIR
        return [], (
            f"requested {count} images but only {len(files)} available in {root}"
        )

    keyed = [(p.stat().st_mtime, p.name, p) for p in files]
    keyed.sort(key=lambda item: (item[0], item[1]))
    files = [p for _mtime, _name, p in keyed]

    return files[-count:], None
