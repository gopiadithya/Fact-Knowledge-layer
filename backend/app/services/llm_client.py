"""The only module that talks to Gemini.

Every call is cached on disk under a key that includes the model, the prompt
version and the schema version, so editing a prompt cannot silently reuse a
response shaped for the old contract. Any failure raises LLMUnavailable, which
callers treat as "run the deterministic path".
"""
import hashlib
import json
import os
from typing import Any, Dict

from ..config import settings

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_DIR = os.path.join(ROOT_DIR, ".llm-cache")
TIMEOUT_SECONDS = 60


class LLMUnavailable(Exception):
    """Gemini could not be reached, or returned something unusable."""


def _cache_key(prompt: str, model: str, prompt_version: str, schema_version: str) -> str:
    blob = "\x00".join([model, prompt_version, schema_version, prompt])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _cache_read(key: str):
    path = os.path.join(CACHE_DIR, f"{key}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _cache_write(key: str, value: Dict[str, Any]) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{key}.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(value, fh)
    os.replace(tmp, path)


def _raw_call(prompt: str, schema: Dict[str, Any], model: str) -> Dict[str, Any]:
    """The single network seam. Tests monkeypatch this function."""
    from google import genai  # imported lazily so the package is optional

    client = genai.Client(api_key=settings().gemini_api_key)
    response = client.interactions.create(
        model=model,
        input=prompt,
        response_format={"type": "text", "mime_type": "application/json", "schema": schema},
    )
    text = getattr(response, "output_text", None) or getattr(response, "text", None)
    if not text:
        raise ValueError("empty response")
    return json.loads(text)


def generate_json(prompt: str, schema: Dict[str, Any], model: str,
                  prompt_version: str, schema_version: str) -> Dict[str, Any]:
    key = _cache_key(prompt, model, prompt_version, schema_version)
    cached = _cache_read(key)
    if cached is not None:
        return cached
    try:
        result = _raw_call(prompt, schema, model)
    except Exception as exc:                      # transport, quota, parse - all the same to callers
        raise LLMUnavailable(str(exc)) from exc
    if not isinstance(result, dict):
        raise LLMUnavailable("response was not a JSON object")
    _cache_write(key, result)                     # only successes are cached
    return result
