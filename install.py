#!/usr/bin/env python3
"""
Cross-platform installer for multimodal-mcp.

Two modes:
  - uvx (default if git remote present): command = "uvx --from git+URL multimodal-mcp"
      Users never need to clone / venv / pip install. Like npx for Python.
  - local: command = ".venv/bin/python server.py"
      For private / dev use. Creates venv and installs deps.

Detects installed MCP clients and configures them automatically:
  - opencode / Claude Code / Claude Desktop / Cursor / Codex CLI
  - Windsurf / Cline (rules only; MCP via UI)

Run:
    python install.py                          # auto mode, interactive
    python install.py --yes                    # auto mode, skip confirm
    python install.py --base-url URL --api-key KEY --model MODEL
    python install.py --mode uvx --repo git+https://github.com/USER/multimodal-mcp
    python install.py --mode local             # force venv mode

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
from typing import Optional, Tuple

PROJECT_DIR = Path(__file__).resolve().parent
VENV_DIR = PROJECT_DIR / ".venv"
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
# System / path helpers.                                                      #
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


def detect_git_remote() -> Optional[str]:
    """Return git+URL for the origin remote, or None. Strips auth info."""
    if not shutil.which("git"):
        return None
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        url = result.stdout.strip()
        if not url:
            return None
        if url.startswith("git@"):
            url = url.replace(":", "/", 1).replace("git@", "https://", 1)
        if not url.startswith(("http://", "https://", "ssh://")):
            return None
        url = re.sub(r"://[^/@]+@", "://", url)
        return f"git+{url}"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


# --------------------------------------------------------------------------- #
# Mode resolution.                                                            #
# --------------------------------------------------------------------------- #
def resolve_server_entry(
    mode: str, system: str, repo_url: Optional[str], yes: bool
) -> Tuple[str, list[str], str]:
    """Return (command, args, mode_used)."""
    if mode == "auto":
        if not repo_url:
            repo_url = detect_git_remote()
        uvx_available = shutil.which("uvx") or shutil.which("uv")
        if repo_url and uvx_available:
            mode = "uvx"
        else:
            mode = "local"

    if mode == "uvx":
        if not repo_url:
            print("[!] uvx mode needs --repo URL or a git origin remote.")
            print("    Specify with: python install.py --mode uvx --repo git+https://github.com/USER/multimodal-mcp")
            sys.exit(1)
        if not (shutil.which("uvx") or shutil.which("uv")):
            print("[!] uv not installed. Install: curl -LsSf https://astral.sh/uv/install.sh | sh")
            sys.exit(1)
        return "uvx", ["--from", repo_url, "multimodal-mcp"], "uvx"

    py_path = setup_venv(system, yes)
    return py_path, [str(SERVER_PY)], "local"


def setup_venv(system: str, yes: bool) -> str:
    if system == "windows":
        py_path = str(VENV_DIR / "Scripts" / "python.exe")
    else:
        py_path = str(VENV_DIR / "bin" / "python")
    if Path(py_path).exists():
        return py_path

    if not yes:
        ans = input("Create virtualenv and install dependencies? [Y/n] ").strip().lower()
        if ans and ans not in ("y", "yes"):
            print("Aborted.")
            sys.exit(1)

    base_python = sys.executable or ("python3" if system != "windows" else "python")
    print(f"[*] Creating venv at {VENV_DIR} using {base_python}")
    subprocess.check_call([base_python, "-m", "venv", str(VENV_DIR)])
    print("[*] Installing dependencies")
    subprocess.check_call([py_path, "-m", "pip", "install", "--upgrade", "pip"])
    subprocess.check_call([py_path, "-m", "pip", "install", "-r", str(REQUIREMENTS)])
    return py_path


# --------------------------------------------------------------------------- #
# Rules file writer (idempotent).                                             #
# --------------------------------------------------------------------------- #
def write_rules_file(path: Path) -> str:
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
        separator = "\n\n" if not content.endswith("\n\n") else ""
        path.write_text(content.rstrip() + separator + RULES_BLOCK + "\n", encoding="utf-8")
        return "updated"
    path.write_text(RULES_BLOCK + "\n", encoding="utf-8")
    return "created"


# --------------------------------------------------------------------------- #
# Config writers.                                                             #
# --------------------------------------------------------------------------- #
def build_opencode_entry(command: str, args: list[str], env: Optional[dict[str, str]] = None) -> dict:
    entry: dict[str, object] = {"type": "local", "command": [command, *args]}
    if env:
        entry["env"] = env
    return entry


def build_json_entry(command: str, args: list[str], env: Optional[dict[str, str]] = None) -> dict:
    entry: dict[str, object] = {"command": command, "args": args}
    if env:
        entry["env"] = env
    return entry


def _escape_toml_basic(value: str) -> str:
    """Escape a string for TOML double-quoted basic string."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_codex_block(command: str, args: list[str], env: Optional[dict[str, str]] = None) -> str:
    args_str = ", ".join(f'"{_escape_toml_basic(a)}"' for a in args)
    block = f"[mcp_servers.{SERVER_NAME}]\ncommand = \"{_escape_toml_basic(command)}\"\nargs = [{args_str}]\n"
    if env:
        env_items = ", ".join(
            f'{k} = "{_escape_toml_basic(v)}"' for k, v in env.items()
        )
        block += f"env = {{ {env_items} }}\n"
    return block


def upsert_json_mcp_server(
    config_path: Path,
    mcp_key: str,
    server_entry: dict,
) -> str:
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


def upsert_codex_mcp_server(
    config_path: Path, command: str, args: list[str], env: Optional[dict[str, str]] = None
) -> str:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    section_header = f"[mcp_servers.{SERVER_NAME}]"
    child_header = f"[mcp_servers.{SERVER_NAME}."

    if config_path.exists():
        content = config_path.read_text(encoding="utf-8")
        if section_header in content:
            section_pattern = re.compile(
                rf"\[mcp_servers\.{SERVER_NAME}\].*?(?=\n\[(?!mcp_servers\.{SERVER_NAME}\.)|\Z)",
                re.DOTALL,
            )
            new_block = build_codex_block(command, args, env)
            new_content = section_pattern.sub(new_block.rstrip() + "\n", content)
            if new_content != content:
                config_path.write_text(new_content, encoding="utf-8")
                return "updated"
            return "already_present"
        separator = "\n" if content and not content.endswith("\n") else ""
        config_path.write_text(
            content + separator + "\n" + build_codex_block(command, args, env) + "\n",
            encoding="utf-8",
        )
        return "updated"
    config_path.write_text(build_codex_block(command, args, env) + "\n", encoding="utf-8")
    return "created"


# --------------------------------------------------------------------------- #
# Client installers.                                                          #
# --------------------------------------------------------------------------- #
def install_opencode(command: str, args: list[str], env: Optional[dict[str, str]] = None) -> None:
    home = Path.home()
    config = home / ".config" / "opencode" / "opencode.json"
    rules = home / ".config" / "opencode" / "AGENTS.md"

    print("[*] opencode")
    if not config.parent.exists() and not rules.parent.exists():
        print("  [-] not detected (no ~/.config/opencode/), skipping")
        return
    status = upsert_json_mcp_server(config, "mcp", build_opencode_entry(command, args, env))
    print(f"  [+] config {status}: {config}")
    rstatus = write_rules_file(rules)
    print(f"  [+] rules  {rstatus}: {rules}")


def install_claude_desktop(system: str, command: str, args: list[str], env: Optional[dict[str, str]] = None) -> None:
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
    status = upsert_json_mcp_server(config, "mcpServers", build_json_entry(command, args, env))
    print(f"  [+] config {status}: {config}")
    rules = PROJECT_DIR / "CLAUDE.md"
    rstatus = write_rules_file(rules)
    print(f"  [+] rules  {rstatus}: {rules} (project-level)")


def install_claude_code(command: str, args: list[str], env: Optional[dict[str, str]] = None) -> None:
    home = Path.home()
    config = home / ".claude.json"
    rules = home / ".claude" / "CLAUDE.md"

    print("[*] Claude Code")
    if not config.exists() and not (home / ".claude").exists():
        print(f"  [-] not detected (no {home / '.claude.json'}), skipping")
        return
    status = upsert_json_mcp_server(config, "mcpServers", build_json_entry(command, args, env))
    print(f"  [+] config {status}: {config}")
    rstatus = install_claude_code_rules(rules)
    print(f"  [+] rules  {rstatus}: {rules} (global)")


def install_claude_code_rules(rules: Path) -> str:
    """Claude Code reads ~/.claude/CLAUDE.md; reuse write_rules_file."""
    return write_rules_file(rules)


def install_cursor(command: str, args: list[str], env: Optional[dict[str, str]] = None) -> None:
    home = Path.home()
    config = home / ".cursor" / "mcp.json"
    rules = home / ".cursor" / "rules" / "multimodal.mdc"

    print("[*] Cursor")
    if not config.parent.exists() and not rules.parent.exists():
        print("  [-] not detected (no ~/.cursor/), skipping")
        return
    status = upsert_json_mcp_server(config, "mcpServers", build_json_entry(command, args, env))
    print(f"  [+] config {status}: {config}")
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


def install_codex(command: str, args: list[str], env: Optional[dict[str, str]] = None) -> None:
    home = Path.home()
    config = home / ".codex" / "config.toml"
    rules = home / ".codex" / "AGENTS.md"

    print("[*] Codex CLI")
    if not config.parent.exists() and not (home / ".codex").exists():
        print(f"  [-] not detected (no {home / '.codex'}), skipping")
        return
    status = upsert_codex_mcp_server(config, command, args, env)
    print(f"  [+] config {status}: {config}")
    rstatus = write_rules_file(rules)
    print(f"  [+] rules  {rstatus}: {rules}")


def install_windsurf(command: str, args: list[str], env: Optional[dict[str, str]] = None) -> None:
    print("[*] Windsurf")
    print("  [!] Windsurf MCP servers are configured via UI (Settings > MCP).")
    print(f"      Add a server with:")
    print(f"        command: {command}")
    print(f"        args:    {args}")
    if env:
        print(f"        env:     {env}")
    rules = PROJECT_DIR / ".windsurfrules"
    rstatus = write_rules_file(rules)
    print(f"  [+] rules  {rstatus}: {rules} (project-level)")


def install_cline(command: str, args: list[str], env: Optional[dict[str, str]] = None) -> None:
    print("[*] Cline / Roo Code")
    print("  [!] Cline MCP servers are configured via VS Code extension settings.")
    print(f"      Add a server with:")
    print(f"        command: {command}")
    print(f"        args:    {args}")
    if env:
        print(f"        env:     {env}")
    rules = PROJECT_DIR / ".clinerules"
    rstatus = write_rules_file(rules)
    print(f"  [+] rules  {rstatus}: {rules} (project-level)")


# --------------------------------------------------------------------------- #
# Main.                                                                       #
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="Install multimodal-mcp for detected MCP clients.")
    parser.add_argument("--yes", "-y", action="store_true", help="skip confirmations")
    parser.add_argument(
        "--mode",
        choices=["auto", "uvx", "local"],
        default="auto",
        help="auto: use uvx if git remote + uv present, else local (default); "
        "uvx: force uvx --from <repo>; local: force venv",
    )
    parser.add_argument(
        "--repo",
        help="git+URL for uvx mode (e.g. git+https://github.com/USER/multimodal-mcp). "
        "Auto-detected from git remote if omitted.",
    )
    parser.add_argument(
        "--base-url",
        help="Vision API base URL. Written into client config env.",
    )
    parser.add_argument(
        "--api-key",
        help="Vision API key. Written into client config env.",
    )
    parser.add_argument(
        "--model",
        help="Vision model name. Written into client config env.",
    )
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

    provided = [args.base_url, args.api_key, args.model]
    if any(provided) and not all(provided):
        print("[!] --base-url, --api-key, --model must be provided together (or not at all).")
        return 1

    env: Optional[dict[str, str]] = None
    if all(provided):
        env = {
            "VISION_BASE_URL": args.base_url,
            "VISION_API_KEY": args.api_key,
            "VISION_MODEL": args.model,
        }

    command, cmd_args, mode_used = resolve_server_entry(args.mode, system, args.repo, args.yes)
    print(f"[*] Mode: {mode_used}")
    print(f"    command: {command} {' '.join(cmd_args)}")
    print()

    install_opencode(command, cmd_args, env)
    print()
    install_claude_desktop(system, command, cmd_args, env)
    print()
    install_claude_code(command, cmd_args, env)
    print()
    install_cursor(command, cmd_args, env)
    print()
    install_codex(command, cmd_args, env)
    print()
    install_windsurf(command, cmd_args, env)
    print()
    install_cline(command, cmd_args, env)
    print()

    print("=" * 60)
    print(f"Done (mode={mode_used}).")
    if env:
        print("  Credentials written to client configs.")
    else:
        print("  Add VISION_BASE_URL / VISION_API_KEY / VISION_MODEL to the env field of each client config.")
    print("  Restart the clients, then say \"看下我的截图\" to test.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
