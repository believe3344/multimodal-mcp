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
