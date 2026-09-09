import os
import pytest
from backend.app import config


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for k in ("GEMINI_API_KEY", "LLM_MODE", "LLM_MODEL_FAST", "LLM_MODEL_STRONG"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(config, "ENV_PATH", str(tmp_path / ".env"))
    yield


def test_no_key_means_llm_disabled():
    s = config.reload()
    assert s.llm_enabled() is False
    assert s.mode == "off"


def test_key_enables_hybrid_by_default(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    s = config.reload()
    assert s.llm_enabled() is True
    assert s.mode == "hybrid"


def test_mode_off_disables_llm_even_with_a_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODE", "off")
    s = config.reload()
    assert s.llm_enabled() is False


def test_env_file_is_read_when_the_variable_is_not_already_set(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=from-file\nLLM_MODE=full\n")
    monkeypatch.setattr(config, "ENV_PATH", str(env))
    s = config.reload()
    assert s.gemini_api_key == "from-file"
    assert s.mode == "full"


def test_real_environment_wins_over_the_env_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=from-file\n")
    monkeypatch.setattr(config, "ENV_PATH", str(env))
    monkeypatch.setenv("GEMINI_API_KEY", "from-environ")
    s = config.reload()
    assert s.gemini_api_key == "from-environ"


def test_an_unknown_mode_falls_back_to_off(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODE", "banana")
    s = config.reload()
    assert s.mode == "off"


def test_unreadable_env_file_fails_safe_to_off(tmp_path, monkeypatch):
    # Use a directory as the env file path, which cannot be opened as a file.
    # This ensures the system fails safe to deterministic mode instead of crashing.
    monkeypatch.setattr(config, "ENV_PATH", str(tmp_path))
    s = config.reload()
    assert s.llm_enabled() is False
    assert s.mode == "off"
