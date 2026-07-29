import pytest

import server
from jobs import JobManager
from state import MultimodalState


@pytest.mark.asyncio
async def test_describe_image_caches_and_returns_image_id(monkeypatch, png_bytes: bytes) -> None:
    calls = []

    async def fake_resolve(_source):
        return png_bytes, None

    async def fake_vision(_provider, _base_url, _api_key, _model, messages, **_kwargs):
        calls.append(messages)
        return "visible text"

    monkeypatch.setattr(server, "_resolve_image_source", fake_resolve)
    monkeypatch.setattr(server, "_vision_completion", fake_vision)
    monkeypatch.setattr(server, "PROVIDER", "openai")
    monkeypatch.setattr(server, "STATE", MultimodalState())
    monkeypatch.setattr(
        server,
        "JOBS",
        JobManager(result_ttl=60, max_entries=8, total_timeout=5),
    )

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

    async def fake_vision(_provider, _base_url, _api_key, _model, messages, **_kwargs):
        captured.extend(messages[0]["content"])
        return "comparison"

    monkeypatch.setattr(server, "_resolve_image_source", fake_resolve)
    monkeypatch.setattr(server, "_normalize_image", fake_normalize)
    monkeypatch.setattr(server, "_vision_completion", fake_vision)
    monkeypatch.setattr(server, "PROVIDER", "openai")
    monkeypatch.setattr(server, "STATE", MultimodalState())
    monkeypatch.setattr(
        server,
        "JOBS",
        JobManager(result_ttl=60, max_entries=8, total_timeout=5),
    )

    result = await server.describe_images(images=["first", "second"])
    urls = [part["image_url"]["url"] for part in captured if part["type"] == "image_url"]
    assert len(urls) == 2
    assert urls[0] != urls[1]
    assert result.startswith("comparison")
    assert '"image_ids":[' in result


@pytest.mark.asyncio
async def test_describe_image_uses_anthropic_request_shape(monkeypatch, png_bytes: bytes) -> None:
    captured = {}

    async def fake_resolve(_source):
        return png_bytes, None

    async def fake_post(url, payload, headers):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers

        class FakeResponse:
            status_code = 200
            text = ""

            def json(self):
                return {
                    "content": [
                        {"type": "text", "text": "line 1"},
                        {"type": "tool_use", "id": "ignore"},
                        {"type": "text", "text": "line 2"},
                    ]
                }

        return FakeResponse()

    monkeypatch.setattr(server, "_resolve_image_source", fake_resolve)
    monkeypatch.setattr(server, "_post_json_with_retry", fake_post)
    monkeypatch.setattr(server, "PROVIDER", "anthropic")
    monkeypatch.setattr(server, "BASE_URL", "https://vision.example.com")
    monkeypatch.setattr(server, "API_KEY", "secret")
    monkeypatch.setattr(server, "MODEL_NAME", "claude-3-7-sonnet")
    monkeypatch.setattr(server, "STATE", MultimodalState())
    monkeypatch.setattr(
        server,
        "JOBS",
        JobManager(result_ttl=60, max_entries=8, total_timeout=5),
    )

    result = await server.describe_image(image="ignored")

    assert result.startswith("line 1\nline 2")
    assert captured["url"] == "https://vision.example.com/v1/messages"
    assert captured["headers"] == {
        "x-api-key": "secret",
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    content = captured["payload"]["messages"][0]["content"]
    assert content[0]["type"] == "image"
    assert content[0]["source"]["type"] == "base64"
    assert content[0]["source"]["media_type"] == "image/png"
    assert content[0]["source"]["data"]
    assert content[1]["type"] == "text"


@pytest.mark.asyncio
async def test_describe_images_rejects_invalid_counts() -> None:
    assert "requires 1 to 8" in await server.describe_images(images=[])
    assert "requires 1 to 8" in await server.describe_images(images=["x"] * 9)


@pytest.mark.asyncio
async def test_describe_image_rejects_invalid_provider_before_resolve(monkeypatch) -> None:
    async def fail_resolve(_source):
        raise AssertionError("image source resolve must not run")

    monkeypatch.setattr(server, "PROVIDER", "responses")
    monkeypatch.setattr(server, "_resolve_image_source", fail_resolve)

    result = await server.describe_image(image="ignored")

    assert "Unsupported PROVIDER 'responses'" in result


@pytest.mark.asyncio
async def test_start_recognition_rejects_invalid_provider_before_submit(monkeypatch) -> None:
    def fail_submit(**_kwargs):
        raise AssertionError("job submit must not run")

    monkeypatch.setattr(server, "PROVIDER", "responses")
    monkeypatch.setattr(server.JOBS, "submit", fail_submit)

    result = await server.start_recognition(
        kind=server.RecognitionKind.IMAGE,
        sources=["ignored"],
    )

    assert "Unsupported PROVIDER 'responses'" in result


@pytest.mark.asyncio
async def test_describe_images_reports_source_index(monkeypatch, png_bytes: bytes) -> None:
    async def fake_resolve(source):
        if source == "bad":
            return None, "download failed"
        return png_bytes, None

    monkeypatch.setattr(server, "_resolve_image_source", fake_resolve)
    monkeypatch.setattr(
        server,
        "JOBS",
        JobManager(result_ttl=60, max_entries=8, total_timeout=5),
    )
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
    monkeypatch.setattr(server.RUNNER, "get_image", state.get_image)
    monkeypatch.setattr(server.RUNNER, "describe_images", fake_describe)
    monkeypatch.setattr(
        server,
        "JOBS",
        JobManager(result_ttl=60, max_entries=8, total_timeout=5),
    )
    result = await server.ask_image(image_id=image_id, question="What is the total?")
    assert result == "answer"
    assert prompts[0][0] == [(png_bytes, "image/png")]
    assert prompts[0][1] == "What is the total?"


@pytest.mark.asyncio
async def test_ask_image_rejects_unknown_id(monkeypatch) -> None:
    monkeypatch.setattr(server, "STATE", MultimodalState())
    monkeypatch.setattr(
        server,
        "JOBS",
        JobManager(result_ttl=60, max_entries=8, total_timeout=5),
    )
    result = await server.ask_image(image_id="img_missing", question="What?")
    assert "expired or does not exist" in result


@pytest.mark.asyncio
async def test_cache_status_and_clear(monkeypatch, png_bytes: bytes) -> None:
    state = MultimodalState()
    state.put_cached("key", "value")
    state.put_image(png_bytes, "image/png")
    monkeypatch.setattr(server, "STATE", state)
    monkeypatch.setattr(
        server,
        "JOBS",
        JobManager(result_ttl=60, max_entries=8, total_timeout=5),
    )
    status = await server.multimodal_cache_status()
    assert '"cache_entries": 1' in status
    result = await server.clear_multimodal_state(server.StateTarget.ALL)
    assert '"cache_entries_removed": 1' in result
    assert state.stats()["image_entries"] == 0
