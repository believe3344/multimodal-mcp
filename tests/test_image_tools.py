import pytest

import server
from state import MultimodalState


@pytest.mark.asyncio
async def test_describe_image_caches_and_returns_image_id(monkeypatch, png_bytes: bytes) -> None:
    calls = []

    async def fake_resolve(_source):
        return png_bytes, None

    async def fake_chat(_base_url, _api_key, _model, messages, **_kwargs):
        calls.append(messages)
        return "visible text"

    monkeypatch.setattr(server, "_resolve_image_source", fake_resolve)
    monkeypatch.setattr(server, "_chat_completion", fake_chat)
    monkeypatch.setattr(server, "STATE", MultimodalState())

    first = await server.describe_image(image="ignored")
    second = await server.describe_image(image="ignored")

    assert first.startswith("visible text")
    assert '"image_id":"img_' in first
    assert '"cache_hit":false' in first
    assert '"cache_hit":true' in second
    assert len(calls) == 1
