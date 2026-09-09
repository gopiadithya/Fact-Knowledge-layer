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
