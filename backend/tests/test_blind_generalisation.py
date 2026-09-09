"""An unseen document is one the metric taxonomy does not recognise. Disabling the
taxonomy simulates that. Before predicate families, the Delhivery corpus produced
6 cross-document pairs in that mode and the macro corpus produced 0."""
import glob
import json
import os

import pytest

from backend.app.config import Settings
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


def _enable_llm(monkeypatch):
    """Settings is a frozen dataclass, so we can't set an attribute on an
    instance. Instead patch the `settings` function that canonicalizer
    imported into its own namespace, to hand back an instance whose
    llm_enabled() is True."""
    monkeypatch.setattr(canonicalizer, "settings",
                        lambda: Settings(gemini_api_key="k", mode="hybrid", model_fast="f", model_strong="s"))


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
    _enable_llm(monkeypatch)
    with open(FIXTURE, encoding="utf-8") as fh:
        recorded = json.load(fh)
    monkeypatch.setattr(canonicalizer, "_call", lambda labels: recorded)

    facts = _facts("delhivery")
    canonicalizer.assign_families(facts)
    pairs = _cross(facts)
    print(f"delhivery taxonomy-blind cross-document pairs with families: {len(pairs)}")
    assert len(pairs) > 6, "families must beat the taxonomy-blind floor of 6 pairs"


def test_no_family_only_contradiction_survives_on_the_blind_corpus(blind, monkeypatch):
    from backend.app.models.relationship import RelationshipType
    _enable_llm(monkeypatch)
    with open(FIXTURE, encoding="utf-8") as fh:
        recorded = json.load(fh)
    monkeypatch.setattr(canonicalizer, "_call", lambda labels: recorded)

    facts = _facts("delhivery")
    canonicalizer.assign_families(facts)
    for rel in _cross(facts):
        if rel.relationship_type == RelationshipType.CONTRADICTION:
            assert rel.match_basis == "exact", (
                f"a family-only match asserted a contradiction: {rel.reasoning_trace.summary}")
