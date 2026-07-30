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

    Files are sorted by modification time descending, then filename ascending.
    The newest `count` files are selected and then reversed so the oldest of the
    selection (the first pasted image) comes first.

    Returns (paths, error_message). `paths` is empty when `error_message` is set.
    """
    if count < 1 or count > 8:
        return [], f"count must be between 1 and 8, got {count}"

    files = list_attachments(directory)
    if not files:
        root = directory or CACHE_DIR
        return [], f"no supported image files in {root}"

    # Newest first, stable tie-breaker by path name.
    keyed = [(p.stat().st_mtime, p.name, p) for p in files]
    keyed.sort(key=lambda item: (-item[0], item[1]))
    files = [p for _mtime, _name, p in keyed]

    selected = files[:count]
    if len(selected) < count:
        root = directory or CACHE_DIR
        return [], (
            f"requested {count} images but only {len(selected)} available in {root}"
        )

    # Restore chronological paste order: oldest selected first.
    selected.reverse()
    return selected, None
