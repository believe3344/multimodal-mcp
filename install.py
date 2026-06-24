#!/usr/bin/env python3
"""
Cross-platform installer for multimodal-mcp.

Detects installed MCP clients and configures them automatically:
  - opencode
  - Claude Desktop
  - Claude Code
  - Cursor
  - Codex CLI
  - Windsurf (rules only, MCP config via UI)
  - Cline (rules only, MCP config via UI)

Run:
    python install.py          # interactive
    python install.py --yes    # skip confirmations

Works on macOS, Linux, Windows.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

PROJECT_DIR = Path(__file__).resolve().parent
VENV_DIR = PROJECT_DIR / ".venv"
ENV_FILE = PROJECT_DIR / ".env"
ENV_EXAMPLE = PROJECT_DIR / ".env.example"
SERVER_PY = PROJECT_DIR / "server.py"
REQUIREMENTS = PROJECT_DIR / "requirements.txt"
SERVER_NAME = "multimodal"

RULES_MARKER_START = "<!-- multimodal-mcp rules start -->"
RULES_MARKER_END = "<!-- multimodal-mcp rules end -->"

RULES_BLOCK = f"""{RULES_MARKER_START}
## multimodal-mcp 图片识别规则

1. 当用户提到任何图片（截图 / 照片 / 图表 / URL / 文件路径 / base64）或说"看下我的截图 / 刚截的图 / 剪贴板里的图 / look at my screenshot"，且当前主模型不支持视觉时，调用 `describe_image` MCP 工具把图片转成文字，再基于描述回答用户。不要拒绝、不要说"我看不到图片"。

2. `describe_image` 的 `image` 参数自动分发：传 http(s) URL 会下载；传 data URI 会提取 base64；传本地文件路径会读取；传 raw base64 会直接用；留空（用户截图场景）会从系统剪贴板读取。根据用户给的信息决定传什么：有地址传地址，用户说"我的截图"但没给地址就留空。

3. 当消息里出现 `[Image 1]`、`[Image N]`、`[图片]`、`[Image attachment]` 等占位符（说明用户粘贴了图片附件，但客户端或网关把真实图片替换成占位符），立即调用 `describe_image`，`image` 留空——工具从系统剪贴板读取用户刚截图/粘贴的图片。即使用户没打字、只发了图片，也要这么做。拿到描述后主动告诉用户你看到了什么，并询问需要做什么。

4. 工具返回的是图片文字描述，不是最终答案。拿到描述后由主模型自己推理并回答用户。
{RULES_MARKER_END}
"""


# --------------------------------------------------------------------------- #
# System detection.                                                           #
# --------------------------------------------------------------------------- #
def detect_system() -> str:
    s = platform.system()
    if s == "Darwin":
        return "macos"
    if s == "Linux":
        return "linux"
    if s == "Windows":
        return "windows"
    return s.lower()


def check_dependencies(system: str) -> list[str]:
    """Return list of missing dependency hints."""
    missing = []
    if system == "macos":
        if not shutil.which("pngpaste"):
            missing.append("pngpaste  (install: brew install pngpaste)")
    elif system == "linux":
        if not shutil.which("xclip"):
            missing.append("xclip  (install: apt install xclip  /  pacman -S xclip)")
    elif system == "windows":
        if not shutil.which("powershell"):
            missing.append("powershell  (should be built-in on Windows 10+)")
    return missing


# --------------------------------------------------------------------------- #
# Python / venv setup.                                                        #
# --------------------------------------------------------------------------- #
def get_python_path(system: str) -> str:
    if system == "windows":
        return str(VENV_DIR / "Scripts" / "python.exe")
    return str(VENV_DIR / "bin" / "python")


def setup_venv(system: str, yes: bool) -> str:
    """Ensure venv exists and deps installed. Returns python path."""
    py_path = get_python_path(system)
    need_setup = not Path(py_path).exists()
    if not need_setup:
        return py_path

    if not yes:
        ans = input("Create virtualenv and install dependencies? [Y/n] ").strip().lower()
        if ans and ans != "y" and ans != "yes":
            print("Aborted.")
            sys.exit(1)

    base_python = sys.executable or ("python3" if system != "windows" else "python")
    print(f"[*] Creating venv at {VENV_DIR} using {base_python}")
    subprocess.check_call([base_python, "-m", "venv", str(VENV_DIR)])

    print("[*] Installing dependencies")
    subprocess.check_call([py_path, "-m", "pip", "install", "--upgrade", "pip"])
    subprocess.check_call([py_path, "-m", "pip", "install", "-r", str(REQUIREMENTS)])
    return py_path


def generate_env_template() -> bool:
    """Create .env from .env.example if it does not exist. Returns True if created."""
    if ENV_FILE.exists():
        return False
    if not ENV_EXAMPLE.exists():
        ENV_FILE.write_text(
            "VISION_BASE_URL=\nVISION_API_KEY=\nVISION_MODEL=qwen3.7-plus\nREQUEST_TIMEOUT=120\n"
        )
        return True
    shutil.copy(ENV_EXAMPLE, ENV_FILE)
    return True


# --------------------------------------------------------------------------- #
# Rules file writer (idempotent via markers).                                 #
# --------------------------------------------------------------------------- #
def write_rules_file(path: Path) -> str:
    """Insert RULES_BLOCK into a markdown file. Idempotent via markers.

    Returns status string: 'created' | 'updated' | 'already_present'.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        content = path.read_text(encoding="utf-8")
        if RULES_MARKER_START in content:
            pattern = re.compile(
                f"{re.escape(RULES_MARKER_START)}.*?{re.escape(RULES_MARKER_END)}",
                re.DOTALL,
            )
            new_content = pattern.sub(RULES_BLOCK.strip(), content)
            if new_content != content:
                path.write_text(new_content, encoding="utf-8")
                return "updated"
            return "already_present"
        # Append to existing file
        separator = "\n\n" if not content.endswith("\n\n") else ""
        path.write_text(content.rstrip() + separator + RULES_BLOCK + "\n", encoding="utf-8")
        return "updated"
    path.write_text(RULES_BLOCK + "\n", encoding="utf-8")
    return "created"


# --------------------------------------------------------------------------- #
# JSON config writer (idempotent).                                            #
# --------------------------------------------------------------------------- #
def upsert_json_mcp_server(
    config_path: Path,
    mcp_key: str,
    server_entry: dict,
) -> str:
    """Insert or update the multimodal MCP entry in a JSON config file.

    mcp_key is the top-level key holding MCP servers (e.g. "mcp" for opencode,
    "mcpServers" for Claude Desktop / Cursor).

    Returns 'created' | 'updated' | 'already_present'.
    """
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if config_path.exists() and config_path.stat().st_size > 0:
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"  [!] {config_path} is not valid JSON, skipping")
            return "error"
    else:
        cfg = {}

    if mcp_key not in cfg or not isinstance(cfg[mcp_key], dict):
        cfg[mcp_key] = {}

    if cfg[mcp_key].get(SERVER_NAME) == server_entry:
        return "already_present"
    cfg[mcp_key][SERVER_NAME] = server_entry

    config_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return "updated" if config_path.exists() else "created"


# --------------------------------------------------------------------------- #
# TOML config writer for Codex (idempotent, text-based).                     #
# --------------------------------------------------------------------------- #
def upsert_codex_mcp_server(config_path: Path, py_path: str) -> str:
    """Insert/update [mcp_servers.multimodal] in a TOML config (text-based)."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    section_header = f"[mcp_servers.{SERVER_NAME}]"

    if config_path.exists():
        content = config_path.read_text(encoding="utf-8")
        if section_header in content:
            pattern = re.compile(
                rf"\[mcp_servers\.{SERVER_NAME}\][^\[]*?(?=\n\[|\Z)",
                re.DOTALL,
            )
            new_block = _codex_block(py_path)
            new_content = pattern.sub(new_block.rstrip() + "\n", content)
            if new_content != content:
                config_path.write_text(new_content, encoding="utf-8")
                return "updated"
            return "already_present"
        separator = "\n" if content and not content.endswith("\n") else ""
        config_path.write_text(
            content + separator + "\n" + _codex_block(py_path) + "\n",
            encoding="utf-8",
        )
        return "updated"
    config_path.write_text(_codex_block(py_path) + "\n", encoding="utf-8")
    return "created"


def _codex_block(py_path: str) -> str:
    return (
        f"[mcp_servers.{SERVER_NAME}]\n"
        f'command = "{py_path}"\n'
        f'args = ["{SERVER_PY}"]\n'
    )


# --------------------------------------------------------------------------- #
# Client installers.                                                          #
# --------------------------------------------------------------------------- #
def _server_entry_for_json(py_path: str) -> dict:
    """opencode / Claude Desktop / Cursor all use this shape."""
    return {
        "type": "local",
        "command": [py_path, str(SERVER_PY)],
    }


def install_opencode(system: str, py_path: str) -> None:
    home = Path.home()
    config = home / ".config" / "opencode" / "opencode.json"
    rules = home / ".config" / "opencode" / "AGENTS.md"

    print("[*] opencode")
    if not config.parent.exists() and not rules.parent.exists():
        print("  [-] not detected (no ~/.config/opencode/), skipping")
        return
    status = upsert_json_mcp_server(config, "mcp", _server_entry_for_json(py_path))
    print(f"  [+] config {status}: {config}")
    rstatus = write_rules_file(rules)
    print(f"  [+] rules  {rstatus}: {rules}")


def install_claude_desktop(system: str, py_path: str) -> None:
    home = Path.home()
    if system == "macos":
        config = home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    elif system == "windows":
        config = Path(os.environ.get("APPDATA", str(home))) / "Claude" / "claude_desktop_config.json"
    else:
        config = home / ".config" / "Claude" / "claude_desktop_config.json"

    print("[*] Claude Desktop")
    if not config.parent.exists():
        print(f"  [-] not detected (no {config.parent}), skipping")
        return
    entry = {"command": py_path, "args": [str(SERVER_PY)]}
    status = upsert_json_mcp_server(config, "mcpServers", entry)
    print(f"  [+] config {status}: {config}")
    rules = Path.cwd() / "CLAUDE.md"
    rstatus = write_rules_file(rules)
    print(f"  [+] rules  {rstatus}: {rules} (project-level; Claude reads it per-project)")


def install_claude_code(system: str, py_path: str) -> None:
    home = Path.home()
    config = home / ".claude.json"
    rules = home / ".claude" / "CLAUDE.md"

    print("[*] Claude Code")
    if not config.exists() and not (home / ".claude").exists():
        print(f"  [-] not detected (no {home / '.claude.json'}), skipping")
        return
    entry = {"command": py_path, "args": [str(SERVER_PY)]}
    status = upsert_json_mcp_server(config, "mcpServers", entry)
    print(f"  [+] config {status}: {config}")
    rstatus = write_rules_file(rules)
    print(f"  [+] rules  {rstatus}: {rules} (global)")


def install_cursor(system: str, py_path: str) -> None:
    home = Path.home()
    config = home / ".cursor" / "mcp.json"
    rules = home / ".cursor" / "rules" / "multimodal.mdc"

    print("[*] Cursor")
    if not config.parent.exists() and not rules.parent.exists():
        print("  [-] not detected (no ~/.cursor/), skipping")
        return
    entry = {"command": py_path, "args": [str(SERVER_PY)]}
    status = upsert_json_mcp_server(config, "mcpServers", entry)
    print(f"  [+] config {status}: {config}")
    # Cursor .mdc has frontmatter
    rules.parent.mkdir(parents=True, exist_ok=True)
    mdc_content = (
        "---\n"
        "description: Multimodal image recognition rules for text-only main models\n"
        "globs: \"\"\n"
        "alwaysApply: true\n"
        "---\n\n"
        + RULES_BLOCK
    )
    if rules.exists() and RULES_MARKER_START in rules.read_text(encoding="utf-8"):
        print(f"  [+] rules  already_present: {rules}")
    else:
        rules.write_text(mdc_content + "\n", encoding="utf-8")
        print(f"  [+] rules  created: {rules}")


def install_codex(system: str, py_path: str) -> None:
    home = Path.home()
    config = home / ".codex" / "config.toml"
    rules = home / ".codex" / "AGENTS.md"

    print("[*] Codex CLI")
    if not config.parent.exists() and not (home / ".codex").exists():
        print(f"  [-] not detected (no {home / '.codex'}), skipping")
        return
    status = upsert_codex_mcp_server(config, py_path)
    print(f"  [+] config {status}: {config}")
    rstatus = write_rules_file(rules)
    print(f"  [+] rules  {rstatus}: {rules}")


def install_windsurf(system: str, py_path: str) -> None:
    print("[*] Windsurf")
    print("  [!] Windsurf MCP servers are configured via UI (Settings > MCP).")
    print(f"      Use this when adding manually:")
    print(f"        command: {py_path}")
    print(f"        args:    [\"{SERVER_PY}\"]")
    rules = Path.cwd() / ".windsurfrules"
    rstatus = write_rules_file(rules)
    print(f"  [+] rules  {rstatus}: {rules} (project-level)")


def install_cline(system: str, py_path: str) -> None:
    print("[*] Cline / Roo Code")
    print("  [!] Cline MCP servers are configured via VS Code extension settings.")
    print(f"      Use this when adding manually:")
    print(f"        command: {py_path}")
    print(f"        args:    [\"{SERVER_PY}\"]")
    rules = Path.cwd() / ".clinerules"
    rstatus = write_rules_file(rules)
    print(f"  [+] rules  {rstatus}: {rules} (project-level)")


# --------------------------------------------------------------------------- #
# Main.                                                                       #
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="Install multimodal-mcp for detected MCP clients.")
    parser.add_argument("--yes", "-y", action="store_true", help="skip confirmations")
    args = parser.parse_args()

    system = detect_system()
    print(f"[*] System: {system}")
    print(f"[*] Project: {PROJECT_DIR}")
    print()

    missing = check_dependencies(system)
    if missing:
        print("[!] Missing system dependencies (only affects clipboard path):")
        for m in missing:
            print(f"      - {m}")
        print("    describe_image still works for URL / file / base64 inputs.")
        print()

    py_path = setup_venv(system, args.yes)
    print(f"[*] Python: {py_path}")
    print()

    if generate_env_template():
        print(f"[+] Created .env template at {ENV_FILE}")
        print("    -> Edit it and fill VISION_BASE_URL / VISION_API_KEY / VISION_MODEL")
    else:
        print(f"[*] .env already exists: {ENV_FILE}")
    print()

    install_opencode(system, py_path)
    print()
    install_claude_desktop(system, py_path)
    print()
    install_claude_code(system, py_path)
    print()
    install_cursor(system, py_path)
    print()
    install_codex(system, py_path)
    print()
    install_windsurf(system, py_path)
    print()
    install_cline(system, py_path)
    print()

    print("=" * 60)
    print("Done. Next steps:")
    print("  1. Edit .env and fill VISION_* values")
    print("  2. Restart the clients you want to use")
    print("  3. Take a screenshot and say \"看下我的截图\" to test")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
