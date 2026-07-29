from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from jobs import RecognitionJob


@dataclass(frozen=True)
class RecognitionRequest:
    kind: str
    sources: list[str]
    instruction: str
    detail: str
    pages: Optional[str] = None
    pdf_mode: str = "auto"

    def dedupe_key(self, model: str, provider: str) -> str:
        payload = json.dumps(
            {
                "v": 1,
                "kind": self.kind,
                "sources": self.sources,
                "instruction": self.instruction,
                "detail": self.detail,
                "pages": self.pages,
                "pdf_mode": self.pdf_mode,
                "model": model,
                "provider": provider,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class RecognitionRunner:
    def __init__(
        self,
        *,
        prepare_image: Optional[Callable[[Optional[str]], Awaitable[tuple[Any, Optional[str]]]]],
        describe_images: Callable[[list[tuple[bytes, str]], str, Any], Awaitable[str]],
        get_image: Optional[Callable[[str], Any]] = None,
        resolve_binary: Optional[Callable[..., Awaitable[tuple[Optional[bytes], Optional[str]]]]] = None,
        extract_pdf: Optional[Callable[..., list[Any]]] = None,
        normalize_image: Optional[Callable[[bytes], tuple[Optional[bytes], Optional[str], Optional[str]]]] = None,
        max_source_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        self.prepare_image = prepare_image
        self.describe_images = describe_images
        self.get_image = get_image
        self.resolve_binary = resolve_binary
        self.extract_pdf = extract_pdf
        self.normalize_image = normalize_image
        self.max_source_bytes = max_source_bytes

    async def run(self, job: RecognitionJob, request: RecognitionRequest) -> str:
        if request.kind in {"image", "images"}:
            if self.prepare_image is None:
                raise RuntimeError("image preparation is unavailable")
            job.set_total_units(1)
            prepared: list[tuple[bytes, str]] = []
            for index, source in enumerate(request.sources, start=1):
                image, error = await self.prepare_image(source or None)
                if image is None:
                    raise RuntimeError(f"image {index}: {error}")
                prepared.append(image)
            result = await self.describe_images(prepared, request.instruction, request.detail)
            job.complete_unit("recognition", result)
            return result
        if request.kind == "image_id":
            if self.get_image is None:
                raise RuntimeError("image session lookup is unavailable")
            entry = self.get_image(request.sources[0])
            if entry is None:
                raise RuntimeError("image_id expired or does not exist")
            job.set_total_units(1)
            result = await self.describe_images(
                [(entry.data, entry.mime)], request.instruction, request.detail
            )
            job.complete_unit("image_id", result)
            return result
        if request.kind == "pdf":
            return await self._run_pdf(job, request)
        raise ValueError(f"unsupported recognition kind: {request.kind}")

    async def _run_pdf(self, job: RecognitionJob, request: RecognitionRequest) -> str:
        if self.resolve_binary is None or self.extract_pdf is None or self.normalize_image is None:
            raise RuntimeError("PDF support is unavailable")
        raw, error = await self.resolve_binary(
            request.sources[0], max_bytes=self.max_source_bytes
        )
        if raw is None:
            raise RuntimeError(error or "cannot read PDF")
        pages = self.extract_pdf(raw, request.pages, request.pdf_mode)
        job.set_total_units(len(pages))
        page_order = [page.number for page in pages]
        ordered: dict[int, str] = {}
        tasks: list[tuple[int, Awaitable[str]]] = []
        for page in pages:
            unit_id = f"page_{page.number}"
            if page.image is None:
                text = page.text or f"[page {page.number} has no extractable text]"
                section = f"## Page {page.number}\n\n{text}"
                ordered[page.number] = section
                job.complete_unit(unit_id, section)
                continue
            normalized, mime, normalize_error = self.normalize_image(page.image)
            if normalized is None or mime is None:
                message = normalize_error or "image normalization failed"
                section = f"## Page {page.number}\n\n[recognition failed: {message}]"
                ordered[page.number] = section
                job.fail_unit(unit_id, message)
                continue

            async def recognize_page(
                number: int = page.number,
                image: tuple[bytes, str] = (normalized, mime),
            ) -> str:
                try:
                    result = await self.describe_images(
                        [image], request.instruction, request.detail
                    )
                    section = f"## Page {number}\n\n{result}"
                    job.complete_unit(f"page_{number}", section)
                    return section
                except Exception as exc:
                    message = f"{type(exc).__name__}: {exc}"
                    job.fail_unit(f"page_{number}", message)
                    return f"## Page {number}\n\n[recognition failed: {message}]"

            tasks.append((page.number, recognize_page()))
        if tasks:
            results = await asyncio.gather(
                *(task for _number, task in tasks)
            )
            for (number, _task), result in zip(tasks, results):
                ordered[number] = result
        return "\n\n".join(ordered[number] for number in page_order)
