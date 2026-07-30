#!/usr/bin/env python3
"""
Multimodal MCP server - "eyes" for any text-only main model.

Single tool: describe_image, with auto-dispatch on the `image` argument:
  - http(s) URL       -> downloaded
  - data: URI         -> base64 extracted
  - local file path   -> read from disk
  - raw base64        -> used as-is
  - empty / None      -> read from the SYSTEM CLIPBOARD (screenshots)

The clipboard path is what makes screenshots work end-to-end across any MCP
client: the user takes a screenshot (Cmd+Shift+4 / Win+Shift+S / scrot) so
the image lives in the OS clipboard, then types "看下我的截图" in the chat.
The agent calls describe_image with no `image` arg; the tool reads the
clipboard, sends it to the vision model, returns text. The main model then
reasons over that text. No client-side attachment handling needed.

This server deliberately does NOT do any reasoning - that is the main model's
job (whatever the user picked in their MCP client: glm-5.2, deepseek-v4-pro,
qwen, etc.). It only bridges the multimodal gap.

Cross-platform clipboard read via:
  - macOS:   pngpaste  (brew install pngpaste)
  - Linux:   xclip     (apt install xclip)
  - Windows: built-in PowerShell

Run locally over stdio (default MCP transport):
    python server.py
or, after `pipx install .`:
    multimodal-mcp
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import tempfile
from enum import Enum
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from providers import (
    build_content,
    build_request,
    extract_response_text,
    normalize_provider,
    validate_provider,
)
from state import MultimodalState, make_cache_key

from pdf_support import MAX_PDF_PAGES, PdfMode, extract_pdf_pages

from jobs import JobManager
from recognition import RecognitionRequest, RecognitionRunner

from attachments import select_pasted_images

# --------------------------------------------------------------------------- #
# Configuration. Vision model only - the main reasoning model is the one the  #
# user picked in their MCP client, not configured here.                        #
# --------------------------------------------------------------------------- #
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")
API_KEY = os.getenv("API_KEY", "").strip()
MODEL_NAME = os.getenv("MODEL_NAME", "").strip()
PROVIDER = normalize_provider(os.getenv("PROVIDER"))

def _positive_float_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
        if value <= 0:
            raise ValueError
        return value
    except ValueError:
        logging.getLogger("multimodal-mcp").warning(
            "invalid %s; using %.1f", name, default
        )
        return default


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
        if value <= 0:
            raise ValueError
        return value
    except ValueError:
        logging.getLogger("multimodal-mcp").warning(
            "invalid %s; using %d", name, default
        )
        return default


POLL_WAIT_MAX_SECONDS = _positive_float_env("POLL_WAIT_MAX_SECONDS", 50.0)
UPSTREAM_CONNECT_TIMEOUT = _positive_float_env("UPSTREAM_CONNECT_TIMEOUT", 10.0)
UPSTREAM_READ_TIMEOUT = _positive_float_env("UPSTREAM_READ_TIMEOUT", 90.0)
UPSTREAM_MAX_RETRIES = _positive_int_env("UPSTREAM_MAX_RETRIES", 2)
UPSTREAM_MAX_CONCURRENCY = _positive_int_env("UPSTREAM_MAX_CONCURRENCY", 2)
JOB_TOTAL_TIMEOUT_SECONDS = _positive_float_env("JOB_TOTAL_TIMEOUT_SECONDS", 900.0)
JOB_RESULT_TTL_SECONDS = _positive_float_env("JOB_RESULT_TTL_SECONDS", 3600.0)
JOB_MAX_ENTRIES = _positive_int_env("JOB_MAX_ENTRIES", 64)
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "120") or "120")

# --------------------------------------------------------------------------- #
# Image limits. Oversized sources are the main cause of flaky recognition:     #
# large retina screenshots blow past upstream payload limits or time out.      #
# --------------------------------------------------------------------------- #
MAX_SOURCE_BYTES = 64 * 1024 * 1024  # hard reject for any resolved source
MAX_INLINE_BYTES = 4 * 1024 * 1024   # above this, re-encode before sending
MAX_INLINE_EDGE = 2048               # above this (px), downscale before sending
TARGET_EDGE = 1568                   # downscale target for the long edge

# Diagnostics go to stderr only - stdout is reserved for the MCP transport.
logger = logging.getLogger("multimodal-mcp")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("[multimodal-mcp] %(levelname)s %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

STATE = MultimodalState()


# --------------------------------------------------------------------------- #
# Default vision prompt. Override per-call via the `instruction` argument.     #
# --------------------------------------------------------------------------- #
DEFAULT_VISION_PROMPT = (
    "请详细且结构化地描述这张图片，务必包含：\n"
    "1. 整体内容与场景\n"
    "2. 图中所有可见文字（完整转录，保留原始排版与表格结构）\n"
    "3. 数字、数据、坐标轴、图表信息（转成结构化文字，不要省略数值）\n"
    "4. 关键对象、颜色、布局、UI 元素\n"
    "5. 任何其他对回答下游问题有用的细节\n"
    "用中文输出，条理清晰，不要泛泛而谈。"
)


# --------------------------------------------------------------------------- #
# MCP server.                                                                 #
# --------------------------------------------------------------------------- #
mcp = FastMCP("multimodal_mcp")


# --------------------------------------------------------------------------- #
# Helpers.                                                                    #
# --------------------------------------------------------------------------- #
RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
UPSTREAM_SEMAPHORE = asyncio.Semaphore(UPSTREAM_MAX_CONCURRENCY)
UPSTREAM_TIMEOUT = httpx.Timeout(
    connect=UPSTREAM_CONNECT_TIMEOUT,
    read=UPSTREAM_READ_TIMEOUT,
    write=UPSTREAM_READ_TIMEOUT,
    pool=UPSTREAM_CONNECT_TIMEOUT,
)


async def _post_json_once(
    client: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
) -> httpx.Response:
    return await client.post(url, json=payload, headers=headers)


async def _post_json_with_retry(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
) -> httpx.Response:
    async with UPSTREAM_SEMAPHORE:
        async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT) as client:
            for attempt in range(UPSTREAM_MAX_RETRIES + 1):
                try:
                    response = await _post_json_once(client, url, payload, headers)
                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    if attempt >= UPSTREAM_MAX_RETRIES:
                        raise RuntimeError(
                            f"upstream network failure after {attempt + 1} attempts: {exc}"
                        ) from exc
                else:
                    if response.status_code not in RETRYABLE_STATUS_CODES:
                        return response
                    if attempt >= UPSTREAM_MAX_RETRIES:
                        return response
                delay = (2.0, 5.0)[min(attempt, 1)]
                logger.warning("upstream retry %d in %.1fs: %s", attempt + 1, delay, url)
                await asyncio.sleep(delay)
    raise RuntimeError("unreachable retry state")


async def _vision_completion(
    provider: str,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    **gen_kwargs: Any,
) -> str:
    validate_provider(provider)
    url, payload, headers = build_request(
        provider,
        base_url,
        api_key,
        model,
        messages,
        **gen_kwargs,
    )

    resp = await _post_json_with_retry(url, payload, headers)
    if resp.status_code >= 400:
        raise RuntimeError(
            f"HTTP {resp.status_code} from {url} for model '{model}': "
            f"{resp.text[:500]}"
        )
    data = resp.json()

    try:
        return extract_response_text(provider, data)
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(
            f"Unexpected response shape from {url}: "
            f"{json.dumps(data, ensure_ascii=False)[:500]}"
        )


def read_clipboard_image() -> tuple[Optional[bytes], Optional[str]]:
    """Read an image from the system clipboard. Cross-platform.

    Returns:
        (raw_bytes, None) on success, (None, error_msg) if no image or
        clipboard helper not installed.

    Uses external CLI tools (no Python GUI deps):
      - macOS:   pngpaste  (brew install pngpaste)
      - Linux:   xclip     (apt install xclip / pacman -S xclip)
      - Windows: built-in PowerShell + System.Windows.Forms
    """
    system = platform.system()

    try:
        if system == "Darwin":
            if not shutil.which("pngpaste"):
                return None, "pngpaste not installed (run: brew install pngpaste)"
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = tmp.name
            try:
                proc = subprocess.run(
                    ["pngpaste", tmp_path],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if proc.returncode != 0:
                    msg = proc.stderr.strip() or "clipboard has no image"
                    return None, f"pngpaste: {msg}"
                with open(tmp_path, "rb") as f:
                    data = f.read()
                if not data:
                    return None, "pngpaste returned empty file"
                return data, None
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        elif system == "Linux":
            if not shutil.which("xclip"):
                return None, "xclip not installed (run: apt install xclip)"
            proc = subprocess.run(
                ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
                capture_output=True,
                timeout=10,
            )
            if proc.returncode != 0 or not proc.stdout:
                return None, "xclip: clipboard has no image"
            return proc.stdout, None

        elif system == "Windows":
            ps_script = (
                "Add-Type -AssemblyName System.Windows.Forms;"
                "Add-Type -AssemblyName System.Drawing;"
                "$img = [System.Windows.Forms.Clipboard]::GetImage();"
                "if ($img) {"
                "  $ms = New-Object System.IO.MemoryStream;"
                "  $img.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png);"
                "  [System.Convert]::ToBase64String($ms.ToArray());"
                "}"
            )
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=10,
            )
            # PowerShell wraps stdout at console width; strip inner newlines
            # or the base64 payload gets corrupted.
            out = re.sub(r"\s+", "", proc.stdout or "")
            if not out:
                return None, "powershell: clipboard has no image"
            try:
                return base64.b64decode(out, validate=True), None
            except Exception:
                return None, "powershell: clipboard data is not valid base64"

        else:
            return None, f"unsupported platform: {system}"

    except subprocess.TimeoutExpired:
        return None, "clipboard read timed out"
    except FileNotFoundError as exc:
        return None, f"command not found: {exc.filename}"


def _config_status() -> dict[str, object]:
    """Health snapshot. Never prints the key value, only presence."""
    provider_valid = True
    provider_error: Optional[str] = None
    try:
        validate_provider(PROVIDER)
    except ValueError as exc:
        provider_valid = False
        provider_error = str(exc)

    return {
        "base_url_set": bool(BASE_URL),
        "api_key_set": bool(API_KEY),
        "model_name": MODEL_NAME or "(not set)",
        "provider": PROVIDER,
        "provider_valid": provider_valid,
        "provider_error": provider_error,
    }


def _sniff_mime(data: bytes) -> Optional[str]:
    """Detect the real image type from magic bytes."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    return None


def _normalize_image(data: bytes) -> tuple[Optional[bytes], Optional[str], Optional[str]]:
    """Validate and, when oversized, downscale/re-encode an image.

    Small images pass through untouched (zero quality loss). Large ones are
    downscaled to TARGET_EDGE on the long edge and re-encoded as JPEG, which
    keeps retina screenshots well under upstream payload limits.

    Returns (bytes, mime, None) on success, (None, None, error_msg) on failure.
    """
    mime = _sniff_mime(data)
    if mime is None:
        return None, None, (
            "source is not a supported image (PNG/JPEG/GIF/WebP/BMP); "
            "refusing to send arbitrary file content to the vision API"
        )
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            if len(data) <= MAX_INLINE_BYTES and max(im.size) <= MAX_INLINE_EDGE:
                return data, mime, None

            orig_size = im.size
            if getattr(im, "is_animated", False):
                im.seek(0)
            scale = TARGET_EDGE / max(im.size)
            if scale < 1:
                im = im.resize(
                    (max(1, round(im.size[0] * scale)), max(1, round(im.size[1] * scale))),
                    Image.LANCZOS,
                )
            if im.mode in ("RGBA", "LA", "P"):
                # Flatten alpha onto white; JPEG has no alpha channel.
                bg = Image.new("RGB", im.size, (255, 255, 255))
                rgba = im.convert("RGBA")
                bg.paste(rgba, mask=rgba.split()[-1])
                im = bg
            elif im.mode != "RGB":
                im = im.convert("RGB")
            out = b""
            for quality in (85, 70, 55):
                buf = io.BytesIO()
                im.save(buf, format="JPEG", quality=quality, optimize=True)
                out = buf.getvalue()
                if len(out) <= MAX_INLINE_BYTES:
                    break
            logger.info(
                "normalized image: %s %dx%d %.1fMB -> jpeg %dx%d %.1fMB",
                mime, orig_size[0], orig_size[1], len(data) / 1e6,
                im.size[0], im.size[1], len(out) / 1e6,
            )
            return out, "image/jpeg", None
    except Exception as exc:  # noqa: BLE001 - corrupt/truncated images land here
        return None, None, f"image decode failed: {type(exc).__name__}: {exc}"


async def _resolve_image_source(image: Optional[str]) -> tuple[Optional[bytes], Optional[str]]:
    """Resolve any image source to raw bytes.

    Dispatch order:
      1. None / empty      -> read from system clipboard (screenshots)
      2. anything else    -> delegate to _resolve_binary_source

    Returns (raw_bytes, None) on success, (None, error_msg) on failure.
    """
    if not image:
        data, err = read_clipboard_image()
        if not data:
            return None, err or "no image in clipboard"
        if len(data) > MAX_SOURCE_BYTES:
            return None, f"clipboard image exceeds {MAX_SOURCE_BYTES // (1024 * 1024)}MB limit"
        logger.info("resolved image source: clipboard, %.2fMB", len(data) / 1e6)
        return data, None
    return await _resolve_binary_source(image, max_bytes=MAX_SOURCE_BYTES)


async def _resolve_binary_source(
    source: str,
    *,
    max_bytes: int,
) -> tuple[Optional[bytes], Optional[str]]:
    src = source.strip()
    if src.startswith(("http://", "https://")):
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
                async with client.stream("GET", src) as response:
                    response.raise_for_status()
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes(65536):
                        total += len(chunk)
                        if total > max_bytes:
                            return None, f"download exceeds {max_bytes // (1024 * 1024)}MB limit"
                        chunks.append(chunk)
            return b"".join(chunks), None
        except httpx.HTTPStatusError as exc:
            return None, f"download HTTP {exc.response.status_code}: {exc.response.text[:200]}"
        except Exception as exc:
            return None, f"download failed: {type(exc).__name__}: {exc}"
    if src.startswith("data:"):
        if "," not in src:
            return None, "invalid data URI (missing comma)"
        encoded = src.split(",", 1)[1]
    elif os.path.exists(src):
        try:
            if os.path.getsize(src) > max_bytes:
                return None, f"file exceeds {max_bytes // (1024 * 1024)}MB limit"
            with open(src, "rb") as file:
                return file.read(), None
        except OSError as exc:
            return None, f"read file failed: {type(exc).__name__}: {exc}"
    else:
        encoded = src
    try:
        data = base64.b64decode(re.sub(r"\s+", "", encoded), validate=True)
    except Exception:
        return None, "source is not a URL, data URI, existing file path, or valid base64"
    if len(data) > max_bytes:
        return None, f"source exceeds {max_bytes // (1024 * 1024)}MB limit"
    return data, None


def _fmt_error(stage: str, exc: Exception) -> str:
    """Consistent error formatting for tool returns."""
    return f"[{stage} failed] {type(exc).__name__}: {exc}"


def _append_meta(text: str, *, image_ids: list[str], cache_hit: bool) -> str:
    payload: dict[str, object] = {"cache_hit": cache_hit}
    if len(image_ids) == 1:
        payload["image_id"] = image_ids[0]
    else:
        payload["image_ids"] = image_ids
    return f"{text}\n\n<!-- multimodal-meta {json.dumps(payload, separators=(',', ':'))} -->"


async def _describe_prepared_images(
    images: list[tuple[bytes, str]],
    prompt: str,
    detail: DetailLevel,
) -> str:
    detail_value = detail.value if isinstance(detail, DetailLevel) else "high"
    image_bytes = [data for data, _mime in images]
    image_ids = [STATE.put_image(data, mime) for data, mime in images]
    cache_key = make_cache_key(
        image_bytes,
        MODEL_NAME,
        PROVIDER,
        detail_value,
        prompt,
    )
    cached = STATE.get_cached(cache_key)
    if cached is not None:
        logger.info("description cache hit: %s", cache_key[:8])
        return _append_meta(cached, image_ids=image_ids, cache_hit=True)

    content: list[dict[str, Any]] = []
    for data, mime in images:
        image_b64 = base64.b64encode(data).decode()
        content.extend(build_content(PROVIDER, image_b64, mime, detail=detail_value))
    content.append({"type": "text", "text": prompt})

    description = await _vision_completion(
        PROVIDER,
        BASE_URL,
        API_KEY,
        MODEL_NAME,
        [{"role": "user", "content": content}],
    )
    STATE.put_cached(cache_key, description)
    return _append_meta(description, image_ids=image_ids, cache_hit=False)


async def _describe_for_runner(
    images: list[tuple[bytes, str]],
    prompt: str,
    detail: str,
) -> str:
    return await _describe_prepared_images(images, prompt, DetailLevel(detail))


def _extract_pdf_for_runner(raw: bytes, pages: Optional[str], mode: str):
    return extract_pdf_pages(raw, pages, PdfMode(mode))


MAX_BATCH_IMAGES = 8


async def _prepare_image(source: Optional[str]) -> tuple[Optional[tuple[bytes, str]], Optional[str]]:
    raw, err = await _resolve_image_source(source)
    if raw is None:
        return None, err or "no image source"
    normalized, mime, nerr = _normalize_image(raw)
    if normalized is None or mime is None:
        return None, nerr or "image decode failed"
    return (normalized, mime), None


RUNNER = RecognitionRunner(
    prepare_image=_prepare_image,
    describe_images=_describe_for_runner,
    get_image=STATE.get_image,
    resolve_binary=_resolve_binary_source,
    extract_pdf=_extract_pdf_for_runner,
    normalize_image=_normalize_image,
    max_source_bytes=MAX_SOURCE_BYTES,
)

JOBS = JobManager(
    result_ttl=JOB_RESULT_TTL_SECONDS,
    max_entries=JOB_MAX_ENTRIES,
    total_timeout=JOB_TOTAL_TIMEOUT_SECONDS,
)


# --------------------------------------------------------------------------- #
# Input model.                                                                #
# --------------------------------------------------------------------------- #
class DetailLevel(str, Enum):
    LOW = "low"
    HIGH = "high"


class StateTarget(str, Enum):
    CACHE = "cache"
    IMAGES = "images"
    ALL = "all"


class RecognitionKind(str, Enum):
    IMAGE = "image"
    IMAGES = "images"
    PDF = "pdf"
    IMAGE_ID = "image_id"


def _validate_recognition_request(request: RecognitionRequest) -> None:
    count = len(request.sources)
    if request.kind in {"image", "pdf", "image_id"} and count != 1:
        raise ValueError(f"{request.kind} requires exactly one source")
    if request.kind == "images" and not 1 <= count <= MAX_BATCH_IMAGES:
        raise ValueError("images requires 1 to 8 sources")
    if request.kind == "images" and sum(not source.strip() for source in request.sources) > 1:
        raise ValueError("only one clipboard image is allowed")
    if request.kind == "image_id" and not request.instruction.strip():
        raise ValueError("image_id recognition requires a question")


def _submit_request(request: RecognitionRequest):
    validate_provider(PROVIDER)
    _validate_recognition_request(request)
    key = request.dedupe_key(MODEL_NAME, PROVIDER)

    async def execute(job):
        return await RUNNER.run(job, request)

    return JOBS.submit(kind=request.kind, dedupe_key=key, runner=execute)


async def _wait_for_compat_result(job) -> str:
    await JOBS.wait_until_done(job.job_id)
    snapshot = JOBS.snapshot(job.job_id)
    if snapshot["status"] in {"completed", "partial"}:
        return str(snapshot.get("result", ""))
    raise RuntimeError(str(snapshot.get("error", snapshot["status"])))


async def _run_compat_request(stage: str, request: RecognitionRequest) -> str:
    try:
        job = _submit_request(request)
        return await _wait_for_compat_result(job)
    except Exception as exc:
        return _fmt_error(stage, exc)


# --------------------------------------------------------------------------- #
# Tool.                                                                        #
# --------------------------------------------------------------------------- #
@mcp.tool(
    name="describe_image",
    annotations={
        "title": "Describe Image",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def describe_image(
    image: Optional[str] = Field(
        default=None,
        description=(
            "Image source - auto-detected by content. Accepts: "
            "(1) http(s) URL - downloaded; "
            "(2) data URI 'data:image/png;base64,...' - extracted; "
            "(3) local file path - read from disk; "
            "(4) raw base64 string - used as-is; "
            "(5) empty/omitted - read from the SYSTEM CLIPBOARD (use this when "
            "the user took a screenshot and says 'look at my screenshot' but "
            "did NOT paste the image into the chat)."
        ),
    ),
    instruction: Optional[str] = Field(
        default=None,
        description=(
            "Optional instruction overriding the default vision prompt. "
            "Use this to focus the description on what you actually need, e.g. "
            "'只提取表格中的数字', '识别这张截图里的所有 UI 组件', "
            "'把流程图转成 Mermaid 代码'."
        ),
    ),
    detail: DetailLevel = Field(
        default=DetailLevel.HIGH,
        description=(
            "Image processing detail. 'high' for OCR / chart / dense text; "
            "'low' for a fast rough summary. Some backends ignore this field."
        ),
    ),
) -> str:
    '''Convert an image into structured text so a text-only model can "see" it.

    Call this tool whenever the current main model cannot view images directly
    (e.g. glm-5.2, deepseek-v4-pro, qwen-text) but the user wants you to look
    at an image. The image source is auto-detected from the `image` argument:

      - http(s) URL          -> downloaded
      - data: URI            -> base64 extracted
      - local file path      -> read from disk
      - raw base64 string    -> used as-is
      - empty / None         -> read from the system clipboard

    The clipboard path is what makes screenshots work without pasting: the
    user takes a screenshot (Cmd+Shift+4 / Win+Shift+S / scrot) so the image
    lives in the OS clipboard, then says something like "看下我的截图" or
    "look at my screenshot" in the chat. You call this tool with no `image`
    argument; it reads the clipboard and returns the description.

    The image is sent to the configured vision model (any OpenAI-compatible
    multimodal endpoint: qwen3.7-plus, qwen-vl-max, gpt-4o, llava, etc.) and
    returned as structured Chinese text covering:

      - overall content and scene
      - all visible text transcribed verbatim (preserving layout / tables)
      - numbers, data, axes, chart values (as structured text, not omitted)
      - key objects, colors, layout, UI elements
      - any other detail useful for downstream reasoning

    This tool does NOT answer questions about the image. It only converts the
    image to text. After it returns, YOU (the main model) do the reasoning and
    answer the user yourself, as if you had read the description.

    Args:
        image (Optional[str]): URL / data URI / file path / base64 / empty.
            Empty reads from the system clipboard.
        instruction (Optional[str]): custom vision instruction; if omitted,
            a comprehensive default prompt is used.
        detail (DetailLevel): 'high' (default) for OCR/dense content, 'low'
            for a quick rough summary.

    Returns:
        str: Markdown text describing the image on success.
             On failure: '[describe_image failed] <ErrorType>: <detail>'.

    When to call:
        - User pasted an image attachment / gave a URL / gave a file path.
        - User says "看下我的截图" / "look at my screenshot" / "我刚截了张图"
          (leave `image` empty - the tool reads the clipboard).
        - You need OCR, table extraction, or chart values from a picture.
        - IMPORTANT: the message contains an image placeholder like
          `[Image 1]`, `[Image N]`, `[图片]`, or `[Image attachment]` - this
          means the user pasted an image but the client/gateway replaced the
          real image data with a placeholder because the main model has no
          vision. The image still lives in the OS clipboard. Call this tool
          with `image` empty to read it from the clipboard, even if the user
          sent no text at all.

    When NOT to call:
        - The user only sent text with no mention of any image.
        - You already have a textual description and no new image arrived.

    Examples:
        - "describe this: https://x.com/a.png"        -> image=<url>
        - "看下我的截图"                                -> image omitted
        - "识别 /tmp/chart.png 里的表格"               -> image="/tmp/chart.png"
        - "把这张流程图(base64)转成 Mermaid"            -> instruction="...", image=<b64>
    '''
    instruction = instruction if isinstance(instruction, (str, type(None))) else instruction.default
    detail = detail if isinstance(detail, DetailLevel) else detail.default

    request = RecognitionRequest(
        kind="image",
        sources=[image or ""],
        instruction=instruction or DEFAULT_VISION_PROMPT,
        detail=detail.value,
    )
    return await _run_compat_request("describe_image", request)


@mcp.tool(
    name="describe_images",
    annotations={
        "title": "Describe Multiple Images",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def describe_images(
    images: list[str] = Field(description="Ordered image sources; 1 to 8 items."),
    instruction: Optional[str] = Field(default=None, description="Joint description or comparison instruction."),
    detail: DetailLevel = Field(default=DetailLevel.HIGH),
) -> str:
    instruction = instruction if isinstance(instruction, (str, type(None))) else instruction.default
    detail = detail if isinstance(detail, DetailLevel) else detail.default

    request = RecognitionRequest(
        kind="images",
        sources=images,
        instruction=instruction or (
            "请按输入顺序联合描述这些图片。先分别转录每张图的文字和关键细节，"
            "再指出图片之间的相同点、差异和连续关系。不要省略数字。"
        ),
        detail=detail.value,
    )
    return await _run_compat_request("describe_images", request)


@mcp.tool(
    name="describe_pasted_images",
    annotations={
        "title": "Describe Pasted Images From Cache",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def describe_pasted_images(
    count: int = Field(
        ge=1,
        le=8,
        description="Number of pasted image placeholders (1 to 8).",
    ),
    instruction: Optional[str] = Field(
        default=None,
        description="Optional custom vision instruction.",
    ),
    detail: DetailLevel = Field(default=DetailLevel.HIGH),
) -> str:
    """Resolve pasted OpenCode image attachments from cache.

    Reads images from ~/.cache/opencode/multimodal-attachments, selects the
    newest `count` supported images, restores original paste order, and sends
    them to the vision model for structured text recognition.

    This tool does NOT use the system clipboard. On failure the caller should
    fall back to describe_image with an empty `image` argument.
    """
    instruction = instruction if isinstance(instruction, (str, type(None))) else instruction.default
    detail = detail if isinstance(detail, DetailLevel) else detail.default

    paths, err = select_pasted_images(count)
    if err:
        return _fmt_error("describe_pasted_images", RuntimeError(err))

    request = RecognitionRequest(
        kind="images",
        sources=[str(p) for p in paths],
        instruction=instruction or (
            "请按输入顺序联合描述这些图片。先分别转录每张图的文字和关键细节，"
            "再指出图片之间的相同点、差异和连续关系。不要省略数字。"
        ),
        detail=detail.value,
    )
    return await _run_compat_request("describe_pasted_images", request)


@mcp.tool(name="ask_image", annotations={"title": "Ask About Stored Image", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
async def ask_image(
    image_id: str = Field(description="Image ID returned by describe_image, describe_images, or describe_pdf."),
    question: str = Field(description="Focused follow-up question, up to 4000 characters."),
    detail: DetailLevel = Field(default=DetailLevel.HIGH),
) -> str:
    detail = detail if isinstance(detail, DetailLevel) else detail.default
    question = question.strip()

    request = RecognitionRequest(
        kind="image_id",
        sources=[image_id],
        instruction=question,
        detail=detail.value,
    )
    return await _run_compat_request("ask_image", request)


@mcp.tool(name="multimodal_cache_status", annotations={"title": "Multimodal Cache Status", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
async def multimodal_cache_status() -> str:
    return json.dumps(
        {"state": STATE.stats(), "jobs": JOBS.stats()},
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool(name="clear_multimodal_state", annotations={"title": "Clear Multimodal State", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False})
async def clear_multimodal_state(
    target: StateTarget = Field(default=StateTarget.ALL),
) -> str:
    target = target if isinstance(target, StateTarget) else target.default
    cleared = STATE.clear(target.value)
    jobs_cancelled = 0
    if target.value == "all":
        jobs_cancelled = JOBS.clear()
    result = dict(cleared)
    result["jobs_cancelled"] = jobs_cancelled
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool(name="describe_pdf", annotations={"title": "Describe PDF", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
async def describe_pdf(
    document: str = Field(description="PDF source: URL, data URI, local path, or raw base64."),
    pages: Optional[str] = Field(default=None, description="1-based selection such as '1-3,5'; default first 20 pages."),
    mode: PdfMode = Field(default=PdfMode.AUTO),
    instruction: Optional[str] = Field(default=None, description="OCR focus for visually processed pages."),
    detail: DetailLevel = Field(default=DetailLevel.HIGH),
) -> str:
    instruction = instruction if isinstance(instruction, (str, type(None))) else instruction.default
    detail = detail if isinstance(detail, DetailLevel) else detail.default
    mode = mode if isinstance(mode, PdfMode) else mode.default
    pages = pages if isinstance(pages, (str, type(None))) else pages.default
    document = document if isinstance(document, str) else document.default

    request = RecognitionRequest(
        kind="pdf",
        sources=[document],
        instruction=instruction or DEFAULT_VISION_PROMPT,
        detail=detail.value,
        pages=pages,
        pdf_mode=mode.value,
    )
    return await _run_compat_request("describe_pdf", request)


@mcp.tool(name="start_recognition", annotations={"title": "Start Recognition", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
async def start_recognition(
    kind: RecognitionKind,
    sources: list[str],
    instruction: Optional[str] = None,
    detail: DetailLevel = DetailLevel.HIGH,
    pages: Optional[str] = None,
    pdf_mode: PdfMode = PdfMode.AUTO,
) -> str:
    instruction_text = (instruction or "").strip()
    if kind != RecognitionKind.IMAGE_ID and not instruction_text:
        instruction_text = DEFAULT_VISION_PROMPT
    request = RecognitionRequest(
        kind=kind.value,
        sources=sources,
        instruction=instruction_text,
        detail=detail.value,
        pages=pages,
        pdf_mode=pdf_mode.value,
    )
    try:
        job = _submit_request(request)
        return json.dumps(JOBS.snapshot(job.job_id), ensure_ascii=False, indent=2)
    except Exception as exc:
        return _fmt_error("start_recognition", exc)


@mcp.tool(name="get_recognition", annotations={"title": "Get Recognition", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
async def get_recognition(
    job_id: str,
    wait_seconds: float = 0,
    include_partial: bool = True,
) -> str:
    if not 0 <= wait_seconds <= POLL_WAIT_MAX_SECONDS:
        return _fmt_error(
            "get_recognition",
            ValueError(f"wait_seconds must be between 0 and {POLL_WAIT_MAX_SECONDS:g}"),
        )
    try:
        if wait_seconds:
            await JOBS.wait(job_id, wait_seconds)
        return json.dumps(
            JOBS.snapshot(job_id, include_partial=include_partial),
            ensure_ascii=False,
            indent=2,
        )
    except KeyError:
        return _fmt_error(
            "get_recognition",
            RuntimeError("job_id expired or does not exist"),
        )


@mcp.tool(name="cancel_recognition", annotations={"title": "Cancel Recognition", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False})
async def cancel_recognition(job_id: str) -> str:
    try:
        job = JOBS.cancel(job_id)
        if job.task is not None and not job.task.done():
            await asyncio.sleep(0)
        return json.dumps(JOBS.snapshot(job_id), ensure_ascii=False, indent=2)
    except KeyError:
        return _fmt_error(
            "cancel_recognition",
            RuntimeError("job_id expired or does not exist"),
        )


@mcp.tool(
    name="multimodal_config_status",
    annotations={
        "title": "Multimodal Config Status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def multimodal_config_status() -> str:
    '''Report whether the required vision env vars are set (never the values).

    Call once after first wiring the server into a client, to confirm
    PROVIDER, BASE_URL, API_KEY and MODEL_NAME are configured and that
    PROVIDER is supported.
    The API key itself is never exposed; only a boolean.

    Returns:
        str: JSON with base_url_set, api_key_set, model_name, provider,
        provider_valid and provider_error.
    '''
    return json.dumps(_config_status(), ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
# Entry point.                                                                #
# --------------------------------------------------------------------------- #
def main() -> None:
    """Run the MCP server over stdio (default MCP transport)."""
    mcp.run()


if __name__ == "__main__":
    main()
