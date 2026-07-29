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
import time
from enum import Enum
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from state import MultimodalState, make_cache_key

from pdf_support import MAX_PDF_PAGES, PdfMode, extract_pdf_pages

# --------------------------------------------------------------------------- #
# Configuration. Vision model only - the main reasoning model is the one the  #
# user picked in their MCP client, not configured here.                        #
# --------------------------------------------------------------------------- #
VISION_BASE_URL = os.getenv("VISION_BASE_URL", "").rstrip("/")
VISION_API_KEY = os.getenv("VISION_API_KEY", "").strip()
VISION_MODEL = os.getenv("VISION_MODEL", "").strip()
VISION_API_STYLE = os.getenv("VISION_API_STYLE", "chat").strip().lower()

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
def _extract_responses_text(data: dict[str, Any]) -> str:
    """Extract assistant text from an OpenAI Responses API response.

    Shape: data["output"][*]["content"][*] where type == "output_text".
    """
    texts: list[str] = []
    for item in data.get("output", []):
        for part in item.get("content", []):
            if part.get("type") == "output_text" and part.get("text"):
                texts.append(part["text"])
    if not texts:
        raise KeyError("no output_text in response")
    return "\n".join(texts)


async def _chat_completion(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    **gen_kwargs: Any,
) -> str:
    """Call an OpenAI-compatible /v1/chat/completions endpoint.

    Returns the assistant message text from the first choice.
    """
    if not api_key:
        raise RuntimeError(f"Missing API key for model '{model}'")
    if not base_url:
        raise RuntimeError(f"Missing base URL for model '{model}'")
    if not model:
        raise RuntimeError("Missing VISION_MODEL")

    if VISION_API_STYLE == "responses":
        url = f"{base_url}/responses"
        payload: dict[str, Any] = {"model": model, "input": messages}
    else:
        url = f"{base_url}/chat/completions"
        payload = {"model": model, "messages": messages}
    payload.update(gen_kwargs)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"HTTP {resp.status_code} from {url} for model '{model}': "
                f"{resp.text[:500]}"
            )
        data = resp.json()

    try:
        if VISION_API_STYLE == "responses":
            return _extract_responses_text(data)
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(
            f"Unexpected response shape from {url}: "
            f"{json.dumps(data, ensure_ascii=False)[:500]}"
        )


def _build_image_content(
    image_b64: str, mime: str, text: str, detail: str = "high"
) -> list[dict[str, Any]]:
    """Build the multimodal user-content list for one image + text.

    Chat Completions uses `image_url` / `text`; Responses API uses
    `input_image` / `input_text`. `mime` is the sniffed real type.
    """
    img_url = f"data:{mime};base64,{image_b64}"

    if VISION_API_STYLE == "responses":
        return [
            {"type": "input_image", "image_url": img_url, "detail": detail},
            {"type": "input_text", "text": text},
        ]
    return [
        {"type": "image_url", "image_url": {"url": img_url, "detail": detail}},
        {"type": "text", "text": text},
    ]


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
    return {
        "vision_base_url_set": bool(VISION_BASE_URL),
        "vision_api_key_set": bool(VISION_API_KEY),
        "vision_model": VISION_MODEL or "(not set)",
        "vision_api_style": VISION_API_STYLE,
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
        VISION_MODEL,
        VISION_API_STYLE,
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
        image_parts = _build_image_content(image_b64, mime, "", detail_value)
        content.extend(image_parts[:1])
    if VISION_API_STYLE == "responses":
        content.append({"type": "input_text", "text": prompt})
    else:
        content.append({"type": "text", "text": prompt})

    description = await _chat_completion(
        VISION_BASE_URL,
        VISION_API_KEY,
        VISION_MODEL,
        [{"role": "user", "content": content}],
    )
    STATE.put_cached(cache_key, description)
    return _append_meta(description, image_ids=image_ids, cache_hit=False)


MAX_BATCH_IMAGES = 8


async def _prepare_image(source: Optional[str]) -> tuple[Optional[tuple[bytes, str]], Optional[str]]:
    raw, err = await _resolve_image_source(source)
    if raw is None:
        return None, err or "no image source"
    normalized, mime, nerr = _normalize_image(raw)
    if normalized is None or mime is None:
        return None, nerr or "image decode failed"
    return (normalized, mime), None


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

    prepared, err = await _prepare_image(image)
    if prepared is None:
        return _fmt_error("describe_image", RuntimeError(err or "no image source"))

    prompt = instruction or DEFAULT_VISION_PROMPT
    t0 = time.monotonic()
    try:
        description = await _describe_prepared_images([prepared], prompt, detail)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "upstream call failed after %.1fs (model %s): %s",
            time.monotonic() - t0, VISION_MODEL or "(unset)", exc,
        )
        return _fmt_error("describe_image", exc)
    logger.info(
        "describe_image ok: %.1fs, reply %d chars",
        time.monotonic() - t0, len(description),
    )
    return description


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

    if not 1 <= len(images) <= MAX_BATCH_IMAGES:
        return _fmt_error("describe_images", ValueError("requires 1 to 8 images"))
    if sum(not item.strip() for item in images) > 1:
        return _fmt_error("describe_images", ValueError("only one clipboard image is allowed"))
    prepared: list[tuple[bytes, str]] = []
    for index, source in enumerate(images, start=1):
        item, err = await _prepare_image(source or None)
        if item is None:
            return _fmt_error("describe_images", RuntimeError(f"image {index}: {err}"))
        prepared.append(item)
    prompt = instruction or (
        "请按输入顺序联合描述这些图片。先分别转录每张图的文字和关键细节，"
        "再指出图片之间的相同点、差异和连续关系。不要省略数字。"
    )
    try:
        return await _describe_prepared_images(prepared, prompt, detail)
    except Exception as exc:
        return _fmt_error("describe_images", exc)


@mcp.tool(name="ask_image", annotations={"title": "Ask About Stored Image", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
async def ask_image(
    image_id: str = Field(description="Image ID returned by describe_image, describe_images, or describe_pdf."),
    question: str = Field(description="Focused follow-up question, up to 4000 characters."),
    detail: DetailLevel = Field(default=DetailLevel.HIGH),
) -> str:
    detail = detail if isinstance(detail, DetailLevel) else detail.default
    question = question.strip()
    if not question or len(question) > 4000:
        return _fmt_error("ask_image", ValueError("question must contain 1 to 4000 characters"))
    entry = STATE.get_image(image_id)
    if entry is None:
        return _fmt_error("ask_image", RuntimeError("image_id expired or does not exist; describe the image again"))
    try:
        return await _describe_prepared_images([(entry.data, entry.mime)], question, detail)
    except Exception as exc:
        return _fmt_error("ask_image", exc)


@mcp.tool(name="multimodal_cache_status", annotations={"title": "Multimodal Cache Status", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
async def multimodal_cache_status() -> str:
    return json.dumps(STATE.stats(), ensure_ascii=False, indent=2)


@mcp.tool(name="clear_multimodal_state", annotations={"title": "Clear Multimodal State", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False})
async def clear_multimodal_state(
    target: StateTarget = Field(default=StateTarget.ALL),
) -> str:
    target = target if isinstance(target, StateTarget) else target.default
    return json.dumps(STATE.clear(target.value), ensure_ascii=False, indent=2)


PDF_VISION_BATCH_SIZE = 4


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

    if not document.strip():
        return _fmt_error("describe_pdf", ValueError("document is required"))
    raw, err = await _resolve_binary_source(document, max_bytes=MAX_SOURCE_BYTES)
    if raw is None:
        return _fmt_error("describe_pdf", RuntimeError(err or "cannot read PDF"))
    try:
        pdf_pages = extract_pdf_pages(raw, pages, mode)
        sections: list[str] = []
        pending: list[tuple[int, tuple[bytes, str]]] = []
        prompt = instruction or DEFAULT_VISION_PROMPT

        async def flush_pending() -> None:
            if not pending:
                return
            page_numbers = [number for number, _image in pending]
            batch_prompt = (
                f"以下图片依次对应 PDF 页码 {page_numbers}。必须以 `## Page N` "
                f"作为每页输出的一级标题，按页码顺序输出。\n\n{prompt}"
            )
            result = await _describe_prepared_images(
                [image for _number, image in pending],
                batch_prompt,
                detail,
            )
            sections.append(result)
            pending.clear()

        for page in pdf_pages:
            if page.image is None:
                await flush_pending()
                text = page.text or f"[page {page.number} has no extractable text]"
                sections.append(f"## Page {page.number}\n\n{text}")
                continue
            normalized, mime, nerr = _normalize_image(page.image)
            if normalized is None or mime is None:
                raise RuntimeError(f"page {page.number}: {nerr}")
            pending.append((page.number, (normalized, mime)))
            if len(pending) == PDF_VISION_BATCH_SIZE:
                await flush_pending()
        await flush_pending()
        return "\n\n".join(sections)
    except Exception as exc:
        return _fmt_error("describe_pdf", exc)


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
    VISION_BASE_URL, VISION_API_KEY and VISION_MODEL are all configured.
    The API key itself is never exposed; only a boolean.

    Returns:
        str: JSON with vision_base_url_set, vision_api_key_set, vision_model.
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
