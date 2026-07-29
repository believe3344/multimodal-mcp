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
