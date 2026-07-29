from __future__ import annotations

from typing import Any, Optional

SUPPORTED_PROVIDERS = ("openai", "anthropic")


def normalize_provider(value: Optional[str]) -> str:
    if value is None:
        return "openai"

    normalized = value.strip().lower()
    return normalized or "openai"


def validate_provider(provider: str) -> None:
    if provider not in SUPPORTED_PROVIDERS:
        accepted = ", ".join(SUPPORTED_PROVIDERS)
        raise ValueError(
            f"Unsupported PROVIDER '{provider}'; expected one of: {accepted}"
        )


def build_content(
    provider: str,
    image_b64: str,
    mime: str,
    text: Optional[str] = None,
    detail: str = "high",
) -> list[dict[str, Any]]:
    validate_provider(provider)

    if provider == "openai":
        content: list[dict[str, Any]] = [
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime};base64,{image_b64}",
                    "detail": detail,
                },
            }
        ]
        if text is not None:
            content.append({"type": "text", "text": text})
        return content

    content = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": mime,
                "data": image_b64,
            },
        }
    ]
    if text is not None:
        content.append({"type": "text", "text": text})
    return content


def build_request(
    provider: str,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    **gen_kwargs: Any,
) -> tuple[str, dict[str, Any], dict[str, str]]:
    validate_provider(provider)

    if not api_key:
        raise RuntimeError(f"Missing API key for model '{model}'")
    if not base_url:
        raise RuntimeError(f"Missing base URL for model '{model}'")
    if not model:
        raise RuntimeError("Missing MODEL_NAME")

    if provider == "openai":
        payload: dict[str, Any] = {"model": model, "messages": messages}
        payload.update(gen_kwargs)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        return f"{base_url}/chat/completions", payload, headers

    payload = {"model": model, "messages": messages, "max_tokens": 4096}
    payload.update(gen_kwargs)
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    return f"{base_url}/v1/messages", payload, headers


def extract_response_text(provider: str, data: dict[str, Any]) -> str:
    validate_provider(provider)

    if provider == "openai":
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = [part.get("text", "") for part in content if isinstance(part, dict)]
            text = "\n".join(part for part in texts if part)
            if text:
                return text
        raise KeyError("no message content in response")

    texts: list[str] = []
    for part in data["content"]:
        if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
            texts.append(part["text"])
    if not texts:
        raise KeyError("no text content in response")
    return "\n".join(texts)
