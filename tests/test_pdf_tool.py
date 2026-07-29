import base64

import pytest

import server
from pdf_support import PdfPage
from state import MultimodalState


@pytest.mark.asyncio
async def test_describe_pdf_returns_digital_text_without_vision(monkeypatch, digital_pdf_bytes: bytes) -> None:
    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("vision API must not be called for digital text")

    monkeypatch.setattr(server, "_chat_completion", fail_if_called)
    monkeypatch.setattr(server, "STATE", MultimodalState())
    source = base64.b64encode(digital_pdf_bytes).decode()
    result = await server.describe_pdf(document=source)
    assert "## Page 1" in result
    assert "Digital PDF text" in result


@pytest.mark.asyncio
async def test_describe_pdf_uses_vision_for_scanned_pages(monkeypatch, scanned_pdf_bytes: bytes) -> None:
    calls = []

    async def fake_describe(images, prompt, detail):
        calls.append((images, prompt, detail))
        return 'scan text\n\n<!-- multimodal-meta {"image_id":"img_page","cache_hit":false} -->'

    monkeypatch.setattr(server, "_describe_prepared_images", fake_describe)
    monkeypatch.setattr(server, "STATE", MultimodalState())
    source = base64.b64encode(scanned_pdf_bytes).decode()
    result = await server.describe_pdf(document=source)
    assert len(calls) == 1
    assert calls[0][0][0][1] in {"image/png", "image/jpeg"}
    assert "img_page" in result


@pytest.mark.asyncio
async def test_describe_pdf_reports_page_selection_error(digital_pdf_bytes: bytes) -> None:
    source = base64.b64encode(digital_pdf_bytes).decode()
    result = await server.describe_pdf(document=source, pages="2")
    assert "outside 1-1" in result


@pytest.mark.asyncio
async def test_describe_pdf_preserves_mixed_page_order(monkeypatch, png_bytes: bytes) -> None:
    async def fake_resolve(_source, *, max_bytes):
        return b"%PDF-fake", None

    def fake_extract(_raw, _pages, _mode):
        return [
            PdfPage(number=1, text="first text page"),
            PdfPage(number=2, image=png_bytes),
            PdfPage(number=3, text="third text page"),
        ]

    async def fake_describe(_images, _prompt, _detail):
        return "## Page 2\n\nsecond scanned page"

    monkeypatch.setattr(server, "_resolve_binary_source", fake_resolve)
    monkeypatch.setattr(server, "extract_pdf_pages", fake_extract)
    monkeypatch.setattr(server, "_describe_prepared_images", fake_describe)
    result = await server.describe_pdf(document="ignored")
    assert result.index("## Page 1") < result.index("## Page 2") < result.index("## Page 3")
