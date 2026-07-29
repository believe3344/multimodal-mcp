from __future__ import annotations

import io

import fitz
import pytest
from PIL import Image


@pytest.fixture
def png_bytes() -> bytes:
    image = Image.new("RGB", (80, 60), "white")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
def digital_pdf_bytes() -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Digital PDF text for extraction. " * 4)
    data = document.tobytes()
    document.close()
    return data


@pytest.fixture
def scanned_pdf_bytes(png_bytes: bytes) -> bytes:
    document = fitz.open()
    page = document.new_page(width=200, height=150)
    page.insert_image(page.rect, stream=png_bytes)
    data = document.tobytes()
    document.close()
    return data
