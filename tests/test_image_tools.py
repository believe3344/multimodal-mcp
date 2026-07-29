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


@pytest.mark.asyncio
async def test_describe_images_sends_images_in_order(monkeypatch, png_bytes: bytes) -> None:
    second = png_bytes[:-1] + b"x"
    sources = {"first": png_bytes, "second": second}
    captured = []

    async def fake_resolve(source):
        return sources[source], None

    def fake_normalize(data):
        return data, "image/png", None

    async def fake_chat(_base_url, _api_key, _model, messages, **_kwargs):
        captured.extend(messages[0]["content"])
        return "comparison"

    monkeypatch.setattr(server, "_resolve_image_source", fake_resolve)
    monkeypatch.setattr(server, "_normalize_image", fake_normalize)
    monkeypatch.setattr(server, "_chat_completion", fake_chat)
    monkeypatch.setattr(server, "STATE", MultimodalState())

    result = await server.describe_images(images=["first", "second"])
    urls = [part["image_url"]["url"] for part in captured if part["type"] == "image_url"]
    assert len(urls) == 2
    assert urls[0] != urls[1]
    assert result.startswith("comparison")
    assert '"image_ids":[' in result


@pytest.mark.asyncio
async def test_describe_images_rejects_invalid_counts() -> None:
    assert "requires 1 to 8" in await server.describe_images(images=[])
    assert "requires 1 to 8" in await server.describe_images(images=["x"] * 9)


@pytest.mark.asyncio
async def test_describe_images_reports_source_index(monkeypatch, png_bytes: bytes) -> None:
    async def fake_resolve(source):
        if source == "bad":
            return None, "download failed"
        return png_bytes, None

    monkeypatch.setattr(server, "_resolve_image_source", fake_resolve)
    result = await server.describe_images(images=["ok", "bad"])
    assert "image 2" in result
    assert "download failed" in result


@pytest.mark.asyncio
async def test_ask_image_reuses_stored_image(monkeypatch, png_bytes: bytes) -> None:
    state = MultimodalState()
    image_id = state.put_image(png_bytes, "image/png")
    prompts = []

    async def fake_describe(images, prompt, detail):
        prompts.append((images, prompt, detail))
        return "answer"

    monkeypatch.setattr(server, "STATE", state)
    monkeypatch.setattr(server, "_describe_prepared_images", fake_describe)
    result = await server.ask_image(image_id=image_id, question="What is the total?")
    assert result == "answer"
    assert prompts[0][0] == [(png_bytes, "image/png")]
    assert prompts[0][1] == "What is the total?"


@pytest.mark.asyncio
async def test_ask_image_rejects_unknown_id(monkeypatch) -> None:
    monkeypatch.setattr(server, "STATE", MultimodalState())
    result = await server.ask_image(image_id="img_missing", question="What?")
    assert "expired or does not exist" in result


@pytest.mark.asyncio
async def test_cache_status_and_clear(monkeypatch, png_bytes: bytes) -> None:
    state = MultimodalState()
    state.put_cached("key", "value")
    state.put_image(png_bytes, "image/png")
    monkeypatch.setattr(server, "STATE", state)
    assert '"cache_entries": 1' in await server.multimodal_cache_status()
    result = await server.clear_multimodal_state(server.StateTarget.ALL)
    assert '"cache_entries_removed": 1' in result
    assert state.stats()["image_entries"] == 0
