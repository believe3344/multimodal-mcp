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
