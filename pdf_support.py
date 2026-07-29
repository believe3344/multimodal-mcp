from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import fitz

MAX_PDF_PAGES = 20
PDF_TEXT_THRESHOLD = 50


class PdfMode(str, Enum):
    AUTO = "auto"
    TEXT = "text"
    VISION = "vision"


@dataclass(frozen=True)
class PdfPage:
    number: int
    text: str | None = None
    image: bytes | None = None


def parse_page_selection(selection: str | None, page_count: int, max_pages: int = MAX_PDF_PAGES) -> list[int]:
    if page_count <= 0:
        return []
    if selection is None or not selection.strip():
        return list(range(min(page_count, max_pages)))
    result: list[int] = []
    seen: set[int] = set()
    for part in selection.split(","):
        token = part.strip()
        if not token:
            raise ValueError("empty page token")
        if "-" in token:
            bounds = token.split("-", 1)
            if len(bounds) != 2 or not all(value.isdigit() for value in bounds):
                raise ValueError(f"invalid page range: {token}")
            start, end = map(int, bounds)
            if start < 1 or end < start:
                raise ValueError(f"invalid page range: {token}")
            values = range(start, end + 1)
        else:
            if not token.isdigit():
                raise ValueError(f"invalid page number: {token}")
            values = [int(token)]
        for value in values:
            if value < 1 or value > page_count:
                raise ValueError(f"page {value} is outside 1-{page_count}")
            index = value - 1
            if index not in seen:
                seen.add(index)
                result.append(index)
            if len(result) > max_pages:
                raise ValueError(f"at most {max_pages} pages may be selected")
    return result


def extract_pdf_pages(data: bytes, selection: str | None, mode: PdfMode) -> list[PdfPage]:
    if not data.startswith(b"%PDF-"):
        raise ValueError("source is not a PDF")
    try:
        document = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"cannot open PDF: {exc}") from exc
    try:
        if document.needs_pass:
            raise ValueError("password-protected PDFs are not supported")
        indices = parse_page_selection(selection, document.page_count)
        result: list[PdfPage] = []
        for index in indices:
            page = document.load_page(index)
            text = page.get_text("text").strip()
            use_text = mode == PdfMode.TEXT or (
                mode == PdfMode.AUTO and len("".join(text.split())) >= PDF_TEXT_THRESHOLD
            )
            if use_text:
                result.append(PdfPage(number=index + 1, text=text))
                continue
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            result.append(PdfPage(number=index + 1, image=pixmap.tobytes("png")))
        return result
    finally:
        document.close()
