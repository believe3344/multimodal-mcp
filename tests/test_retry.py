import httpx
import pytest

import server


@pytest.mark.asyncio
async def test_post_json_retries_transient_status(monkeypatch) -> None:
    attempts = 0

    async def fake_once(client, url, payload, headers):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503, request=httpx.Request("POST", url), content=b"busy")
        return httpx.Response(200, request=httpx.Request("POST", url), json={"ok": True})

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(server, "_post_json_once", fake_once)
    monkeypatch.setattr(server.asyncio, "sleep", no_sleep)
    response = await server._post_json_with_retry("https://example.test/v1", {}, {})
    assert response.status_code == 200
    assert attempts == 3


@pytest.mark.asyncio
async def test_post_json_does_not_retry_bad_request(monkeypatch) -> None:
    attempts = 0

    async def fake_once(client, url, payload, headers):
        nonlocal attempts
        attempts += 1
        return httpx.Response(400, request=httpx.Request("POST", url), content=b"bad")

    monkeypatch.setattr(server, "_post_json_once", fake_once)
    response = await server._post_json_with_retry("https://example.test/v1", {}, {})
    assert response.status_code == 400
    assert attempts == 1


@pytest.mark.asyncio
async def test_upstream_concurrency_is_limited_to_two(monkeypatch) -> None:
    current = 0
    peak = 0
    release = server.asyncio.Event()

    async def fake_once(client, url, payload, headers):
        nonlocal current, peak
        current += 1
        peak = max(peak, current)
        await release.wait()
        current -= 1
        return httpx.Response(200, request=httpx.Request("POST", url), json={"ok": True})

    monkeypatch.setattr(server, "UPSTREAM_SEMAPHORE", server.asyncio.Semaphore(2))
    monkeypatch.setattr(server, "_post_json_once", fake_once)
    tasks = [
        server.asyncio.create_task(server._post_json_with_retry("https://example.test", {}, {}))
        for _ in range(4)
    ]
    await server.asyncio.sleep(0.01)
    assert peak == 2
    release.set()
    await server.asyncio.gather(*tasks)
