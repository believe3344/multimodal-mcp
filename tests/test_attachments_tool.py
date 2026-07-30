import os
import time
from pathlib import Path

import pytest

import server
from jobs import JobManager
from state import MultimodalState


def _write_image(path: Path, data: bytes, mtime_offset: float = 0.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    atime = time.time() + mtime_offset
    os.utime(path, (atime, atime))


@pytest.mark.asyncio
async def test_describe_pasted_images_selects_newest_n_in_paste_order(
    monkeypatch, tmp_path: Path, png_bytes: bytes
) -> None:
    cache = tmp_path / "multimodal-attachments"
    _write_image(cache / "A.png", png_bytes, mtime_offset=-0.3)
    _write_image(cache / "B.png", png_bytes, mtime_offset=-0.2)
    _write_image(cache / "C.png", png_bytes, mtime_offset=-0.1)

    captured_sources = []

    async def fake_resolve(source):
        captured_sources.append(source)
        return png_bytes, None

    async def fake_vision(_provider, _base_url, _api_key, _model, messages, **_kwargs):
        return "description"

    import attachments
    monkeypatch.setattr(attachments, "CACHE_DIR", cache)
    monkeypatch.setattr(server, "_resolve_image_source", fake_resolve)
    monkeypatch.setattr(server, "_vision_completion", fake_vision)
    monkeypatch.setattr(server, "PROVIDER", "openai")
    monkeypatch.setattr(server, "STATE", MultimodalState())
    monkeypatch.setattr(
        server,
        "JOBS",
        JobManager(result_ttl=60, max_entries=8, total_timeout=5),
    )

    result = await server.describe_pasted_images(count=3)
    assert "description" in result
    assert captured_sources == [str(cache / "A.png"), str(cache / "B.png"), str(cache / "C.png")]


@pytest.mark.asyncio
async def test_describe_pasted_images_returns_error_when_too_few(
    monkeypatch, tmp_path: Path, png_bytes: bytes
) -> None:
    cache = tmp_path / "multimodal-attachments"
    _write_image(cache / "only.png", png_bytes)

    import attachments
    monkeypatch.setattr(attachments, "CACHE_DIR", cache)
    monkeypatch.setattr(server, "STATE", MultimodalState())
    monkeypatch.setattr(
        server,
        "JOBS",
        JobManager(result_ttl=60, max_entries=8, total_timeout=5),
    )

    result = await server.describe_pasted_images(count=2)
    assert "[describe_pasted_images failed]" in result
    assert "only 1 available" in result


@pytest.mark.asyncio
async def test_describe_pasted_images_rejects_invalid_count(monkeypatch, tmp_path: Path) -> None:
    cache = tmp_path / "multimodal-attachments"
    import attachments
    monkeypatch.setattr(attachments, "CACHE_DIR", cache)

    result = await server.describe_pasted_images(count=0)
    assert "[describe_pasted_images failed]" in result
    assert "between 1 and 8" in result
