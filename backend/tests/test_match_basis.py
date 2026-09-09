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


def test_family_only_text_facts_never_contradict():
    a = _fact("d1", "attr.listing_status", "status_family", None, "Listed on NSE")
    b = _fact("d2", "attr.exchange_status", "status_family", None, "Delisted")
    rel = DeterministicReasoningEngine.evaluate_fact_pair(a, b)
    assert rel is not None
    assert rel.match_basis == "family"
    assert rel.relationship_type != RelationshipType.CONTRADICTION
