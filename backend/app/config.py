"""Runtime configuration.

The LLM is opt-in and fails safe: with no key, or an unrecognised mode, the
system runs the deterministic pipeline exactly as it did before Gemini existed.
"""
import os
from dataclasses import dataclass
from typing import Dict

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(ROOT_DIR, ".env")

VALID_MODES = ("off", "hybrid", "full")
DEFAULT_MODEL_FAST = "gemini-3.5-flash-lite"
DEFAULT_MODEL_STRONG = "gemini-3.8-flash"


def _read_env_file(path: str) -> Dict[str, str]:
    """A tiny KEY=VALUE reader, so the project needs no extra dependency.

    Returns empty dict if the file cannot be read (permission denied, is a
    directory, encoding error, etc), ensuring the system fails safe to
    deterministic mode rather than crashing at import time.
    """
    out: Dict[str, str] = {}
    if not os.path.exists(path):
        return out
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                out[key.strip()] = value.strip().strip('"').strip("'")
    except (OSError, UnicodeDecodeError):
        # If the file cannot be read (permissions, is a directory, encoding
        # error, etc), return empty dict to fail safe to deterministic mode.
        pass
    return out


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    mode: str
    model_fast: str
    model_strong: str

    def llm_enabled(self) -> bool:
        return bool(self.gemini_api_key) and self.mode != "off"


_SETTINGS: Settings = None  # type: ignore[assignment]


def reload() -> Settings:
    """Re-read configuration. The real environment wins over the .env file."""
    global _SETTINGS
    file_env = _read_env_file(ENV_PATH)

    def get(name: str, default: str = "") -> str:
        return (os.environ.get(name) or file_env.get(name) or default).strip()

    key = get("GEMINI_API_KEY")
    mode = get("LLM_MODE", "hybrid").lower()
    if not key or mode not in VALID_MODES:
        mode = "off"
    _SETTINGS = Settings(
        gemini_api_key=key,
        mode=mode,
        model_fast=get("LLM_MODEL_FAST", DEFAULT_MODEL_FAST),
        model_strong=get("LLM_MODEL_STRONG", DEFAULT_MODEL_STRONG),
    )
    return _SETTINGS


def settings() -> Settings:
    return _SETTINGS if _SETTINGS is not None else reload()
