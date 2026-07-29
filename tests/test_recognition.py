import asyncio

import pytest

from jobs import RecognitionJob
from recognition import RecognitionRequest, RecognitionRunner


def make_job(kind: str) -> RecognitionJob:
    return RecognitionJob("job_test", kind, "key", 1.0, 1.0)


@pytest.mark.asyncio
async def test_image_runner_records_one_unit(png_bytes: bytes) -> None:
    async def prepare(_source):
        return (png_bytes, "image/png"), None

    async def describe(_images, _prompt, _detail):
        return "image result"

    runner = RecognitionRunner(prepare_image=prepare, describe_images=describe)
    request = RecognitionRequest(kind="image", sources=["one"], instruction="read", detail="high")
    job = make_job("image")
    result = await runner.run(job, request)
    assert result == "image result"
    assert job.total_units == 1
    assert job.units["recognition"].result == "image result"


@pytest.mark.asyncio
async def test_image_id_runner_reads_state(png_bytes: bytes) -> None:
    class Entry:
        data = png_bytes
        mime = "image/png"

    async def describe(_images, prompt, _detail):
        assert prompt == "what is shown?"
        return "follow-up"

    runner = RecognitionRunner(
        prepare_image=None,
        describe_images=describe,
        get_image=lambda image_id: Entry() if image_id == "img_ok" else None,
    )
    request = RecognitionRequest(
        kind="image_id",
        sources=["img_ok"],
        instruction="what is shown?",
        detail="high",
    )
    assert await runner.run(make_job("image_id"), request) == "follow-up"


@pytest.mark.asyncio
async def test_pdf_runner_preserves_order_and_records_partial_failures(png_bytes: bytes) -> None:
    class Page:
        def __init__(self, number, text=None, image=None):
            self.number = number
            self.text = text
            self.image = image

    async def resolve(_source, *, max_bytes):
        return b"%PDF-fake", None

    def extract(_raw, _pages, _mode):
        return [
            Page(3, image=png_bytes + b"different"),
            Page(1, text="digital page"),
            Page(2, image=png_bytes),
        ]

    def normalize(data):
        return data, "image/png", None

    async def describe(images, prompt, detail):
        if images[0][0].endswith(b"different"):
            raise RuntimeError("upstream 503")
        return "scanned page"

    runner = RecognitionRunner(
        prepare_image=None,
        describe_images=describe,
        resolve_binary=resolve,
        extract_pdf=extract,
        normalize_image=normalize,
    )
    request = RecognitionRequest("pdf", ["doc"], "ocr", "high")
    job = make_job("pdf")
    result = await runner.run(job, request)
    assert result.index("## Page 3") < result.index("## Page 1") < result.index("## Page 2")
    assert "digital page" in result
    assert "scanned page" in result
    assert "upstream 503" in result
    assert job.completed_units == 2
    assert job.failed_units == 1


@pytest.mark.asyncio
async def test_pdf_scanned_pages_run_concurrently(png_bytes: bytes) -> None:
    active = 0
    peak = 0
    release = asyncio.Event()

    class Page:
        def __init__(self, number):
            self.number = number
            self.text = None
            self.image = png_bytes + bytes([number])

    async def resolve(_source, *, max_bytes):
        return b"%PDF-fake", None

    async def describe(images, prompt, detail):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await release.wait()
        active -= 1
        return "page"

    runner = RecognitionRunner(
        prepare_image=None,
        describe_images=describe,
        resolve_binary=resolve,
        extract_pdf=lambda *_args: [Page(1), Page(2)],
        normalize_image=lambda data: (data, "image/png", None),
    )
    task = asyncio.create_task(
        runner.run(make_job("pdf"), RecognitionRequest("pdf", ["doc"], "ocr", "high"))
    )
    await asyncio.sleep(0.01)
    assert peak == 2
    release.set()
    await task
