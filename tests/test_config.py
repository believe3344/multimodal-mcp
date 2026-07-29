import server


def test_positive_float_env_falls_back_for_invalid_values(monkeypatch) -> None:
    monkeypatch.setenv("TEST_TIMEOUT", "invalid")
    assert server._positive_float_env("TEST_TIMEOUT", 20.0) == 20.0
    monkeypatch.setenv("TEST_TIMEOUT", "0")
    assert server._positive_float_env("TEST_TIMEOUT", 20.0) == 20.0
    monkeypatch.setenv("TEST_TIMEOUT", "3.5")
    assert server._positive_float_env("TEST_TIMEOUT", 20.0) == 3.5


def test_positive_int_env_falls_back_for_invalid_values(monkeypatch) -> None:
    monkeypatch.setenv("TEST_LIMIT", "-1")
    assert server._positive_int_env("TEST_LIMIT", 2) == 2
    monkeypatch.setenv("TEST_LIMIT", "4")
    assert server._positive_int_env("TEST_LIMIT", 2) == 4


def test_config_status_reports_provider(monkeypatch) -> None:
    monkeypatch.setattr(server, "BASE_URL", "https://vision.example.com/v1")
    monkeypatch.setattr(server, "API_KEY", "secret")
    monkeypatch.setattr(server, "MODEL_NAME", "gpt-4o-mini")
    monkeypatch.setattr(server, "PROVIDER", "openai")

    status = server._config_status()

    assert status["base_url_set"] is True
    assert status["api_key_set"] is True
    assert status["model_name"] == "gpt-4o-mini"
    assert status["provider"] == "openai"
    assert status["provider_valid"] is True
    assert status["provider_error"] is None
