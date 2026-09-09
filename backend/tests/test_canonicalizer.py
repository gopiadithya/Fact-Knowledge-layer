"""Families are for retrieval. Two rules matter more than the clustering itself:
a level and a change never share a family, and any failure falls back to today's
exact keys rather than breaking the pipeline."""
import pytest

from backend.app.config import Settings
from backend.app.models.fact import Fact, SourceEvidence
from backend.app.services import canonicalizer
from backend.app.services.llm_client import LLMUnavailable


def _fact(metric, canonical, unit="INR"):
    return Fact(document_id="d", document_name="d.pdf", entity="E", entity_canonical="e",
                metric=metric, metric_canonical=canonical, raw_value="1", normalized_unit=unit,
                source_evidence=SourceEvidence(document_id="d", document_name="d.pdf",
                                               page_number=1, exact_quote="q"))


def _enable_llm(monkeypatch):
    """Settings is a frozen dataclass, so we can't set an attribute on an
    instance. Instead patch the `settings` function that canonicalizer
    imported into its own namespace, to hand back an instance whose
    llm_enabled() is True."""
    monkeypatch.setattr(canonicalizer, "settings",
                        lambda: Settings(gemini_api_key="k", mode="hybrid", model_fast="f", model_strong="s"))


def test_members_of_one_family_share_a_family_id(monkeypatch):
    _enable_llm(monkeypatch)
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
    _enable_llm(monkeypatch)
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
    _enable_llm(monkeypatch)
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
    _enable_llm(monkeypatch)

    def boom(labels):
        raise LLMUnavailable("no key")

    monkeypatch.setattr(canonicalizer, "_call", boom)
    a = _fact("revenue", "financial.revenue")
    canonicalizer.assign_families([a])
    assert a.predicate_family == "financial.revenue"
    assert a.predicate_family_confidence == 1.0


def test_a_label_the_model_never_returned_still_gets_a_family(monkeypatch):
    _enable_llm(monkeypatch)
    monkeypatch.setattr(canonicalizer, "_call", lambda labels: {"families": []})
    a = _fact("headcount", "ops.headcount")
    canonicalizer.assign_families([a])
    assert a.predicate_family == "ops.headcount"


def test_no_call_is_made_when_there_is_nothing_to_group(monkeypatch):
    called = []
    monkeypatch.setattr(canonicalizer, "_call", lambda labels: called.append(1) or {"families": []})
    canonicalizer.assign_families([])
    assert not called


def test_families_not_a_list_falls_back_to_exact_canonical_keys(monkeypatch):
    """A schema-drifted or hand-edited cache entry must never crash the pipeline."""
    _enable_llm(monkeypatch)
    monkeypatch.setattr(canonicalizer, "_call", lambda labels: {"families": "not-a-list"})
    a = _fact("revenue", "financial.revenue")
    canonicalizer.assign_families([a])
    assert a.predicate_family == "financial.revenue"
    assert a.predicate_family_confidence == 1.0


def test_a_non_dict_entry_in_families_falls_back_to_exact_canonical_keys(monkeypatch):
    _enable_llm(monkeypatch)
    monkeypatch.setattr(canonicalizer, "_call", lambda labels: {"families": ["not-a-dict", 42, None]})
    a = _fact("revenue", "financial.revenue")
    canonicalizer.assign_families([a])
    assert a.predicate_family == "financial.revenue"
    assert a.predicate_family_confidence == 1.0


def test_members_not_a_list_falls_back_to_exact_canonical_keys(monkeypatch):
    _enable_llm(monkeypatch)
    monkeypatch.setattr(canonicalizer, "_call", lambda labels: {"families": [
        {"family_id": "grab_bag", "family_label": "Grab bag", "members": "oops"}]})
    a = _fact("revenue", "financial.revenue")
    canonicalizer.assign_families([a])
    assert a.predicate_family == "financial.revenue"
    assert a.predicate_family_confidence == 1.0
