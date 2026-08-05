import install


def test_main_rejects_partial_vision_credentials(monkeypatch, capsys) -> None:
    monkeypatch.setattr(install.sys, "argv", ["install.py", "--base-url", "https://vision.example.com"])
    monkeypatch.setattr(install, "detect_system", lambda: "macos")
    monkeypatch.setattr(install, "check_dependencies", lambda system: [])

    def fail_resolve(*args, **kwargs):
        raise AssertionError("resolve_server_entry should not be called")

    monkeypatch.setattr(install, "resolve_server_entry", fail_resolve)

    result = install.main()
    output = capsys.readouterr().out

    assert result == 1
    assert "--base-url, --api-key, --model must be provided together" in output


def test_main_passes_provider_env_with_complete_vision_credentials(monkeypatch) -> None:
    monkeypatch.setattr(
        install.sys,
        "argv",
        [
            "install.py",
            "--provider",
            "anthropic",
            "--base-url",
            "https://vision.example.com",
            "--api-key",
            "secret",
            "--model",
            "claude-3-7-sonnet",
        ],
    )
    monkeypatch.setattr(install, "detect_system", lambda: "macos")
    monkeypatch.setattr(install, "check_dependencies", lambda system: [])
    monkeypatch.setattr(
        install,
        "resolve_server_entry",
        lambda mode, system, repo, yes: ("python", ["server.py"], "local"),
    )

    captured: list[tuple[str, dict[str, str] | None]] = []

    def record_env(name: str):
        def inner(*args) -> None:
            captured.append((name, args[-1]))

        return inner

    monkeypatch.setattr(install, "install_opencode", record_env("opencode"))
    monkeypatch.setattr(install, "install_claude_desktop", record_env("claude_desktop"))
    monkeypatch.setattr(install, "install_claude_code", record_env("claude_code"))
    monkeypatch.setattr(install, "install_cursor", record_env("cursor"))
    monkeypatch.setattr(install, "install_codex", record_env("codex"))
    monkeypatch.setattr(install, "install_windsurf", record_env("windsurf"))
    monkeypatch.setattr(install, "install_cline", record_env("cline"))
    monkeypatch.setattr(install, "install_reasonix", record_env("reasonix"))

    result = install.main()

    assert result == 0
    assert captured
    expected_env = {
        "PROVIDER": "anthropic",
        "BASE_URL": "https://vision.example.com",
        "API_KEY": "secret",
        "MODEL_NAME": "claude-3-7-sonnet",
    }
    assert [env for _, env in captured] == [expected_env] * len(captured)


def test_build_opencode_entry_includes_timeout() -> None:
    entry = install.build_opencode_entry("python", ["server.py"], {"MODEL_NAME": "vision"})
    assert entry == {
        "type": "local",
        "command": ["python", "server.py"],
        "environment": {"MODEL_NAME": "vision"},
        "timeout": 960_000,
    }


def test_build_opencode_entry_timeout_without_env() -> None:
    entry = install.build_opencode_entry("python", ["server.py"])
    assert entry == {
        "type": "local",
        "command": ["python", "server.py"],
        "timeout": 960_000,
    }


def test_build_json_entry_does_not_include_timeout() -> None:
    entry = install.build_json_entry("python", ["server.py"], {"MODEL_NAME": "vision"})
    assert entry == {
        "command": "python",
        "args": ["server.py"],
        "env": {"MODEL_NAME": "vision"},
    }


def test_rules_no_longer_instruct_polling() -> None:
    assert "get_recognition(job_id, wait_seconds=50)" not in install.RULES_BLOCK
    assert "\u7ed3\u675f\u5f53\u524d\u56de\u5408" not in install.RULES_BLOCK
    assert "\u4f1a\u7b49\u5f85\u8bc6\u522b\u5b8c\u6210\u5e76\u76f4\u63a5\u8fd4\u56de\u6700\u7ec8\u7ed3\u679c" in install.RULES_BLOCK
    assert "start_recognition" in install.RULES_BLOCK
    assert "status: processing" not in install.RULES_BLOCK


def test_rules_cover_both_pasted_image_tools() -> None:
    assert "describe_pasted_images(count=N)" in install.RULES_BLOCK
    assert "describe_claude_pasted_images(count=N)" in install.RULES_BLOCK
    assert "~/.claude/image-cache/<session-id>/" in install.RULES_BLOCK


def test_upsert_settings_env_creates_file(tmp_path) -> None:
    import json

    settings = tmp_path / ".claude" / "settings.json"
    status = install.upsert_settings_env(settings, "MCP_TOOL_TIMEOUT", "960000")
    assert status == "updated"
    assert json.loads(settings.read_text()) == {"env": {"MCP_TOOL_TIMEOUT": "960000"}}


def test_upsert_settings_env_preserves_existing_keys(tmp_path) -> None:
    import json

    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"model": "opus", "env": {"OTHER": "1"}}))

    status = install.upsert_settings_env(settings, "MCP_TOOL_TIMEOUT", "960000")
    assert status == "updated"
    assert json.loads(settings.read_text()) == {
        "model": "opus",
        "env": {"OTHER": "1", "MCP_TOOL_TIMEOUT": "960000"},
    }


def test_upsert_settings_env_is_idempotent(tmp_path) -> None:
    import json

    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"env": {"MCP_TOOL_TIMEOUT": "960000"}}))

    status = install.upsert_settings_env(settings, "MCP_TOOL_TIMEOUT", "960000")
    assert status == "already_present"
    assert json.loads(settings.read_text()) == {"env": {"MCP_TOOL_TIMEOUT": "960000"}}


def test_upsert_settings_env_skips_invalid_json(tmp_path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text("{not json")

    status = install.upsert_settings_env(settings, "MCP_TOOL_TIMEOUT", "960000")
    assert status == "error"
    assert settings.read_text() == "{not json"


def test_install_claude_code_writes_timeout(monkeypatch, tmp_path) -> None:
    import json

    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude.json").write_text("{}")
    monkeypatch.setattr(install.Path, "home", lambda: tmp_path)

    install.install_claude_code("python", ["server.py"])

    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert settings["env"]["MCP_TOOL_TIMEOUT"] == str(
        install.CLAUDE_CODE_MCP_TOOL_TIMEOUT_MS
    )


def test_rules_cover_reasonix_attachment_marker() -> None:
    assert "@.reasonix/attachments/" in install.RULES_BLOCK
    assert "image attachment available at" in install.RULES_BLOCK


def test_install_reasonix_skips_without_reasonix_home(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(install.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(install, "PROJECT_DIR", tmp_path)

    install.install_reasonix("python", ["server.py"])

    output = capsys.readouterr().out
    assert "skipping" in output
    assert not (tmp_path / ".mcp.json").exists()


def test_install_reasonix_writes_project_mcp_json_and_rules(monkeypatch, tmp_path) -> None:
    import json

    (tmp_path / ".reasonix").mkdir()
    monkeypatch.setattr(install.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(install, "PROJECT_DIR", tmp_path)

    install.install_reasonix("python", ["server.py"], {"MODEL_NAME": "vision"})

    config = json.loads((tmp_path / ".mcp.json").read_text())
    assert config["mcpServers"]["multimodal"] == {
        "command": "python",
        "args": ["server.py"],
        "env": {"MODEL_NAME": "vision"},
    }
    assert "multimodal-mcp rules start" in (tmp_path / "CLAUDE.md").read_text()


def test_install_reasonix_is_idempotent(monkeypatch, tmp_path, capsys) -> None:
    import json

    (tmp_path / ".reasonix").mkdir()
    monkeypatch.setattr(install.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(install, "PROJECT_DIR", tmp_path)

    install.install_reasonix("python", ["server.py"])
    first = (tmp_path / ".mcp.json").read_text()
    install.install_reasonix("python", ["server.py"])
    second = (tmp_path / ".mcp.json").read_text()

    assert first == second
    assert json.loads(first)["mcpServers"]["multimodal"]["command"] == "python"
    output = capsys.readouterr().out
    assert "mcp_call_timeout_seconds" in output
