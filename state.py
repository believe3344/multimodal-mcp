from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Optional

DESCRIPTION_CACHE_TTL_SECONDS = 3600
DESCRIPTION_CACHE_MAX_ENTRIES = 128
IMAGE_SESSION_TTL_SECONDS = 1800
IMAGE_SESSION_MAX_ENTRIES = 32
IMAGE_SESSION_MAX_BYTES = 64 * 1024 * 1024


def make_image_id(data: bytes) -> str:
    return f"img_{hashlib.sha256(data).hexdigest()[:16]}"


def make_cache_key(
    images: list[bytes],
    model: str,
    provider: str,
    detail: str,
    instruction: str,
) -> str:
    digest = hashlib.sha256(b"multimodal-cache-v1\0")
    for value in (model, provider, detail, instruction):
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    for image in images:
        digest.update(hashlib.sha256(image).digest())
        digest.update(b"\0")
    return digest.hexdigest()


@dataclass(frozen=True)
class CacheEntry:
    value: str
    created_at: float


@dataclass(frozen=True)
class ImageEntry:
    data: bytes
    mime: str
    created_at: float


class MultimodalState:
    def __init__(
        self,
        *,
        cache_ttl: float = DESCRIPTION_CACHE_TTL_SECONDS,
        cache_max_entries: int = DESCRIPTION_CACHE_MAX_ENTRIES,
        image_ttl: float = IMAGE_SESSION_TTL_SECONDS,
        image_max_entries: int = IMAGE_SESSION_MAX_ENTRIES,
        image_max_bytes: int = IMAGE_SESSION_MAX_BYTES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cache_ttl = cache_ttl
        self.cache_max_entries = cache_max_entries
        self.image_ttl = image_ttl
        self.image_max_entries = image_max_entries
        self.image_max_bytes = image_max_bytes
        self.clock = clock
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._images: OrderedDict[str, ImageEntry] = OrderedDict()
        self._image_bytes = 0
        self._hits = 0
        self._misses = 0
        self._lock = threading.RLock()

    def _purge_expired(self) -> None:
        now = self.clock()
        for key, entry in list(self._cache.items()):
            if now - entry.created_at >= self.cache_ttl:
                del self._cache[key]
        for image_id, entry in list(self._images.items()):
            if now - entry.created_at >= self.image_ttl:
                self._image_bytes -= len(entry.data)
                del self._images[image_id]

    def peek_cached(self, key: str) -> Optional[str]:
        with self._lock:
            self._purge_expired()
            entry = self._cache.get(key)
            if entry is None:
                return None
            self._cache.move_to_end(key)
            return entry.value

    def get_cached(self, key: str) -> Optional[str]:
        with self._lock:
            self._purge_expired()
            entry = self._cache.get(key)
            if entry is None:
                self._misses += 1
                return None
            self._cache.move_to_end(key)
            self._hits += 1
            return entry.value

    def put_cached(self, key: str, value: str) -> None:
        with self._lock:
            self._purge_expired()
            self._cache[key] = CacheEntry(value=value, created_at=self.clock())
            self._cache.move_to_end(key)
            while len(self._cache) > self.cache_max_entries:
                self._cache.popitem(last=False)

    def put_image(self, data: bytes, mime: str) -> str:
        with self._lock:
            self._purge_expired()
            image_id = make_image_id(data)
            previous = self._images.get(image_id)
            if previous is not None:
                self._images.move_to_end(image_id)
                return image_id
            self._images[image_id] = ImageEntry(data=data, mime=mime, created_at=self.clock())
            self._image_bytes += len(data)
            while (
                len(self._images) > self.image_max_entries
                or self._image_bytes > self.image_max_bytes
            ):
                _, removed = self._images.popitem(last=False)
                self._image_bytes -= len(removed.data)
            return image_id

    def get_image(self, image_id: str) -> Optional[ImageEntry]:
        with self._lock:
            self._purge_expired()
            entry = self._images.get(image_id)
            if entry is not None:
                self._images.move_to_end(image_id)
            return entry

    def clear(self, target: str) -> dict[str, int]:
        if target not in {"cache", "images", "all"}:
            raise ValueError("target must be cache, images, or all")
        with self._lock:
            before = self.stats()
            if target in {"cache", "all"}:
                self._cache.clear()
            if target in {"images", "all"}:
                self._images.clear()
                self._image_bytes = 0
            return {
                "cache_entries_removed": before["cache_entries"] - len(self._cache),
                "image_entries_removed": before["image_entries"] - len(self._images),
            }

    def stats(self) -> dict[str, int]:
        with self._lock:
            self._purge_expired()
            return {
                "cache_entries": len(self._cache),
                "cache_hits": self._hits,
                "cache_misses": self._misses,
                "image_entries": len(self._images),
                "image_bytes": self._image_bytes,
            }
