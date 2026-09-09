import glob
import io
import os

from fastapi.testclient import TestClient

from backend.app import main as main_module
from backend.app.main import app
from backend.app.config import Settings
from backend.app.models.fact import Fact, FactPeriod, PeriodType, SourceEvidence
from backend.app.models.relationship import FactRelationship, ReasoningTrace, RelationshipType
from backend.app.services import canonicalizer
from backend.app.services.extractor import FactExtractor
from backend.app.services.matcher import CrossDocumentMatcher
from backend.app.services.parser import PDFParser

client = TestClient(app)
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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


def _fact(entity, metric, metric_canonical, family=None):
    return Fact(
        document_id="d1", document_name="d1.pdf",
        entity=entity, entity_canonical=entity,
        metric=metric, metric_canonical=metric_canonical,
        predicate_family=family, predicate_family_confidence=1.0 if family else 0.0,
        raw_value="100", numeric_value=100.0, normalized_numeric_value=100.0, normalized_unit="INR",
        period=FactPeriod(raw="FY2024", period_type=PeriodType.FISCAL_YEAR, canonical="2024"),
        confidence=0.95,
        source_evidence=SourceEvidence(document_id="d1", document_name="d1.pdf", page_number=1, exact_quote="q"),
    )


def test_incremental_upload_matches_a_new_fact_against_an_existing_familys_member(monkeypatch):
    """Regression pin: existing facts already carry a predicate_family from the last
    rebuild. If the incremental path does not regroup new facts together with them
    before matching, a newly uploaded fact naming the same quantity differently lands
    in a different bucket and never meets its existing counterpart - matching less
    than the deterministic pipeline did before families existed."""
    saved = (dict(main_module.DOCUMENTS), dict(main_module.FACTS), dict(main_module.RELATIONSHIPS))
    main_module.clear_all()
    monkeypatch.setattr(main_module, "save_store", lambda: None)
    try:
        existing = _fact("Acme Ltd", "Total Revenue", "metric.total_revenue", family="stale_family_from_last_rebuild")
        main_module.FACTS[existing.id] = existing

        new_fact = _fact("Acme Ltd", "Revenue from Operations", "metric.revenue_from_ops")

        def fake_ingest_file(path, original_name):
            meta = main_module.DocumentMetadata(filename=original_name, original_name=original_name,
                                                 page_count=1, file_size_bytes=1, status="extracted")
            doc = main_module.Document(metadata=meta, pages=[])
            main_module.DOCUMENTS[meta.id] = doc
            new_fact.document_id = meta.id
            main_module.FACTS[new_fact.id] = new_fact
            return [new_fact]

        monkeypatch.setattr(main_module, "ingest_file", fake_ingest_file)

        # Deterministic, offline stand-in for the LLM seam (same pattern as test_canonicalizer.py):
        # both raw labels belong to one family, regardless of which document introduced them.
        monkeypatch.setattr(canonicalizer, "settings",
                            lambda: Settings(gemini_api_key="k", mode="hybrid", model_fast="f", model_strong="s"))
        monkeypatch.setattr(canonicalizer, "_call", lambda labels: {"families": [
            {"family_id": "revenue_family", "family_label": "Revenue",
             "members": [{"raw": "Total Revenue", "confidence": 0.9},
                         {"raw": "Revenue from Operations", "confidence": 0.9}]}]})

        resp = client.post("/api/documents/upload-incremental",
                            files={"files": ("test.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")})
        assert resp.status_code == 200

        assert existing.predicate_family == new_fact.predicate_family == "revenue_family"
        matched = [r for r in main_module.RELATIONSHIPS.values()
                   if {r.fact_a_id, r.fact_b_id} == {existing.id, new_fact.id}]
        assert matched, "expected the incremental path to relate the existing fact and the new fact"
    finally:
        main_module.DOCUMENTS.clear(); main_module.DOCUMENTS.update(saved[0])
        main_module.FACTS.clear(); main_module.FACTS.update(saved[1])
        main_module.RELATIONSHIPS.clear(); main_module.RELATIONSHIPS.update(saved[2])


def test_api_cases_surfaces_the_identifier_revision_case():
    """/api/cases is the endpoint that serves the assignment's headline examples from live data.
    The Delhivery CIN pair - same company, one character apart in its identifier, explained by a
    corporate event rather than a real conflict - must actually appear in its
    'contextually_explained' list, not just be reachable via the reasoner in isolation."""
    pdfs = sorted(glob.glob(os.path.join(ROOT, "starter-datasets", "delhivery", "*.pdf")))
    if not pdfs:
        import pytest
        pytest.skip("sample dataset not present")

    facts = []
    for pdf in pdfs:
        doc = PDFParser.parse_pdf(pdf, os.path.basename(pdf))
        facts.extend(FactExtractor.extract_from_document(doc))
    rels = CrossDocumentMatcher.match_facts(facts)

    saved = (dict(main_module.DOCUMENTS), dict(main_module.FACTS), dict(main_module.RELATIONSHIPS))
    main_module.clear_all()
    try:
        for f in facts:
            main_module.FACTS[f.id] = f
        for r in rels:
            main_module.RELATIONSHIPS[r.id] = r

        body = client.get("/api/cases").json()
        factors = [row["relationship"]["reasoning_trace"]["primary_divergence_factor"]
                   for row in body["contextually_explained"]]
        assert "IDENTIFIER_REVISED" in factors, (
            f"expected the CIN identifier-revision case in /api/cases contextually_explained, got {factors}")
    finally:
        main_module.DOCUMENTS.clear(); main_module.DOCUMENTS.update(saved[0])
        main_module.FACTS.clear(); main_module.FACTS.update(saved[1])
        main_module.RELATIONSHIPS.clear(); main_module.RELATIONSHIPS.update(saved[2])


def test_review_queue_ranks_cross_document_above_intra_document():
    """/api/insights' review queue ranking rule: a cross-document disagreement must outrank an
    intra-document one of the same verdict and the same confidence - it is document-pairing, not
    just verdict type, that drives severity. Built from synthetic facts/relationships (not the
    corpus) so this holds deterministically regardless of what the loaded documents contain -
    the Delhivery corpus itself no longer has any CONTRADICTION to pin this with."""
    def _text_fact(doc_id, value):
        return Fact(
            document_id=doc_id, document_name=f"{doc_id}.pdf",
            entity="Acme Ltd", entity_canonical="acme_ltd",
            metric="Registered status", metric_canonical="attr.registered_status",
            raw_value=value, numeric_value=None, normalized_numeric_value=None, normalized_unit="TEXT",
            period=FactPeriod(raw="", period_type=PeriodType.UNKNOWN, canonical="unknown"),
            confidence=0.95,
            source_evidence=SourceEvidence(document_id=doc_id, document_name=f"{doc_id}.pdf",
                                           page_number=1, exact_quote=value),
        )

    def _contradiction(a, b):
        return FactRelationship(
            fact_a_id=a.id, fact_b_id=b.id, relationship_type=RelationshipType.CONTRADICTION, confidence=0.9,
            reasoning_trace=ReasoningTrace(summary="synthetic test relationship",
                                           primary_divergence_factor="TEST", confidence=0.9),
            entity_canonical="acme_ltd", metric_canonical="attr.registered_status",
        )

    a1, a2 = _text_fact("docA", "active"), _text_fact("docA", "ceased")
    b1 = _text_fact("docB", "dissolved")
    # same relationship type and same confidence on both rows: only document-pairing differs
    cross_rel = _contradiction(a1, b1)   # docA vs docB
    intra_rel = _contradiction(a1, a2)   # docA vs docA

    saved = (dict(main_module.DOCUMENTS), dict(main_module.FACTS), dict(main_module.RELATIONSHIPS))
    main_module.clear_all()
    try:
        for f in (a1, a2, b1):
            main_module.FACTS[f.id] = f
        for r in (cross_rel, intra_rel):
            main_module.RELATIONSHIPS[r.id] = r

        queue = client.get("/api/insights").json()["queue"]
        rows = [q for q in queue if q["relationship_id"] in {cross_rel.id, intra_rel.id}]
        assert {q["relationship_id"] for q in rows} == {cross_rel.id, intra_rel.id}, (
            "both synthetic relationships should reach the review queue")
        by_rel = {q["relationship_id"]: q for q in rows}
        assert by_rel[cross_rel.id]["cross_document"] is True
        assert by_rel[intra_rel.id]["cross_document"] is False
        assert by_rel[cross_rel.id]["severity"] > by_rel[intra_rel.id]["severity"]
        cross_idx = next(i for i, q in enumerate(queue) if q["relationship_id"] == cross_rel.id)
        intra_idx = next(i for i, q in enumerate(queue) if q["relationship_id"] == intra_rel.id)
        assert cross_idx < intra_idx, "a cross-document disagreement must rank ahead of an intra-document one"
    finally:
        main_module.DOCUMENTS.clear(); main_module.DOCUMENTS.update(saved[0])
        main_module.FACTS.clear(); main_module.FACTS.update(saved[1])
        main_module.RELATIONSHIPS.clear(); main_module.RELATIONSHIPS.update(saved[2])
