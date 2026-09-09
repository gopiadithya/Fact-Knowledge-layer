"""Same metric name + same period is not evidence that two figures are the same claim.

Each check below is a pair the engine used to call a CONTRADICTION on the Delhivery set, where
the two numbers are really about different things - or about the same thing stated two ways.
"""
import glob
import os

import pytest

from backend.app.models.relationship import RelationshipType
from backend.app.services.extractor import FactExtractor
from backend.app.services.matcher import CrossDocumentMatcher
from backend.app.services.parser import PDFParser

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="module")
def delhivery():
    facts = []
    for pdf in sorted(glob.glob(os.path.join(ROOT, "starter-datasets", "delhivery", "*.pdf"))):
        doc = PDFParser.parse_pdf(pdf, os.path.basename(pdf))
        facts.extend(FactExtractor.extract_from_document(doc))
    return facts, CrossDocumentMatcher.match_facts(facts), {f.id: f for f in facts}


def _factors(rels, kind):
    return [r for r in rels if r.reasoning_trace.primary_divergence_factor == kind]


def test_the_delhivery_corpus_has_zero_contradictions(delhivery):
    """Every apparent conflict in this corpus turns out to be a definition, basis, subset or
    identifier-revision difference - none of them is two sources genuinely disagreeing.

    The CIN pair (prospectus 'U63090DL2011PLC221234' vs annual report 'L63090DL2011PLC221234')
    used to be reported as the one real CONTRADICTION here. It isn't: the two values differ by a
    single character in a single unbroken identifier token, from documents filed almost two years
    apart. Token-set Jaccard (the metric used for ordinary prose) scores that pair 0.0 similarity
    because an identifier has no tokens to share, which is what produced the false conflict.
    Character-level similarity (~0.95) makes clear this is one CIN being revised over time, not
    two competing claims - so the correct verdict is CONTEXTUALLY_EXPLAINED / IDENTIFIER_REVISED,
    and the Delhivery corpus contains zero real contradictions."""
    _, rels, by_id = delhivery
    conflicts = [r for r in rels if r.relationship_type == RelationshipType.CONTRADICTION]
    assert conflicts == []

    cin_pairs = [r for r in rels if r.metric_canonical == "attr.corporate_identity_number_cin"]
    assert cin_pairs, "the CIN facts should still be matched against each other"
    for r in cin_pairs:
        a, b = by_id[r.fact_a_id], by_id[r.fact_b_id]
        if a.document_id == b.document_id:
            continue
        assert r.relationship_type == RelationshipType.CONTEXTUALLY_EXPLAINED
        assert r.reasoning_trace.primary_divergence_factor == "IDENTIFIER_REVISED"


def test_share_of_an_associates_profit_is_not_the_companys_loss(delhivery):
    """"the Group's share of net profit of Rs 86.95 million" vs "Loss for the year ... Rs 2,491.86
    million" - same metric key, same FY24, different quantities."""
    _, rels, by_id = delhivery
    explained = _factors(rels, "DIFFERENT_DEFINITION")
    assert explained, "the share-of-associate pair should be explained, not flagged"
    for r in explained:
        assert r.relationship_type == RelationshipType.CONTEXTUALLY_EXPLAINED
        a, b = by_id[r.fact_a_id], by_id[r.fact_b_id]
        assert ("share_of" in a.context.qualifiers) != ("share_of" in b.context.qualifiers)


def test_adjusted_and_reported_margins_are_kept_apart(delhivery):
    """"Our Adjusted EBITDA margin ..." states the basis once, at the head of the sentence."""
    facts, rels, _ = delhivery
    p56 = [f for f in facts if f.metric_canonical == "financial.ebitda_margin"
           and f.source_evidence.page_number == 56]
    bases = {f.context.accounting_standard for f in p56}
    assert bases == {"As reported", "Non-GAAP (adjusted)"}
    for r in rels:
        if r.metric_canonical == "financial.ebitda_margin":
            assert r.relationship_type != RelationshipType.CONTRADICTION


def test_an_elided_scale_word_is_read_from_the_rest_of_the_sentence(delhivery):
    """"... from Rs (10,077.79) million in FY23 to Rs (2,491.86) in FY24" - the last figure is in
    millions too, and reading it as Rs 2,491.86 made one report contradict itself."""
    facts, _, _ = delhivery
    fy24_loss = [f for f in facts if f.metric_canonical == "financial.net_income"
                 and f.period.canonical == "FY2024" and f.numeric_value == -2491.86]
    assert fy24_loss, "the FY24 loss should be extracted"
    for f in fy24_loss:
        assert f.normalized_numeric_value == -2491860000.0


def test_unexplained_disagreement_inside_one_report_is_uncertainty_not_conflict(delhivery):
    """Active Customers for express parcel vs heavy goods vs Spoton: one report, one label,
    different populations. The honest verdict is "needs review"."""
    _, rels, by_id = delhivery
    same_doc = [r for r in rels
                if by_id[r.fact_a_id].document_id == by_id[r.fact_b_id].document_id]
    assert not [r for r in same_doc if r.relationship_type == RelationshipType.CONTRADICTION]
    assert _factors(rels, "SAME_LABEL_DIFFERENT_SUBJECTS")
