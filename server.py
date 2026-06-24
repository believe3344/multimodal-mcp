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
import json
import os
import platform
import shutil
import subprocess
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

load_dotenv(Path(__file__).resolve().parent / ".env")

# --------------------------------------------------------------------------- #
# Configuration. Vision model only - the main reasoning model is the one the  #
# user picked in their MCP client, not configured here.                      #
# --------------------------------------------------------------------------- #
VISION_BASE_URL = os.getenv("VISION_BASE_URL", "").rstrip("/")
VISION_API_KEY = os.getenv("VISION_API_KEY", "").strip()
VISION_MODEL = os.getenv("VISION_MODEL", "").strip()

REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "120") or "120")


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

    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {"model": model, "messages": messages}
    payload.update(gen_kwargs)

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code >= 400:
            # Surface the upstream body so misconfig (auth, model name, quota)
            # is immediately visible to the caller.
            raise RuntimeError(
                f"HTTP {resp.status_code} from {base_url} for model '{model}': "
                f"{resp.text[:500]}"
            )
        data = resp.json()

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(
            f"Unexpected response shape from {base_url}: "
            f"{json.dumps(data, ensure_ascii=False)[:500]}"
        )


def _build_image_content(image: str, text: str, detail: str = "high") -> list[dict[str, Any]]:
    """Build the OpenAI multimodal user-content list.

    `image` accepts:
      - http(s) URL
      - data URI like 'data:image/png;base64,...'
      - raw base64 string (will be wrapped as PNG)
    """
    if image.startswith(("http://", "https://", "data:")):
        img_url = image
    else:
        # Assume raw base64. PNG is a safe default for screenshots.
        img_url = f"data:image/png;base64,{image.strip()}"

    return [
        {
            "type": "image_url",
            "image_url": {"url": img_url, "detail": detail},
        },
        {"type": "text", "text": text},
    ]


def read_clipboard_image() -> tuple[Optional[str], Optional[str]]:
    """Read an image from the system clipboard. Cross-platform.

    Returns:
        (base64_str, mime_type) on success, (None, None) if no image or
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
                return base64.b64encode(data).decode(), "image/png"
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
            return base64.b64encode(proc.stdout).decode(), "image/png"

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
            out = proc.stdout.strip() if proc.stdout else ""
            if not out:
                return None, "powershell: clipboard has no image"
            return out, "image/png"

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
    }


async def _resolve_image_source(image: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Resolve any image source to a base64 string.

    Dispatch order:
      1. None / empty      -> read from system clipboard (screenshots)
      2. http(s) URL       -> download and base64-encode
      3. data: URI         -> extract base64 part
      4. existing file path -> read and base64-encode
      5. anything else    -> treat as raw base64 string

    Returns (base64_str, None) on success, (None, error_msg) on failure.
    """
    if not image:
        b64, err = read_clipboard_image()
        if not b64:
            return None, err or "no image in clipboard"
        return b64, None

    src = image.strip()

    if src.startswith(("http://", "https://")):
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
                resp = await client.get(src)
                resp.raise_for_status()
                data = resp.content
            if not data:
                return None, "downloaded image is empty"
            return base64.b64encode(data).decode(), None
        except httpx.HTTPStatusError as exc:
            return None, f"download HTTP {exc.response.status_code}: {exc.response.text[:200]}"
        except Exception as exc:  # noqa: BLE001
            return None, f"download failed: {type(exc).__name__}: {exc}"

    if src.startswith("data:"):
        if "," not in src:
            return None, "invalid data URI (missing comma)"
        return src.split(",", 1)[1], None

    if os.path.exists(src):
        try:
            with open(src, "rb") as f:
                data = f.read()
            if not data:
                return None, "file is empty"
            return base64.b64encode(data).decode(), None
        except OSError as exc:
            return None, f"read file failed: {type(exc).__name__}: {exc}"

    try:
        base64.b64decode(src, validate=True)
    except Exception:
        return None, (
            "input is not a URL, data URI, existing file path, or valid base64. "
            "Pass an http(s) URL, a data:image/...;base64,... URI, a local file "
            "path, raw base64, or leave empty to read from the system clipboard."
        )
    return src, None


def _fmt_error(stage: str, exc: Exception) -> str:
    """Consistent error formatting for tool returns."""
    return f"[{stage} failed] {type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------- #
# Input model.                                                                #
# --------------------------------------------------------------------------- #
class DetailLevel(str, Enum):
    LOW = "low"
    HIGH = "high"


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
    image_b64, err = await _resolve_image_source(image)
    if not image_b64:
        return _fmt_error("describe_image", RuntimeError(err or "no image source"))

    prompt = instruction or DEFAULT_VISION_PROMPT
    content = _build_image_content(image_b64, prompt, detail.value)
    messages = [{"role": "user", "content": content}]
    try:
        description = await _chat_completion(
            VISION_BASE_URL, VISION_API_KEY, VISION_MODEL, messages
        )
    except Exception as exc:  # noqa: BLE001 - we surface any upstream failure
        return _fmt_error("describe_image", exc)
    return description


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
