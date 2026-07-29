import pytest

import server
from providers import (
    build_content,
    build_request,
    extract_response_text,
    normalize_provider,
    validate_provider,
)


def test_provider_defaults_to_openai() -> None:
    assert normalize_provider(None) == "openai"
    assert normalize_provider("   ") == "openai"


def test_provider_is_normalized() -> None:
    assert normalize_provider(" Anthropic ") == "anthropic"


def test_invalid_provider_is_rejected() -> None:
    with pytest.raises(ValueError, match="openai, anthropic"):
        validate_provider("responses")


def test_build_content_for_openai() -> None:
    content = build_content("openai", "abc", "image/png", "inspect this", detail="low")
    assert content == [
        {
            "type": "image_url",
            "image_url": {
                "url": "data:image/png;base64,abc",
                "detail": "low",
            },
        },
        {"type": "text", "text": "inspect this"},
    ]


def test_build_content_for_anthropic() -> None:
    content = build_content("anthropic", "abc", "image/png", "inspect this")
    assert content == [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": "abc",
            },
        },
        {"type": "text", "text": "inspect this"},
    ]


def test_build_request_for_openai() -> None:
    url, payload, headers = build_request(
        "openai",
        "https://vision.example.com",
        "secret",
        "gpt-4o-mini",
        [{"role": "user", "content": []}],
        temperature=0.2,
    )
    assert url == "https://vision.example.com/chat/completions"
    assert payload == {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": []}],
        "temperature": 0.2,
    }
    assert headers == {
        "Authorization": "Bearer secret",
        "Content-Type": "application/json",
    }


def test_build_request_for_anthropic() -> None:
    url, payload, headers = build_request(
        "anthropic",
        "https://vision.example.com",
        "secret",
        "claude-3-7-sonnet",
        [{"role": "user", "content": []}],
    )
    assert url == "https://vision.example.com/v1/messages"
    assert payload == {
        "model": "claude-3-7-sonnet",
        "messages": [{"role": "user", "content": []}],
        "max_tokens": 4096,
    }
    assert headers == {
        "x-api-key": "secret",
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }


def test_extract_response_text_for_openai() -> None:
    text = extract_response_text(
        "openai",
        {"choices": [{"message": {"content": "visible text"}}]},
    )
    assert text == "visible text"


def test_extract_response_text_for_anthropic() -> None:
    text = extract_response_text(
        "anthropic",
        {
            "content": [
                {"type": "text", "text": "line 1"},
                {"type": "tool_use", "id": "ignore"},
                {"type": "text", "text": "line 2"},
            ]
        },
    )
    assert text == "line 1\nline 2"


@pytest.mark.asyncio
async def test_vision_completion_rejects_invalid_provider(monkeypatch) -> None:
    with pytest.raises(ValueError, match="openai, anthropic"):
        await server._vision_completion(
            "responses",
            "https://vision.example.com",
            "secret",
            "model",
            [{"role": "user", "content": []}],
        )


def test_config_status_reports_invalid_provider(monkeypatch) -> None:
    monkeypatch.setattr(server, "PROVIDER", "responses")
    status = server._config_status()

    assert status["provider"] == "responses"
    assert status["provider_valid"] is False
    assert "openai, anthropic" in str(status["provider_error"])
