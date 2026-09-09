# LLM Generalisation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the fact knowledge layer produce accurate, evidence-grounded facts and relationships on PDFs it has never seen, using Gemini for semantics while every number stays deterministically derived from the source text.

**Architecture:** Two phases. Phase A adds an LLM pass that groups raw predicate labels into *families* used only for candidate retrieval, and a match-basis policy so a family-only match can corroborate but never contradict. Phase B adds tiered LLM claim extraction across all pages behind a verification gate that re-derives every value with the existing deterministic parser and discards anything not found verbatim in the source. The deterministic pipeline remains the default and the fallback throughout.

**Tech Stack:** Python 3.10, FastAPI, Pydantic v2, PyMuPDF, pytest, `google-genai` SDK (Gemini).

**Spec:** `docs/superpowers/specs/2026-09-09-llm-generalisation-design.md`

## Global Constraints

- **The LLM never originates a number, a date, or a quote.** Values are re-derived by `parse_values`; quotes must be found verbatim in the page text before a fact is stored.
- **Precedence is one-directional:** source text → deterministic parser → normalised value → stored fact. LLM output may label a fact, never supply or overrule its value.
- **A family-only match may never produce `CONTRADICTION`.** At most `INSUFFICIENT_EVIDENCE`.
- **No key means no LLM.** Absent `GEMINI_API_KEY`, or `LLM_MODE=off`, the system runs exactly as it does today. Any API error falls back to the deterministic path rather than failing an upload.
- **Secrets never enter the repo.** `backend/.env` and `backend/.llm-cache/` are gitignored (already done).
- Model ids are configuration, never constants in code: `LLM_MODEL_FAST`, `LLM_MODEL_STRONG`.
- Cache key is `SHA256(model_id + prompt_version + schema_version + input_text)`.
- All existing tests must stay green with no API key set. Run: `cd /home/sana/Fact-Knowledge-layer && unset PYTHONPATH && backend/.venv/bin/python -m pytest -q backend/tests`
- Run tests from the repo root with `PYTHONPATH` unset; the venv is at `backend/.venv`.

---

## File Structure

**Create:**
- `backend/app/config.py` — environment/`.env` loading, mode flags, model ids. No domain logic.
- `backend/app/services/llm_client.py` — the only module that talks to Gemini. JSON-schema calls, disk cache, timeout, retry, `LLMUnavailable`.
- `backend/app/services/canonicalizer.py` — Phase A. Raw predicate labels → families.
- `backend/app/services/verification.py` — Phase B gate. Pure functions, no network.
- `backend/app/services/llm_extractor.py` — Phase B. Page batches → proposed claims → gate → facts.
- `backend/tests/test_config.py`, `test_llm_client.py`, `test_canonicalizer.py`, `test_match_basis.py`, `test_verification.py`, `test_llm_extractor.py`, `test_blind_generalisation.py`
- `backend/tests/fixtures/` — recorded Gemini responses.

**Modify:**
- `backend/app/models/fact.py` — `predicate_family`, `predicate_family_confidence`, `entity_source`; `FactPeriod.source`.
- `backend/app/models/relationship.py` — `FactRelationship.match_basis`.
- `backend/app/services/matcher.py:19-21` — `_key()` buckets on family.
- `backend/app/services/reasoner.py` — match basis in step 2, policy in the verdict ladder.
- `backend/app/main.py` — canonicalise on ingest, `/api/status`, rejection stats.
- `backend/requirements.txt` — add `google-genai`.
- `README.md` — blind experiment, limitations, setup.

---

### Task 1: Configuration and `.env` loading

**Files:**
- Create: `backend/app/config.py`
- Test: `backend/tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `config.settings()` returning a `Settings` dataclass with fields `gemini_api_key: str`, `mode: str`, `model_fast: str`, `model_strong: str`, and method `llm_enabled() -> bool`. `config.reload()` re-reads the environment (tests use it).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_config.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/sana/Fact-Knowledge-layer && unset PYTHONPATH && backend/.venv/bin/python -m pytest backend/tests/test_config.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'backend.app.config'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/config.py
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
    """A tiny KEY=VALUE reader, so the project needs no extra dependency."""
    out: Dict[str, str] = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip().strip('"').strip("'")
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/sana/Fact-Knowledge-layer && unset PYTHONPATH && backend/.venv/bin/python -m pytest backend/tests/test_config.py -q`
Expected: PASS, 6 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/config.py backend/tests/test_config.py
git commit -m "feat: add LLM configuration that fails safe to deterministic mode"
```

---

### Task 2: Gemini client with schema-checked JSON, cache, and safe failure

**Files:**
- Create: `backend/app/services/llm_client.py`
- Modify: `backend/requirements.txt`
- Test: `backend/tests/test_llm_client.py`

**Interfaces:**
- Consumes: `config.settings()` from Task 1.
- Produces:
  - `llm_client.generate_json(prompt: str, schema: dict, model: str, prompt_version: str, schema_version: str) -> dict`
  - `llm_client.LLMUnavailable(Exception)`
  - `llm_client._raw_call(prompt, schema, model) -> dict` — the single network seam; tests monkeypatch this and never touch the network.
  - `llm_client.CACHE_DIR: str`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_llm_client.py
import json
import pytest
from backend.app.services import llm_client


@pytest.fixture(autouse=True)
def cache_in_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_client, "CACHE_DIR", str(tmp_path / "cache"))
    yield


def test_a_response_is_cached_and_the_network_is_not_hit_twice(monkeypatch):
    calls = []

    def fake(prompt, schema, model):
        calls.append(model)
        return {"ok": True}

    monkeypatch.setattr(llm_client, "_raw_call", fake)
    a = llm_client.generate_json("p", {"type": "object"}, "m", "v1", "s1")
    b = llm_client.generate_json("p", {"type": "object"}, "m", "v1", "s1")
    assert a == b == {"ok": True}
    assert len(calls) == 1


def test_a_prompt_version_change_busts_the_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(llm_client, "_raw_call",
                        lambda prompt, schema, model: calls.append(1) or {"n": len(calls)})
    llm_client.generate_json("p", {"type": "object"}, "m", "v1", "s1")
    llm_client.generate_json("p", {"type": "object"}, "m", "v2", "s1")
    assert len(calls) == 2


def test_a_schema_version_change_busts_the_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(llm_client, "_raw_call",
                        lambda prompt, schema, model: calls.append(1) or {"n": len(calls)})
    llm_client.generate_json("p", {"type": "object"}, "m", "v1", "s1")
    llm_client.generate_json("p", {"type": "object"}, "m", "v1", "s2")
    assert len(calls) == 2


def test_a_transport_error_raises_LLMUnavailable(monkeypatch):
    def boom(prompt, schema, model):
        raise RuntimeError("network down")

    monkeypatch.setattr(llm_client, "_raw_call", boom)
    with pytest.raises(llm_client.LLMUnavailable):
        llm_client.generate_json("p", {"type": "object"}, "m", "v1", "s1")


def test_unparseable_output_raises_LLMUnavailable(monkeypatch):
    monkeypatch.setattr(llm_client, "_raw_call",
                        lambda prompt, schema, model: (_ for _ in ()).throw(ValueError("not json")))
    with pytest.raises(llm_client.LLMUnavailable):
        llm_client.generate_json("p", {"type": "object"}, "m", "v1", "s1")


def test_a_failure_is_not_cached(monkeypatch, tmp_path):
    state = {"fail": True}

    def flaky(prompt, schema, model):
        if state["fail"]:
            raise RuntimeError("down")
        return {"ok": True}

    monkeypatch.setattr(llm_client, "_raw_call", flaky)
    with pytest.raises(llm_client.LLMUnavailable):
        llm_client.generate_json("p", {"type": "object"}, "m", "v1", "s1")
    state["fail"] = False
    assert llm_client.generate_json("p", {"type": "object"}, "m", "v1", "s1") == {"ok": True}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/sana/Fact-Knowledge-layer && unset PYTHONPATH && backend/.venv/bin/python -m pytest backend/tests/test_llm_client.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'backend.app.services.llm_client'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/services/llm_client.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/sana/Fact-Knowledge-layer && unset PYTHONPATH && backend/.venv/bin/python -m pytest backend/tests/test_llm_client.py -q`
Expected: PASS, 6 passed

- [ ] **Step 5: Add the dependency**

Append to `backend/requirements.txt`:

```
google-genai>=1.0.0
```

Install: `cd /home/sana/Fact-Knowledge-layer && unset PYTHONPATH && backend/.venv/bin/pip install google-genai`

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/llm_client.py backend/tests/test_llm_client.py backend/requirements.txt
git commit -m "feat: add cached Gemini JSON client that fails safe"
```

---

### Task 3: Model fields for families, provenance and match basis

**Files:**
- Modify: `backend/app/models/fact.py`, `backend/app/models/relationship.py`
- Test: `backend/tests/test_model_defaults.py` (create)

**Interfaces:**
- Produces: `Fact.predicate_family: Optional[str]`, `Fact.predicate_family_confidence: float`, `Fact.entity_source: str`, `FactPeriod.source: str`, `FactRelationship.match_basis: str`.
- All default to today's behaviour so existing snapshots and tests keep loading.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_model_defaults.py
"""New fields must default so an existing store.json snapshot still loads."""
from backend.app.models.fact import Fact, FactPeriod, SourceEvidence
from backend.app.models.relationship import (FactRelationship, ReasoningTrace, RelationshipType)


def _fact(**kw):
    base = dict(document_id="d", document_name="d.pdf", entity="E", entity_canonical="e",
                metric="Revenue", metric_canonical="financial.revenue", raw_value="1",
                source_evidence=SourceEvidence(document_id="d", document_name="d.pdf",
                                               page_number=1, exact_quote="q"))
    base.update(kw)
    return Fact(**base)


def test_family_fields_default_to_absent():
    f = _fact()
    assert f.predicate_family is None
    assert f.predicate_family_confidence == 0.0


def test_period_and_entity_provenance_default_to_the_document():
    f = _fact()
    assert f.period.source == "quote"
    assert f.entity_source == "document"


def test_match_basis_defaults_to_exact():
    rel = FactRelationship(fact_a_id="a", fact_b_id="b",
                           relationship_type=RelationshipType.CORROBORATED, confidence=0.9,
                           reasoning_trace=ReasoningTrace(summary="s"),
                           entity_canonical="e", metric_canonical="m")
    assert rel.match_basis == "exact"


def test_provenance_accepts_a_table_header():
    f = _fact(period=FactPeriod(raw="FY2024", canonical="FY2024", source="table_header"))
    assert f.period.source == "table_header"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/sana/Fact-Knowledge-layer && unset PYTHONPATH && backend/.venv/bin/python -m pytest backend/tests/test_model_defaults.py -q`
Expected: FAIL, `AttributeError: 'Fact' object has no attribute 'predicate_family'`

- [ ] **Step 3: Write minimal implementation**

In `backend/app/models/fact.py`, add to `FactPeriod`:

```python
    source: str = "quote"   # quote | table_header | section_header | page_context | document_metadata | inferred
```

Add to `Fact`, directly below `metric_category`:

```python
    # Retrieval-only grouping. A family says two claims are worth comparing;
    # it never says they are the same fact. See metric_canonical for that.
    predicate_family: Optional[str] = None
    predicate_family_confidence: float = 0.0
```

Add to `Fact`, directly below `entity_canonical`:

```python
    entity_source: str = "document"   # document | quote | page_context | inferred
```

In `backend/app/models/relationship.py`, add to `FactRelationship` below `metric_canonical`:

```python
    # How the two facts were found comparable. "family" means an LLM grouping
    # brought them together, which is never strong enough to assert a conflict.
    match_basis: str = "exact"   # exact | family | surface
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/sana/Fact-Knowledge-layer && unset PYTHONPATH && backend/.venv/bin/python -m pytest backend/tests/test_model_defaults.py backend/tests -q`
Expected: PASS, 29 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/fact.py backend/app/models/relationship.py backend/tests/test_model_defaults.py
git commit -m "feat: add predicate family, context provenance and match basis fields"
```

---

### Task 4: Phase A — predicate families

**Files:**
- Create: `backend/app/services/canonicalizer.py`
- Test: `backend/tests/test_canonicalizer.py`

**Interfaces:**
- Consumes: `llm_client.generate_json`, `llm_client.LLMUnavailable`, `config.settings()`, `Fact`.
- Produces: `canonicalizer.assign_families(facts: List[Fact]) -> None` — mutates each fact's `predicate_family` and `predicate_family_confidence` in place. Always succeeds; on any failure every fact falls back to `predicate_family = metric_canonical` with confidence `1.0`.
- Produces: `canonicalizer.PROMPT_VERSION`, `canonicalizer.SCHEMA_VERSION`, `canonicalizer.MIN_MEMBER_CONFIDENCE = 0.6`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_canonicalizer.py
"""Families are for retrieval. Two rules matter more than the clustering itself:
a level and a change never share a family, and any failure falls back to today's
exact keys rather than breaking the pipeline."""
import pytest

from backend.app.models.fact import Fact, SourceEvidence
from backend.app.services import canonicalizer
from backend.app.services.llm_client import LLMUnavailable


def _fact(metric, canonical, unit="INR"):
    return Fact(document_id="d", document_name="d.pdf", entity="E", entity_canonical="e",
                metric=metric, metric_canonical=canonical, raw_value="1", normalized_unit=unit,
                source_evidence=SourceEvidence(document_id="d", document_name="d.pdf",
                                               page_number=1, exact_quote="q"))


def test_members_of_one_family_share_a_family_id(monkeypatch):
    monkeypatch.setattr(canonicalizer, "_call", lambda labels: {"families": [
        {"family_id": "profitability_result", "family_label": "Profitability result",
         "members": [{"raw": "loss for the year", "confidence": 0.88},
                     {"raw": "profit after tax", "confidence": 0.91}]}]})
    a = _fact("loss for the year", "metric.loss_for_the_year")
    b = _fact("profit after tax", "metric.profit_after_tax")
    canonicalizer.assign_families([a, b])
    assert a.predicate_family == b.predicate_family == "profitability_result"
    assert a.predicate_family_confidence == 0.88


def test_a_change_never_joins_the_family_of_a_level(monkeypatch):
    """Deterministic guard: the prompt forbids this, and so does the code."""
    monkeypatch.setattr(canonicalizer, "_call", lambda labels: {"families": [
        {"family_id": "profitability_result", "family_label": "Profitability",
         "members": [{"raw": "EBITDA", "confidence": 0.9},
                     {"raw": "EBITDA change", "confidence": 0.9}]}]})
    level = _fact("EBITDA", "financial.ebitda")
    change = _fact("EBITDA change", "financial.ebitda.change")
    canonicalizer.assign_families([level, change])
    assert level.predicate_family != change.predicate_family
    assert change.predicate_family.endswith(".change")


def test_a_low_confidence_member_falls_back_to_a_singleton_family(monkeypatch):
    monkeypatch.setattr(canonicalizer, "_call", lambda labels: {"families": [
        {"family_id": "grab_bag", "family_label": "Grab bag",
         "members": [{"raw": "revenue", "confidence": 0.95},
                     {"raw": "pin codes served", "confidence": 0.20}]}]})
    good = _fact("revenue", "financial.revenue")
    weak = _fact("pin codes served", "ops.pin_codes")
    canonicalizer.assign_families([good, weak])
    assert good.predicate_family == "grab_bag"
    assert weak.predicate_family == "ops.pin_codes"


def test_an_llm_failure_falls_back_to_exact_canonical_keys(monkeypatch):
    def boom(labels):
        raise LLMUnavailable("no key")

    monkeypatch.setattr(canonicalizer, "_call", boom)
    a = _fact("revenue", "financial.revenue")
    canonicalizer.assign_families([a])
    assert a.predicate_family == "financial.revenue"
    assert a.predicate_family_confidence == 1.0


def test_a_label_the_model_never_returned_still_gets_a_family(monkeypatch):
    monkeypatch.setattr(canonicalizer, "_call", lambda labels: {"families": []})
    a = _fact("headcount", "ops.headcount")
    canonicalizer.assign_families([a])
    assert a.predicate_family == "ops.headcount"


def test_no_call_is_made_when_there_is_nothing_to_group(monkeypatch):
    called = []
    monkeypatch.setattr(canonicalizer, "_call", lambda labels: called.append(1) or {"families": []})
    canonicalizer.assign_families([])
    assert not called
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/sana/Fact-Knowledge-layer && unset PYTHONPATH && backend/.venv/bin/python -m pytest backend/tests/test_canonicalizer.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'backend.app.services.canonicalizer'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/services/canonicalizer.py
"""Phase A: group raw predicate labels into families.

A family is a retrieval device. It says two claims are worth comparing; it never
says they are the same fact - `metric_canonical` is what says that. The prompt
sees labels only: no values, no numbers, no page text, so this call cannot put a
wrong figure into the knowledge layer.
"""
from typing import Dict, List, Tuple

from ..config import settings
from ..models.fact import Fact
from .llm_client import LLMUnavailable, generate_json

PROMPT_VERSION = "families-1"
SCHEMA_VERSION = "families-1"
MIN_MEMBER_CONFIDENCE = 0.6

SCHEMA = {
    "type": "object",
    "properties": {
        "families": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "family_id": {"type": "string"},
                    "family_label": {"type": "string"},
                    "members": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "raw": {"type": "string"},
                                "confidence": {"type": "number"},
                            },
                            "required": ["raw", "confidence"],
                        },
                    },
                },
                "required": ["family_id", "family_label", "members"],
            },
        }
    },
    "required": ["families"],
}

PROMPT = """You group measurement labels taken from business, scientific and government documents.

Group the labels below into families. A family holds labels that name the SAME KIND of
measurable quantity, so that two documents using different words for one quantity end up
together. This grouping is used to decide which claims are worth comparing - it is not a
statement that the claims are identical.

Rules:
- Never put a level and a change in the same family. "EBITDA" and "EBITDA change" are different.
- Never merge labels whose scope clearly differs, such as a total and a single segment.
- When unsure, leave a label alone in its own family. A wrong grouping is worse than no grouping.
- family_id must be lower_snake_case and describe the quantity, not the document.
- confidence is your certainty that the label belongs in that family, from 0 to 1.
- Every label given to you must appear in exactly one family.

Labels:
{labels}
"""


def _call(labels: List[str]) -> Dict:
    """The seam tests monkeypatch. Raises LLMUnavailable on any failure."""
    s = settings()
    prompt = PROMPT.format(labels="\n".join(f"- {l}" for l in labels))
    return generate_json(prompt, SCHEMA, s.model_fast, PROMPT_VERSION, SCHEMA_VERSION)


def _fallback(facts: List[Fact]) -> None:
    for f in facts:
        f.predicate_family = f.metric_canonical
        f.predicate_family_confidence = 1.0


def assign_families(facts: List[Fact]) -> None:
    """Fill predicate_family on every fact. Never raises."""
    if not facts:
        return
    if not settings().llm_enabled():
        _fallback(facts)
        return

    labels = sorted({f.metric for f in facts if f.metric})
    if not labels:
        _fallback(facts)
        return
    try:
        payload = _call(labels)
    except LLMUnavailable:
        _fallback(facts)
        return

    assigned: Dict[str, Tuple[str, float]] = {}
    for family in payload.get("families", []):
        fid = str(family.get("family_id") or "").strip()
        if not fid:
            continue
        for member in family.get("members", []):
            raw = str(member.get("raw") or "").strip()
            try:
                conf = float(member.get("confidence", 0.0))
            except (TypeError, ValueError):
                conf = 0.0
            if raw and conf >= MIN_MEMBER_CONFIDENCE:
                assigned[raw] = (fid, conf)

    for f in facts:
        family, conf = assigned.get(f.metric, (f.metric_canonical, 1.0))
        # A change is its own quantity whatever the model decided, so keep the two apart.
        if f.metric_canonical.endswith(".change") and not family.endswith(".change"):
            family = f"{family}.change"
        f.predicate_family = family
        f.predicate_family_confidence = round(conf, 2)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/sana/Fact-Knowledge-layer && unset PYTHONPATH && backend/.venv/bin/python -m pytest backend/tests/test_canonicalizer.py -q`
Expected: PASS, 6 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/canonicalizer.py backend/tests/test_canonicalizer.py
git commit -m "feat: group predicate labels into retrieval families with Gemini"
```

---

### Task 5: Match-basis policy in matcher and reasoner

**Files:**
- Modify: `backend/app/services/matcher.py`, `backend/app/services/reasoner.py`
- Test: `backend/tests/test_match_basis.py`

**Interfaces:**
- Consumes: `Fact.predicate_family` from Task 4, `FactRelationship.match_basis` from Task 3.
- Produces: `matcher._key()` returning `(entity_canonical, predicate_family or metric_canonical, normalized_unit)`; every `FactRelationship` carries `match_basis`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_match_basis.py
"""A family-only match is weaker evidence than an exact key match, and the
verdicts it is allowed to reach must reflect that."""
from backend.app.models.fact import Fact, FactPeriod, PeriodType, SourceEvidence
from backend.app.models.relationship import RelationshipType
from backend.app.services.matcher import CrossDocumentMatcher
from backend.app.services.reasoner import DeterministicReasoningEngine


def _fact(doc, canonical, family, value, raw, period="FY2024"):
    return Fact(document_id=doc, document_name=f"{doc}.pdf", entity="E", entity_canonical="e",
                metric=canonical.split(".")[-1], metric_canonical=canonical,
                predicate_family=family, predicate_family_confidence=0.9,
                raw_value=raw, numeric_value=value, normalized_numeric_value=value,
                unit="INR Million", normalized_unit="INR",
                period=FactPeriod(raw=period, canonical=period, period_type=PeriodType.FISCAL_YEAR),
                confidence=0.9,
                source_evidence=SourceEvidence(document_id=doc, document_name=f"{doc}.pdf",
                                               page_number=1, exact_quote="q"))


def test_family_only_match_with_differing_values_is_never_a_contradiction():
    a = _fact("d1", "metric.loss_for_the_year", "profitability_result", -2491.0, "a")
    b = _fact("d2", "metric.share_of_associate_profit", "profitability_result", 86.0, "b")
    rel = DeterministicReasoningEngine.evaluate_fact_pair(a, b)
    assert rel is not None
    assert rel.match_basis == "family"
    assert rel.relationship_type != RelationshipType.CONTRADICTION
    assert rel.relationship_type == RelationshipType.INSUFFICIENT_EVIDENCE


def test_exact_match_with_differing_values_may_still_contradict():
    a = _fact("d1", "financial.revenue", "revenue_family", 100.0, "a")
    b = _fact("d2", "financial.revenue", "revenue_family", 200.0, "b")
    rel = DeterministicReasoningEngine.evaluate_fact_pair(a, b)
    assert rel.match_basis == "exact"
    assert rel.relationship_type == RelationshipType.CONTRADICTION


def test_family_only_match_with_equal_values_may_corroborate_at_lower_confidence():
    a = _fact("d1", "financial.revenue", "revenue_family", 100.0, "a")
    b = _fact("d2", "metric.net_revenue_from_operations", "revenue_family", 100.0, "b")
    rel = DeterministicReasoningEngine.evaluate_fact_pair(a, b)
    assert rel.relationship_type == RelationshipType.CORROBORATED
    assert rel.match_basis == "family"
    exact = DeterministicReasoningEngine.evaluate_fact_pair(
        _fact("d1", "financial.revenue", "revenue_family", 100.0, "a"),
        _fact("d2", "financial.revenue", "revenue_family", 100.0, "b"))
    assert rel.confidence < exact.confidence


def test_the_matcher_buckets_on_family_so_differently_named_claims_meet():
    a = _fact("d1", "metric.loss_for_the_year", "profitability_result", 100.0, "a")
    b = _fact("d2", "metric.profit_after_tax", "profitability_result", 100.0, "b")
    rels = CrossDocumentMatcher.match_facts([a, b])
    assert len(rels) == 1


def test_facts_in_different_families_never_meet():
    a = _fact("d1", "financial.revenue", "revenue_family", 100.0, "a")
    b = _fact("d2", "ops.headcount", "headcount_family", 100.0, "b")
    assert CrossDocumentMatcher.match_facts([a, b]) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/sana/Fact-Knowledge-layer && unset PYTHONPATH && backend/.venv/bin/python -m pytest backend/tests/test_match_basis.py -q`
Expected: FAIL — `test_family_only_match_with_differing_values_is_never_a_contradiction` fails with `rel is None`, because step 2 currently returns `None` when `metric_canonical` differs.

- [ ] **Step 3: Change the matcher key**

In `backend/app/services/matcher.py`, replace `_key`:

```python
def _key(f: Fact) -> Tuple[str, str, str]:
    # Facts are grouped by predicate family, so two documents naming one quantity
    # differently still meet. Whether they are the SAME claim is the reasoner's call.
    return (f.entity_canonical, f.predicate_family or f.metric_canonical, f.normalized_unit)
```

- [ ] **Step 4: Change the reasoner's metric check**

In `backend/app/services/reasoner.py`, replace the step-2 block:

```python
        # 2. metric ------------------------------------------------------------------
        exact = a.metric_canonical == b.metric_canonical
        family_ok = bool(a.predicate_family) and a.predicate_family == b.predicate_family
        match_basis = "exact" if exact else ("family" if family_ok else "surface")
        metric_ok = exact or family_ok
        checks.append(ReasoningCheck(
            step_number=2, name="Metric", passed=metric_ok,
            explanation=(f"'{a.metric}' and '{b.metric}' both map to {a.metric_canonical}" if exact
                         else (f"'{a.metric}' and '{b.metric}' are different labels grouped into the same "
                               f"family ({a.predicate_family}); comparable, but not proof of one claim"
                               if family_ok else f"{a.metric_canonical} vs {b.metric_canonical}")),
            details={"match_basis": match_basis}))
        if not metric_ok:
            return None
```

- [ ] **Step 5: Apply the policy in the verdict ladder**

In `backend/app/services/reasoner.py`, immediately before `if same_value and period_ok and context_ok:`, insert:

```python
            # A family match brought these together, which is weaker evidence than an
            # identical canonical key. It may confirm an agreement, but the uncertainty
            # the grouping introduced must never be reported as a conflict.
            family_only = match_basis != "exact"
```

Replace the final `else:` branch of the ladder with:

```python
            elif family_only:
                rel, why, conf = RelationshipType.INSUFFICIENT_EVIDENCE, "FAMILY_MATCH_ONLY", 0.45
                summary = (f"'{a.metric}' ({a.raw_value}) and '{b.metric}' ({b.raw_value}) describe related "
                           f"quantities for {pa}, but they are different labels grouped by similarity rather "
                           f"than the same measurement. Values differ; this needs review, not a conflict call.")
            else:
                both_estimates = "estimate" in qa and "estimate" in qb
```

Then reduce confidence for family corroborations. Replace the two `CORROBORATED` assignments:

```python
            if same_value and period_ok and context_ok:
                conf = 0.80 if family_only else 0.95
                rel, why = RelationshipType.CORROBORATED, None
                summary = (f"Both sources report {a.metric} for {pa} as the same value "
                           f"({a.raw_value} in {a.document_name}; {b.raw_value} in {b.document_name}).")
            elif same_value and period_ok:
                conf = 0.72 if family_only else 0.85
                rel, why = RelationshipType.CORROBORATED, None
                summary = (f"Same value for {a.metric} in {pa} ({a.raw_value} vs {b.raw_value}); context differs only in "
                           f"how it was stated ({'; '.join(notes)}).")
```

Finally, pass the basis into the relationship. In the `return FactRelationship(...)` call, add:

```python
                match_basis=match_basis,
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /home/sana/Fact-Knowledge-layer && unset PYTHONPATH && backend/.venv/bin/python -m pytest backend/tests -q`
Expected: PASS, 34 passed. If `test_starter_datasets.py` fails, it is because facts have no `predicate_family` yet and fall back to `metric_canonical` — which is the intended identical-to-today behaviour, so investigate rather than loosen the assertion.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/matcher.py backend/app/services/reasoner.py backend/tests/test_match_basis.py
git commit -m "feat: bucket on predicate family and forbid family-only contradictions"
```

---

### Task 6: Wire Phase A into the API

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_status_endpoint.py` (create)

**Interfaces:**
- Consumes: `canonicalizer.assign_families`, `config.settings`.
- Produces: `GET /api/status`; `rebuild_relationships()` assigns families before matching.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_status_endpoint.py
from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)


def test_status_reports_the_active_mode():
    body = client.get("/api/status").json()
    assert set(["llm_enabled", "mode", "model_fast", "model_strong"]) <= set(body)
    assert isinstance(body["llm_enabled"], bool)


def test_status_reports_llm_off_when_no_key_is_configured(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    from backend.app import config
    monkeypatch.setattr(config, "ENV_PATH", "/nonexistent/.env")
    config.reload()
    body = client.get("/api/status").json()
    assert body["llm_enabled"] is False
    assert body["mode"] == "off"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/sana/Fact-Knowledge-layer && unset PYTHONPATH && backend/.venv/bin/python -m pytest backend/tests/test_status_endpoint.py -q`
Expected: FAIL, 404 on `/api/status`

- [ ] **Step 3: Write minimal implementation**

In `backend/app/main.py`, add to the imports:

```python
from .config import settings
from .services.canonicalizer import assign_families
```

Replace `rebuild_relationships`:

```python
def rebuild_relationships() -> None:
    RELATIONSHIPS.clear()
    facts = list(FACTS.values())
    # families are assigned over the whole layer, because a family only earns its
    # keep when it brings together labels from different documents
    assign_families(facts)
    for rel in CrossDocumentMatcher.match_facts(facts):
        RELATIONSHIPS[rel.id] = rel
```

Add the route next to the other `@app.get` routes:

```python
@app.get("/api/status")
def status():
    """What mode the server is actually running in - visible without reading logs."""
    s = settings()
    return {
        "llm_enabled": s.llm_enabled(),
        "mode": s.mode,
        "model_fast": s.model_fast if s.llm_enabled() else None,
        "model_strong": s.model_strong if s.llm_enabled() else None,
        "documents": len(DOCUMENTS),
        "facts": len(FACTS),
        "relationships": len(RELATIONSHIPS),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/sana/Fact-Knowledge-layer && unset PYTHONPATH && backend/.venv/bin/python -m pytest backend/tests -q`
Expected: PASS, 36 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/tests/test_status_endpoint.py
git commit -m "feat: assign predicate families on rebuild and expose /api/status"
```

---

### Task 7: Prove Phase A closed the generalisation gap

**Files:**
- Create: `backend/tests/test_blind_generalisation.py`
- Create: `backend/tests/fixtures/families_delhivery.json`

**Interfaces:**
- Consumes: everything from Tasks 4–6.
- Produces: a regression test pinning the improvement, and the numbers the README will quote.

- [ ] **Step 1: Record a real families response**

With `GEMINI_API_KEY` set, run this once and save the output. If no key is available, hand-write the fixture using the same shape — the test must not require a key.

```bash
cd /home/sana/Fact-Knowledge-layer && unset PYTHONPATH && backend/.venv/bin/python - <<'EOF'
import sys, glob, os, json
sys.path.insert(0, "backend")
from app.services.parser import PDFParser
from app.services.extractor import FactExtractor
from app.services import canonicalizer
facts = []
for p in sorted(glob.glob("starter-datasets/delhivery/*.pdf")):
    facts += FactExtractor.extract_from_document(PDFParser.parse_pdf(p, os.path.basename(p)))
labels = sorted({f.metric for f in facts if f.metric})
json.dump(canonicalizer._call(labels), open("backend/tests/fixtures/families_delhivery.json", "w"), indent=2)
EOF
```

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_blind_generalisation.py
"""An unseen document is one the metric taxonomy does not recognise. Disabling the
taxonomy simulates that. Before predicate families, the Delhivery corpus produced
6 cross-document pairs in that mode and the macro corpus produced 0."""
import glob
import json
import os

import pytest

from backend.app.services import canonicalizer, normalizer
from backend.app.services.extractor import FactExtractor
from backend.app.services.matcher import CrossDocumentMatcher
from backend.app.services.parser import PDFParser

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "families_delhivery.json")


@pytest.fixture
def blind(monkeypatch):
    """Every taxonomy keyword removed: the open-schema path is all that is left."""
    monkeypatch.setattr(normalizer, "METRICS", [])
    monkeypatch.setattr(normalizer, "_ALIASES", [])
    monkeypatch.setattr(normalizer, "METRIC_BY_CANONICAL", {})
    yield


def _facts(folder):
    out = []
    for pdf in sorted(glob.glob(os.path.join(ROOT, "starter-datasets", folder, "*.pdf"))):
        out.extend(FactExtractor.extract_from_document(PDFParser.parse_pdf(pdf, os.path.basename(pdf))))
    return out


def _cross(facts):
    by = {f.id: f for f in facts}
    return [r for r in CrossDocumentMatcher.match_facts(facts)
            if by[r.fact_a_id].document_id != by[r.fact_b_id].document_id]


def test_families_restore_cross_document_matching_on_an_unrecognised_corpus(blind, monkeypatch):
    with open(FIXTURE, encoding="utf-8") as fh:
        recorded = json.load(fh)
    monkeypatch.setattr(canonicalizer, "_call", lambda labels: recorded)
    monkeypatch.setattr(canonicalizer.settings(), "llm_enabled", lambda: True, raising=False)

    facts = _facts("delhivery")
    canonicalizer.assign_families(facts)
    assert len(_cross(facts)) > 6, "families must beat the taxonomy-blind floor of 6 pairs"


def test_no_family_only_contradiction_survives_on_the_blind_corpus(blind, monkeypatch):
    from backend.app.models.relationship import RelationshipType
    with open(FIXTURE, encoding="utf-8") as fh:
        recorded = json.load(fh)
    monkeypatch.setattr(canonicalizer, "_call", lambda labels: recorded)
    monkeypatch.setattr(canonicalizer.settings(), "llm_enabled", lambda: True, raising=False)

    facts = _facts("delhivery")
    canonicalizer.assign_families(facts)
    for rel in _cross(facts):
        if rel.relationship_type == RelationshipType.CONTRADICTION:
            assert rel.match_basis == "exact", (
                f"a family-only match asserted a contradiction: {rel.reasoning_trace.summary}")
```

- [ ] **Step 3: Run tests**

Run: `cd /home/sana/Fact-Knowledge-layer && unset PYTHONPATH && backend/.venv/bin/python -m pytest backend/tests/test_blind_generalisation.py -q`
Expected: PASS. If the first test fails, the families are too fine-grained — inspect the fixture, and check `MIN_MEMBER_CONFIDENCE` is not discarding good members.

- [ ] **Step 4: Record the numbers for the README**

Run and keep the output; Task 13 quotes it.

```bash
cd /home/sana/Fact-Knowledge-layer && unset PYTHONPATH && backend/.venv/bin/python -m pytest backend/tests/test_blind_generalisation.py -q -s
```

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_blind_generalisation.py backend/tests/fixtures/families_delhivery.json
git commit -m "test: pin the taxonomy-blind generalisation improvement"
```

---

### Task 8: Phase B — the verification gate

**Files:**
- Create: `backend/app/services/verification.py`
- Test: `backend/tests/test_verification.py`

**Interfaces:**
- Consumes: `normalizer.parse_values`, `normalizer.prenormalize`.
- Produces:
  - `verification.ProposedClaim` — a Pydantic model with `subject, predicate, value_text, unit_text, period_text, scope, qualifiers, exact_quote, period_source, subject_source`.
  - `verification.VerificationResult` — `ok: bool`, `reason: str`, `parsed` (a `ParsedValue` or `None`), `quote_start: int`, `quote_end: int`, `corrected: bool`.
  - `verification.verify(claim: ProposedClaim, page_text: str) -> VerificationResult`
  - `verification.REASONS` — the reason codes: `ok`, `quote_not_found`, `value_not_in_quote`, `value_unparseable`, `unit_scale_error`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_verification.py
"""The gate is the whole anti-hallucination guarantee. Every one of these is a
claim a model could plausibly produce, and every one must be caught without a
network call."""
import pytest

from backend.app.services.verification import ProposedClaim, verify

PAGE = ("Other costs Depreciation and amortisation expenses y Loss for the year decreased to "
        "₹2,491.86 million for FY24 from ₹10,077.79 million for FY23.")


def _claim(**kw):
    base = dict(subject="Delhivery Limited", predicate="loss for the year",
                value_text="₹2,491.86 million", unit_text="INR million",
                period_text="FY24", scope="consolidated", qualifiers=[],
                exact_quote="Loss for the year decreased to ₹2,491.86 million for FY24")
    base.update(kw)
    return ProposedClaim(**base)


def test_a_grounded_claim_passes_and_the_parser_supplies_the_value():
    result = verify(_claim(), PAGE)
    assert result.ok
    assert result.parsed.numeric == 2491.86
    assert result.parsed.normalized == 2491860000.0


def test_a_quote_that_is_not_in_the_page_is_rejected():
    result = verify(_claim(exact_quote="Loss for the year rose to ₹9,999.00 million"), PAGE)
    assert not result.ok
    assert result.reason == "quote_not_found"


def test_a_value_that_is_not_inside_its_own_quote_is_rejected():
    result = verify(_claim(value_text="₹5,000.00 million"), PAGE)
    assert not result.ok
    assert result.reason == "value_not_in_quote"


def test_a_fabricated_number_cannot_reach_the_store():
    """The classic hallucination: a plausible figure that is not in the document."""
    result = verify(_claim(value_text="₹3,100.00 million",
                           exact_quote="Loss for the year decreased to ₹3,100.00 million for FY24"), PAGE)
    assert not result.ok
    assert result.reason == "quote_not_found"


def test_the_parser_overrules_the_model_when_they_disagree():
    """Model says billion, source says million. The parser's reading is stored."""
    result = verify(_claim(unit_text="INR billion"), PAGE)
    assert result.ok
    assert result.parsed.normalized == 2491860000.0


def test_a_wrong_scale_that_changes_the_quantity_is_rejected():
    claim = _claim(value_text="₹2,491.86 million", unit_text="INR thousand")
    result = verify(claim, PAGE)
    assert not result.ok
    assert result.reason == "unit_scale_error"


def test_whitespace_differences_do_not_defeat_the_quote_check():
    result = verify(_claim(exact_quote="Loss for the year  decreased   to ₹2,491.86 million for FY24"), PAGE)
    assert result.ok


def test_a_quote_with_no_parseable_number_is_rejected():
    result = verify(_claim(value_text="several", exact_quote="Other costs Depreciation and amortisation expenses"),
                    PAGE)
    assert not result.ok
    assert result.reason in ("value_not_in_quote", "value_unparseable")


def test_a_period_absent_from_the_document_is_not_trusted():
    result = verify(_claim(period_text="FY2099"), PAGE)
    assert result.ok            # the value is still good
    assert result.period_ok is False


def test_a_period_from_a_table_header_is_allowed_when_it_appears_on_the_page():
    result = verify(_claim(period_text="FY23", period_source="table_header"), PAGE)
    assert result.ok
    assert result.period_ok is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/sana/Fact-Knowledge-layer && unset PYTHONPATH && backend/.venv/bin/python -m pytest backend/tests/test_verification.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'backend.app.services.verification'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/services/verification.py
"""The gate every model-proposed claim must survive.

The model may say what a span means. It may not say what number the span
contains: that comes from `parse_values`, over text located in the document.
A claim whose quote or value cannot be found is discarded, not stored.
"""
import re
from dataclasses import dataclass
from typing import List, Optional

from pydantic import BaseModel, Field

from .normalizer import ParsedValue, parse_values, prenormalize

REASONS = ("ok", "quote_not_found", "value_not_in_quote", "value_unparseable", "unit_scale_error")

SCALE_TOLERANCE = 0.005   # 0.5%: enough for rounding, far tighter than any scale step


class ProposedClaim(BaseModel):
    subject: str
    predicate: str
    value_text: str = ""
    unit_text: str = ""
    period_text: str = ""
    scope: str = "unspecified"
    qualifiers: List[str] = Field(default_factory=list)
    exact_quote: str = ""
    period_source: str = "quote"
    subject_source: str = "page_context"


@dataclass
class VerificationResult:
    ok: bool
    reason: str
    parsed: Optional[ParsedValue] = None
    quote_start: int = -1
    quote_end: int = -1
    corrected: bool = False
    period_ok: bool = False


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _locate(quote: str, page_text: str) -> tuple:
    """Find a quote allowing for whitespace differences. Returns (start, end) or (-1, -1)."""
    if not quote.strip():
        return (-1, -1)
    squashed_quote = _squash(quote)
    if not squashed_quote:
        return (-1, -1)
    # build a pattern that lets any run of whitespace match any other
    pattern = r"\s+".join(re.escape(tok) for tok in squashed_quote.split(" "))
    m = re.search(pattern, page_text)
    return (m.start(), m.end()) if m else (-1, -1)


def _model_scale_factor(unit_text: str) -> Optional[float]:
    from .normalizer import SCALE
    for word, factor in SCALE.items():
        if re.search(rf"\b{re.escape(word)}\b", unit_text.lower()):
            return factor
    return None


def verify(claim: ProposedClaim, page_text: str) -> VerificationResult:
    normalized_page = prenormalize(page_text)

    start, end = _locate(claim.exact_quote, normalized_page)
    if start < 0:
        start, end = _locate(claim.exact_quote, page_text)
    if start < 0:
        return VerificationResult(False, "quote_not_found")
    quote = normalized_page[start:end] if end <= len(normalized_page) else page_text[start:end]

    if not claim.value_text.strip():
        return VerificationResult(False, "value_not_in_quote")
    if _squash(prenormalize(claim.value_text)) not in _squash(quote):
        return VerificationResult(False, "value_not_in_quote")

    values = parse_values(quote)
    if not values:
        return VerificationResult(False, "value_unparseable")

    # the value nearest the model's own value_text inside the quote
    offset = _squash(quote).find(_squash(prenormalize(claim.value_text)))
    parsed = min(values, key=lambda v: abs(v.start - max(offset, 0)))

    # the model's stated unit must agree with what the parser read, once both are
    # expressed in base units. "2.49 billion" against "2,491.86 million" is fine;
    # "thousand" against "million" is a scale error, not a rounding difference.
    model_factor = _model_scale_factor(claim.unit_text)
    corrected = False
    if model_factor is not None and parsed.numeric:
        parser_factor = parsed.normalized / parsed.numeric if parsed.numeric else 1.0
        model_normalized = parsed.numeric * model_factor
        if parsed.normalized and abs(model_normalized - parsed.normalized) / abs(parsed.normalized) > SCALE_TOLERANCE:
            if abs(model_factor - parser_factor) > 1e-9:
                return VerificationResult(False, "unit_scale_error")
        corrected = abs(model_factor - parser_factor) > 1e-9

    period_ok = bool(claim.period_text) and _squash(claim.period_text).lower() in _squash(normalized_page).lower()

    return VerificationResult(True, "ok", parsed=parsed, quote_start=start, quote_end=end,
                              corrected=corrected, period_ok=period_ok)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/sana/Fact-Knowledge-layer && unset PYTHONPATH && backend/.venv/bin/python -m pytest backend/tests/test_verification.py -q`
Expected: PASS, 10 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/verification.py backend/tests/test_verification.py
git commit -m "feat: add the verification gate that keeps model output out of stored values"
```

---

### Task 9: Phase B — LLM claim extraction

**Files:**
- Create: `backend/app/services/llm_extractor.py`
- Test: `backend/tests/test_llm_extractor.py`

**Interfaces:**
- Consumes: `verification.verify`, `verification.ProposedClaim`, `llm_client.generate_json`, `config.settings`, `Document`, `DocumentPage`, `Fact`.
- Produces:
  - `llm_extractor.extract_from_document(document: Document) -> Tuple[List[Fact], Dict[str, int]]` — verified facts and a rejection tally keyed by reason code.
  - `llm_extractor.PAGES_PER_CALL = 2`, `PROMPT_VERSION`, `SCHEMA_VERSION`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_llm_extractor.py
import pytest

from backend.app.models.document import Document, DocumentMetadata, DocumentPage
from backend.app.services import llm_extractor

PAGE_TEXT = ("Loss for the year decreased to ₹2,491.86 million for FY24 "
             "from ₹10,077.79 million for FY23.")


def _doc():
    return Document(
        metadata=DocumentMetadata(filename="d.pdf", original_name="d.pdf", page_count=1, file_size_bytes=1,
                                  primary_entity="Delhivery Limited"),
        pages=[DocumentPage(page_number=1, text=PAGE_TEXT, char_count=len(PAGE_TEXT))])


def _payload(**over):
    claim = dict(subject="Delhivery Limited", predicate="loss for the year",
                 value_text="₹2,491.86 million", unit_text="INR million", period_text="FY24",
                 scope="consolidated", qualifiers=[], page_number=1,
                 exact_quote="Loss for the year decreased to ₹2,491.86 million for FY24",
                 period_source="quote", subject_source="page_context")
    claim.update(over)
    return {"claims": [claim]}


def test_a_grounded_claim_becomes_a_verified_fact(monkeypatch):
    monkeypatch.setattr(llm_extractor, "_call", lambda text, model: _payload())
    monkeypatch.setattr(llm_extractor.settings(), "llm_enabled", lambda: True, raising=False)
    facts, rejects = llm_extractor.extract_from_document(_doc())
    assert len(facts) == 1
    assert facts[0].numeric_value == 2491.86
    assert facts[0].extraction_method == "llm_verified"
    assert facts[0].source_evidence.page_number == 1
    assert not rejects


def test_a_fabricated_value_is_rejected_and_counted(monkeypatch):
    monkeypatch.setattr(llm_extractor, "_call", lambda text, model: _payload(
        value_text="₹9,999.00 million",
        exact_quote="Loss for the year decreased to ₹9,999.00 million for FY24"))
    monkeypatch.setattr(llm_extractor.settings(), "llm_enabled", lambda: True, raising=False)
    facts, rejects = llm_extractor.extract_from_document(_doc())
    assert facts == []
    assert rejects["quote_not_found"] == 1


def test_an_llm_failure_yields_no_facts_and_does_not_raise(monkeypatch):
    from backend.app.services.llm_client import LLMUnavailable

    def boom(text, model):
        raise LLMUnavailable("down")

    monkeypatch.setattr(llm_extractor, "_call", boom)
    monkeypatch.setattr(llm_extractor.settings(), "llm_enabled", lambda: True, raising=False)
    facts, rejects = llm_extractor.extract_from_document(_doc())
    assert facts == []


def test_nothing_runs_when_the_llm_is_disabled(monkeypatch):
    called = []
    monkeypatch.setattr(llm_extractor, "_call", lambda text, model: called.append(1) or _payload())
    facts, rejects = llm_extractor.extract_from_document(_doc())
    assert facts == [] and not called


def test_a_period_absent_from_the_page_is_stored_as_unknown(monkeypatch):
    monkeypatch.setattr(llm_extractor, "_call", lambda text, model: _payload(period_text="FY2099"))
    monkeypatch.setattr(llm_extractor.settings(), "llm_enabled", lambda: True, raising=False)
    facts, _ = llm_extractor.extract_from_document(_doc())
    assert facts[0].period.canonical == "unknown"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/sana/Fact-Knowledge-layer && unset PYTHONPATH && backend/.venv/bin/python -m pytest backend/tests/test_llm_extractor.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'backend.app.services.llm_extractor'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/services/llm_extractor.py
"""Phase B: ask Gemini what claims a page makes, then prove each one against the page.

The model proposes. `verification.verify` disposes. Nothing the model says about a
number survives into a Fact unless the deterministic parser finds that number in
the quoted span.
"""
from collections import Counter
from typing import Dict, List, Tuple

from ..config import settings
from ..models.document import Document
from ..models.fact import (Fact, FactContext, FactPeriod, MetricCategory, PeriodType, SourceEvidence)
from .llm_client import LLMUnavailable, generate_json
from .normalizer import find_periods, normalize_entity, normalize_metric
from .verification import ProposedClaim, verify

PAGES_PER_CALL = 2
PROMPT_VERSION = "claims-1"
SCHEMA_VERSION = "claims-1"
LLM_CONFIDENCE_CEILING = 0.8

SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "predicate": {"type": "string"},
                    "value_text": {"type": "string"},
                    "unit_text": {"type": "string"},
                    "period_text": {"type": "string"},
                    "scope": {"type": "string"},
                    "qualifiers": {"type": "array", "items": {"type": "string"}},
                    "exact_quote": {"type": "string"},
                    "page_number": {"type": "integer"},
                    "period_source": {"type": "string"},
                    "subject_source": {"type": "string"},
                },
                "required": ["subject", "predicate", "exact_quote", "page_number"],
            },
        }
    },
    "required": ["claims"],
}

PROMPT = """Identify the factual claims made by the document pages below.

A claim is anything the text asserts about something: a measurement, a status, a role, a
date, a definition. Claims may be numeric or not.

For each claim give:
- subject: what the claim is about
- predicate: what is being asserted about it, in the document's own words
- value_text: the value EXACTLY as it appears in the text, character for character.
  Leave empty if the claim has no value.
- unit_text: the unit, including any scale word such as million or crore
- period_text: the time period, copied from the document
- scope: consolidated, standalone, segment, or unspecified
- qualifiers: short tags such as adjusted, estimate, excluding_something
- exact_quote: a span copied VERBATIM from the page containing the claim
- page_number: the page the quote came from
- period_source: where the period came from - quote, table_header, section_header,
  page_context, or document_metadata
- subject_source: where the subject came from - quote, page_context, or document_metadata

Rules that matter more than completeness:
- Copy quotes and values character for character. Never paraphrase, round, or reformat them.
- If you cannot copy a value exactly as written, leave value_text empty.
- Never state a number that is not in the text.
- Skip a claim rather than guess at one.

Pages:
{pages}
"""


def _call(text: str, model: str) -> Dict:
    """The seam tests monkeypatch."""
    return generate_json(PROMPT.format(pages=text), SCHEMA, model, PROMPT_VERSION, SCHEMA_VERSION)


def _period_for(claim: ProposedClaim, verified) -> FactPeriod:
    if not verified.period_ok or not claim.period_text:
        return FactPeriod(raw="", period_type=PeriodType.UNKNOWN, canonical="unknown",
                          source=claim.period_source or "inferred")
    found = find_periods(claim.period_text)
    if not found:
        return FactPeriod(raw=claim.period_text, period_type=PeriodType.UNKNOWN, canonical="unknown",
                          source=claim.period_source or "quote")
    period = found[0][2]
    period.source = claim.period_source or "quote"
    return period


def extract_from_document(document: Document) -> Tuple[List[Fact], Dict[str, int]]:
    """Returns (verified facts, rejection tally). Never raises."""
    rejects: Counter = Counter()
    if not settings().llm_enabled():
        return [], dict(rejects)

    md = document.metadata
    model = settings().model_fast
    by_number = {p.page_number: p for p in document.pages}
    facts: List[Fact] = []

    for i in range(0, len(document.pages), PAGES_PER_CALL):
        batch = document.pages[i:i + PAGES_PER_CALL]
        rendered = "\n\n".join(f"--- page {p.page_number} ---\n{p.text}" for p in batch)
        try:
            payload = _call(rendered, model)
        except LLMUnavailable:
            continue                                   # a failed batch costs recall, never correctness

        for raw in payload.get("claims", []):
            page = by_number.get(raw.get("page_number"))
            if page is None:
                rejects["quote_not_found"] += 1
                continue
            try:
                claim = ProposedClaim(**{k: v for k, v in raw.items() if k != "page_number"})
            except Exception:
                rejects["quote_not_found"] += 1
                continue

            result = verify(claim, page.text)
            if not result.ok:
                rejects[result.reason] += 1
                continue
            if result.corrected:
                rejects["value_corrected"] += 1     # counted, but the fact is kept: the parser won

            canonical, category = normalize_metric(claim.predicate)
            entity = claim.subject or md.primary_entity or "Unknown entity"
            value = result.parsed
            facts.append(Fact(
                document_id=md.id, document_name=md.original_name,
                entity=entity, entity_canonical=normalize_entity(entity),
                entity_source=claim.subject_source or "page_context",
                metric=claim.predicate, metric_canonical=canonical, metric_category=category,
                raw_value=value.raw, numeric_value=value.numeric,
                normalized_numeric_value=value.normalized,
                unit=value.unit, normalized_unit=value.normalized_unit,
                period=_period_for(claim, result),
                context=FactContext(scope=claim.scope or "unspecified", qualifiers=list(claim.qualifiers)),
                source_evidence=SourceEvidence(
                    document_id=md.id, document_name=md.original_name, page_number=page.page_number,
                    exact_quote=claim.exact_quote, context_window=claim.exact_quote,
                    char_start=result.quote_start, char_end=result.quote_end),
                confidence=LLM_CONFIDENCE_CEILING,
                extraction_method="llm_verified",
                notes="Proposed by the model, value verified against the quoted text."))

    return facts, dict(rejects)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/sana/Fact-Knowledge-layer && unset PYTHONPATH && backend/.venv/bin/python -m pytest backend/tests/test_llm_extractor.py -q`
Expected: PASS, 5 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/llm_extractor.py backend/tests/test_llm_extractor.py
git commit -m "feat: add LLM claim extraction behind the verification gate"
```

---

### Task 10: Merge LLM facts with deterministic facts

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_merge.py` (create)

**Interfaces:**
- Consumes: `llm_extractor.extract_from_document`.
- Produces: `main.merge_facts(deterministic: List[Fact], llm: List[Fact]) -> List[Fact]`; `main.EXTRACTION_STATS: Dict[str, int]`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_merge.py
"""Union with dedup. A deterministic fact is exact by construction, so when the two
paths describe the same measurement the deterministic one is kept."""
from backend.app.main import merge_facts
from backend.app.models.fact import Fact, FactPeriod, SourceEvidence


def _fact(method, value, page=1, canonical="financial.net_income", period="FY2024"):
    return Fact(document_id="d", document_name="d.pdf", entity="E", entity_canonical="e",
                metric="Net profit / loss", metric_canonical=canonical,
                raw_value=str(value), numeric_value=value, normalized_numeric_value=value * 1e6,
                normalized_unit="INR",
                period=FactPeriod(raw=period, canonical=period),
                extraction_method=method, confidence=0.9 if method != "llm_verified" else 0.8,
                source_evidence=SourceEvidence(document_id="d", document_name="d.pdf",
                                               page_number=page, exact_quote="q"))


def test_the_deterministic_fact_wins_a_duplicate():
    merged = merge_facts([_fact("rule_sentence", 2491.86)], [_fact("llm_verified", 2491.86)])
    assert len(merged) == 1
    assert merged[0].extraction_method == "rule_sentence"


def test_an_llm_fact_the_rules_missed_is_kept():
    merged = merge_facts([_fact("rule_sentence", 2491.86)],
                         [_fact("llm_verified", 650.02, canonical="metric.share_of_associate_profit")])
    assert len(merged) == 2


def test_the_same_value_on_a_different_page_is_not_a_duplicate():
    merged = merge_facts([_fact("rule_sentence", 2491.86, page=36)],
                         [_fact("llm_verified", 2491.86, page=65)])
    assert len(merged) == 2


def test_the_same_value_in_a_different_period_is_not_a_duplicate():
    merged = merge_facts([_fact("rule_sentence", 2491.86, period="FY2024")],
                         [_fact("llm_verified", 2491.86, period="FY2023")])
    assert len(merged) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/sana/Fact-Knowledge-layer && unset PYTHONPATH && backend/.venv/bin/python -m pytest backend/tests/test_merge.py -q`
Expected: FAIL, `ImportError: cannot import name 'merge_facts'`

- [ ] **Step 3: Write minimal implementation**

In `backend/app/main.py`, add the import:

```python
from .services import llm_extractor
```

Add near the other module state:

```python
EXTRACTION_STATS: Dict[str, int] = {}
```

Add the helper next to `ingest_file`:

```python
def merge_facts(deterministic: List[Fact], llm: List[Fact]) -> List[Fact]:
    """Union the two extraction paths, deterministic facts winning any duplicate.

    A rule-extracted fact is exact by construction; an LLM fact is verified but was
    proposed. When both describe the same measurement, keep the stronger one.
    """
    def signature(f: Fact):
        return (f.predicate_family or f.metric_canonical, f.period.canonical,
                f.normalized_numeric_value, f.source_evidence.page_number)

    merged: Dict[Any, Fact] = {}
    for f in deterministic:
        merged.setdefault(signature(f), f)
    for f in llm:
        merged.setdefault(signature(f), f)
    return list(merged.values())
```

Replace the body of `ingest_file` after the parse, keeping the de-duplication block above it unchanged:

```python
    doc = PDFParser.parse_pdf(path, original_name)
    facts = FactExtractor.extract_from_document(doc)
    llm_facts, rejects = llm_extractor.extract_from_document(doc)
    for reason, count in rejects.items():
        EXTRACTION_STATS[reason] = EXTRACTION_STATS.get(reason, 0) + count
    EXTRACTION_STATS["proposed"] = EXTRACTION_STATS.get("proposed", 0) + len(llm_facts) + sum(rejects.values())
    EXTRACTION_STATS["accepted"] = EXTRACTION_STATS.get("accepted", 0) + len(llm_facts)
    facts = merge_facts(facts, llm_facts)
    doc.metadata.extracted_facts_count = len(facts)
    doc.metadata.status = "extracted"
    DOCUMENTS[doc.metadata.id] = doc
    for f in facts:
        FACTS[f.id] = f
    return facts
```

Add `EXTRACTION_STATS` to the `/api/status` payload:

```python
        "extraction": dict(EXTRACTION_STATS),
```

And clear it in `clear_all`:

```python
def clear_all() -> None:
    DOCUMENTS.clear(); FACTS.clear(); RELATIONSHIPS.clear(); EXTRACTION_STATS.clear()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/sana/Fact-Knowledge-layer && unset PYTHONPATH && backend/.venv/bin/python -m pytest backend/tests -q`
Expected: PASS, 45 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/tests/test_merge.py
git commit -m "feat: merge verified LLM facts with deterministic facts and count rejections"
```

---

### Task 11: Tiering — escalate ambiguous claims to the strong model

**Files:**
- Modify: `backend/app/services/llm_extractor.py`
- Test: `backend/tests/test_tiering.py` (create)

**Interfaces:**
- Consumes: Task 9.
- Produces: `llm_extractor.needs_escalation(claim: ProposedClaim, result: VerificationResult) -> bool`; escalation re-runs the batch on `settings().model_strong` in `full` mode, and only for the ambiguous batch in `hybrid`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_tiering.py
from backend.app.services.llm_extractor import needs_escalation
from backend.app.services.verification import ProposedClaim, VerificationResult


def _claim(**kw):
    base = dict(subject="S", predicate="p", value_text="1", exact_quote="q")
    base.update(kw)
    return ProposedClaim(**base)


def test_a_clean_verified_claim_does_not_escalate():
    assert needs_escalation(_claim(period_text="FY24"),
                            VerificationResult(True, "ok", period_ok=True)) is False


def test_a_claim_whose_period_could_not_be_grounded_escalates():
    assert needs_escalation(_claim(period_text="FY24"),
                            VerificationResult(True, "ok", period_ok=False)) is True


def test_a_value_the_parser_had_to_correct_escalates():
    assert needs_escalation(_claim(period_text="FY24"),
                            VerificationResult(True, "ok", period_ok=True, corrected=True)) is True


def test_a_rejected_claim_escalates_for_one_retry():
    assert needs_escalation(_claim(), VerificationResult(False, "value_not_in_quote")) is True


def test_a_quote_not_found_does_not_escalate():
    """If the model invented the span, a stronger model on the same page is unlikely to help."""
    assert needs_escalation(_claim(), VerificationResult(False, "quote_not_found")) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/sana/Fact-Knowledge-layer && unset PYTHONPATH && backend/.venv/bin/python -m pytest backend/tests/test_tiering.py -q`
Expected: FAIL, `ImportError: cannot import name 'needs_escalation'`

- [ ] **Step 3: Write minimal implementation**

Add to `backend/app/services/llm_extractor.py`:

```python
from .verification import VerificationResult

ESCALATE_REASONS = ("value_not_in_quote", "value_unparseable", "unit_scale_error")


def needs_escalation(claim: ProposedClaim, result: VerificationResult) -> bool:
    """Is this claim worth a second look from the stronger model?

    Worth escalating: a value the parser had to correct, a period the page could not
    ground, and a claim rejected for a fixable reason. Not worth it: a quote that is
    not in the document at all - a bigger model reading the same page will not find it.
    """
    if not result.ok:
        return result.reason in ESCALATE_REASONS
    if result.corrected:
        return True
    if claim.period_text and not result.period_ok:
        return True
    return False
```

Then in `extract_from_document`, collect escalation candidates per batch and re-run once:

```python
        ambiguous = False
        for raw in payload.get("claims", []):
            ...
            result = verify(claim, page.text)
            if needs_escalation(claim, result):
                ambiguous = True
            ...
```

and after the per-claim loop for the batch, in `full` mode or when `ambiguous`:

```python
        if ambiguous and settings().model_strong != model:
            try:
                payload = _call(rendered, settings().model_strong)
            except LLMUnavailable:
                payload = {"claims": []}
            # re-run the same per-claim verification over `payload`, replacing this
            # batch's contributions; claims that still fail are counted as rejects
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/sana/Fact-Knowledge-layer && unset PYTHONPATH && backend/.venv/bin/python -m pytest backend/tests -q`
Expected: PASS, 50 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/llm_extractor.py backend/tests/test_tiering.py
git commit -m "feat: escalate ambiguous claims to the stronger model"
```

---

### Task 12: Rejection panel in the UI

**Files:**
- Modify: `frontend/src/services/api.ts`, `frontend/src/components/Overview.tsx`
- Create: `frontend/src/components/VerificationPanel.tsx`

**Interfaces:**
- Consumes: `GET /api/status` returning `extraction` and `llm_enabled`.
- Produces: a panel showing proposed / accepted / rejected with per-reason counts, rendered only when `llm_enabled` is true.

- [ ] **Step 1: Add the API call**

In `frontend/src/services/api.ts`, add:

```ts
export interface StatusResponse {
  llm_enabled: boolean;
  mode: string;
  model_fast: string | null;
  model_strong: string | null;
  documents: number;
  facts: number;
  relationships: number;
  extraction: Record<string, number>;
}

export const getStatus = () => http<StatusResponse>('/api/status');
```

Match the existing helper's name and signature in that file; if requests are made with a different helper, use that one instead.

- [ ] **Step 2: Create the panel**

```tsx
// frontend/src/components/VerificationPanel.tsx
import { StatusResponse } from '../services/api';

const REASON_LABELS: Record<string, string> = {
  quote_not_found: 'quote not found in document',
  value_not_in_quote: 'value not present in its quote',
  value_unparseable: 'no value could be parsed from the quote',
  unit_scale_error: 'unit or scale disagreed with the source',
  value_corrected: 'value corrected to the parser’s reading',
};

export function VerificationPanel({ status }: { status: StatusResponse }) {
  if (!status.llm_enabled) return null;
  const stats = status.extraction || {};
  const proposed = stats.proposed ?? 0;
  const accepted = stats.accepted ?? 0;
  if (!proposed) return null;
  const rejected = proposed - accepted;
  const reasons = Object.entries(stats).filter(([k]) => k in REASON_LABELS && k !== 'value_corrected');

  return (
    <section className="panel">
      <h2>Model output, checked against the source</h2>
      <dl className="verification-counts">
        <div><dt>Claims proposed by model</dt><dd>{proposed}</dd></div>
        <div><dt>Verified against source</dt><dd>{accepted}</dd></div>
        <div><dt>Rejected</dt><dd>{rejected}</dd></div>
      </dl>
      {reasons.length > 0 && (
        <ul className="verification-reasons">
          {reasons.map(([reason, count]) => (
            <li key={reason}><span className="count">{count}</span> {REASON_LABELS[reason]}</li>
          ))}
        </ul>
      )}
      <p className="note">
        Every stored value is re-derived from the document by the deterministic parser.
        A claim whose quote or figure cannot be found in the source is discarded, not stored.
      </p>
    </section>
  );
}
```

- [ ] **Step 3: Render it**

In `frontend/src/components/Overview.tsx`, fetch the status alongside the existing data and render `<VerificationPanel status={status} />` above the existing summary tiles.

- [ ] **Step 4: Verify in the browser**

```bash
cd /home/sana/Fact-Knowledge-layer/backend && unset PYTHONPATH && .venv/bin/python -m uvicorn app.main:app --port 8000 &
cd /home/sana/Fact-Knowledge-layer/frontend && npm run dev
```

Upload a starter PDF with `GEMINI_API_KEY` set and confirm the panel shows non-zero counts. With the key unset, confirm the panel does not render and the rest of the app is unchanged.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/VerificationPanel.tsx frontend/src/components/Overview.tsx frontend/src/services/api.ts
git commit -m "feat: show what the model proposed and what verification rejected"
```

---

### Task 13: README — the blind experiment and honest limitations

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: the numbers recorded in Task 7 Step 4.

- [ ] **Step 1: Replace the runtime claim**

`README.md:143` currently ends "No LLM is used at runtime." That is no longer true. Replace with a description of the hybrid design: Gemini for semantics, deterministic parsing for every value, and the verification gate between them.

- [ ] **Step 2: Add the generalisation section**

Add a section titled "Testing whether it generalises", containing the before/after table with the real measured numbers from Task 7:

```markdown
An unseen document is, to this system, one whose words miss the metric taxonomy.
I simulated that by disabling my own taxonomy and re-running both corpora.

| Corpus | Taxonomy matches | Taxonomy blind, before | Taxonomy blind, after |
|---|---|---|---|
| Delhivery, 3 docs | 210 cross-document pairs | 6 | <fill from Task 7> |
| India macro, 3 docs | 70 cross-document pairs | 0 | <fill from Task 7> |

The blind column is the honest measure of how the system behaves on a document it
was not built around. `pytest backend/tests/test_blind_generalisation.py` reproduces it.
```

Replace `<fill from Task 7>` with the actual numbers. Do not ship the placeholder.

- [ ] **Step 3: Add setup for the key**

Under "Setup and Run", document that the system works with no key (deterministic mode) and that `GEMINI_API_KEY` in `backend/.env` enables the hybrid path, plus `LLM_MODE=off|hybrid|full`.

- [ ] **Step 4: Extend Limitations**

Add the known defects, each with its reproduction:
- The RBI excerpt yields `inflation = 61 per cent @FY2024` from a running page header reflowed into a chart caption. Parser-level defect in header/body separation.
- On page 65 of the annual report, two values compete for one period token and the extractor keeps `₹86.89 million` (total comprehensive income) rather than `₹86.95 million` (share of net profit).
- Fiscal years are assumed April–March; a US filer's FY would be misread.
- A family-only match can never assert a contradiction, so a real conflict between two differently-named metrics is reported as "needs review" rather than a conflict. Deliberate: precision over recall.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: record the generalisation experiment and the known defects"
```

---

### Task 14: Full-suite verification and an unseen-domain smoke test

**Files:**
- Create: `backend/tests/test_unseen_domain.py`

- [ ] **Step 1: Write the test**

```python
# backend/tests/test_unseen_domain.py
"""A PDF from outside finance and macroeconomics must produce sane, grounded facts.
Uses the assignment PDF in the repo root: prose, almost no numbers, and nothing the
taxonomy was built for."""
import os

import pytest

from backend.app.services.extractor import FactExtractor
from backend.app.services.parser import PDFParser

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PDF = os.path.join(ROOT, "superjoin-vit-2026-assignment.pdf")


@pytest.mark.skipif(not os.path.exists(PDF), reason="assignment PDF not present")
def test_an_out_of_domain_pdf_yields_only_grounded_facts():
    doc = PDFParser.parse_pdf(PDF, os.path.basename(PDF))
    facts = FactExtractor.extract_from_document(doc)
    pages = {p.page_number: p.text for p in doc.pages}
    for f in facts:
        page = pages[f.source_evidence.page_number]
        assert f.source_evidence.exact_quote, f"{f.metric} has no evidence"
        if f.raw_value and f.numeric_value is not None:
            digits = f.raw_value.replace(",", "").strip("₹$€£ ")
            assert any(ch.isdigit() for ch in digits)
    assert doc.metadata.primary_entity
```

- [ ] **Step 2: Run the whole suite**

Run: `cd /home/sana/Fact-Knowledge-layer && unset PYTHONPATH && backend/.venv/bin/python -m pytest backend/tests -q`
Expected: PASS, all tests

- [ ] **Step 3: Run the suite with no key, to prove the fallback**

Run: `cd /home/sana/Fact-Knowledge-layer && unset PYTHONPATH && env -u GEMINI_API_KEY LLM_MODE=off backend/.venv/bin/python -m pytest backend/tests -q`
Expected: PASS, all tests

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_unseen_domain.py
git commit -m "test: check an out-of-domain PDF produces only grounded facts"
```

---

## Self-Review Notes

- **Spec coverage.** Invariant → Tasks 8, 9. Three predicate levels → Tasks 3, 4. Match-basis policy → Task 5. Tiering and `LLM_MODE` → Tasks 1, 11. Verification gate incl. unit/scale → Task 8. Provenance → Tasks 3, 8, 9. Merge → Task 10. Config and fallback → Tasks 1, 6. Caching → Task 2. Observability → Tasks 6, 10, 12. Testing → Tasks 7, 8, 14. README → Task 13.
- **Not covered by a task, by decision:** the spec's open question about deleting the sample-specific taxonomy entries. It is explicitly a follow-up to be decided on evidence after Phase A lands.
- **Type consistency.** `assign_families` mutates in place and returns `None` (Tasks 4, 6, 7). `extract_from_document` returns `Tuple[List[Fact], Dict[str, int]]` (Tasks 9, 10, 11). `verify` returns `VerificationResult` (Tasks 8, 9, 11). `_call` is the monkeypatch seam in both `canonicalizer` and `llm_extractor`, with different signatures — `_call(labels)` and `_call(text, model)` — which is intentional and used consistently.
- **Task 11 Step 3** describes the re-run loop rather than repeating the whole batch body verbatim. The implementer should extract the per-claim verification into a local helper first, then call it for both payloads.
