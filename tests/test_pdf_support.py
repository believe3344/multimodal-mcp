import pytest

from pdf_support import parse_page_selection


def test_parse_page_selection_preserves_order_and_removes_duplicates() -> None:
    assert parse_page_selection("3,1-2,2", page_count=5, max_pages=20) == [2, 0, 1]


@pytest.mark.parametrize("selection", ["0", "4-2", "1,a", "6"])
def test_parse_page_selection_rejects_invalid_values(selection: str) -> None:
    with pytest.raises(ValueError):
        parse_page_selection(selection, page_count=5, max_pages=20)


def test_default_selection_is_capped() -> None:
    assert parse_page_selection(None, page_count=25, max_pages=20) == list(range(20))


from pdf_support import PdfMode, extract_pdf_pages


def test_auto_mode_extracts_digital_text(digital_pdf_bytes: bytes) -> None:
    pages = extract_pdf_pages(digital_pdf_bytes, None, PdfMode.AUTO)
    assert len(pages) == 1
    assert "Digital PDF text" in (pages[0].text or "")
    assert pages[0].image is None


def test_auto_mode_renders_scanned_page(scanned_pdf_bytes: bytes) -> None:
    pages = extract_pdf_pages(scanned_pdf_bytes, None, PdfMode.AUTO)
    assert len(pages) == 1
    assert pages[0].text is None
    assert pages[0].image.startswith(b"\x89PNG")


def test_text_mode_does_not_render_scanned_page(scanned_pdf_bytes: bytes) -> None:
    pages = extract_pdf_pages(scanned_pdf_bytes, None, PdfMode.TEXT)
    assert pages[0].text == ""
    assert pages[0].image is None


def test_extract_pdf_pages_rejects_non_pdf() -> None:
    with pytest.raises(ValueError, match="not a PDF"):
        extract_pdf_pages(b"not-pdf", None, PdfMode.AUTO)
