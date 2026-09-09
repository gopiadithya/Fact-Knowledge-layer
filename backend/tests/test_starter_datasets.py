"""End-to-end checks on the sample datasets: the four required cases must come out of the real pipeline."""
import glob
import os

import pytest

from backend.app.models.relationship import RelationshipType
from backend.app.services.extractor import MATCH_THRESHOLD, FactExtractor
from backend.app.services.matcher import CrossDocumentMatcher
from backend.app.services.parser import PDFParser

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATASETS = os.path.join(ROOT, "starter-datasets")


def _run(folder):
    facts = []
    for pdf in sorted(glob.glob(os.path.join(DATASETS, folder, "*.pdf"))):
        doc = PDFParser.parse_pdf(pdf, os.path.basename(pdf))
        facts.extend(FactExtractor.extract_from_document(doc))
    rels = CrossDocumentMatcher.match_facts(facts)
    by_id = {f.id: f for f in facts}
    return facts, rels, by_id


def _cross(rels, by_id, kind, metric=None, factor=None):
    out = []
    for r in rels:
        a, b = by_id[r.fact_a_id], by_id[r.fact_b_id]
        if a.document_id == b.document_id or r.relationship_type != kind:
            continue
        if metric and r.metric_canonical != metric:
            continue
        if factor and r.reasoning_trace.primary_divergence_factor != factor:
            continue
        out.append((r, a, b))
    return out


@pytest.fixture(scope="module")
def delhivery():
    if not glob.glob(os.path.join(DATASETS, "delhivery", "*.pdf")):
        pytest.skip("sample dataset not present")
    return _run("delhivery")


@pytest.fixture(scope="module")
def macro():
    if not glob.glob(os.path.join(DATASETS, "india-macroeconomy", "*.pdf")):
        pytest.skip("sample dataset not present")
    return _run("india-macroeconomy")


def test_every_fact_is_grounded(delhivery):
    facts, _, _ = delhivery
    assert facts
    for f in facts:
        assert f.source_evidence.page_number >= 1 and f.source_evidence.exact_quote
        assert f.document_name


def test_case1_corroborated_across_documents_expressed_differently(delhivery):
    _, rels, by_id = delhivery
    revenue = _cross(rels, by_id, RelationshipType.CORROBORATED, "financial.revenue")
    assert any({a.raw_value, b.raw_value} == {"₹81,415.38 million", "₹8,142 Cr"} for _, a, b in revenue)
    parcels = _cross(rels, by_id, RelationshipType.CORROBORATED, "ops.parcel_volume")
    assert any(a.normalized_numeric_value == b.normalized_numeric_value == 740_000_000 for _, a, b in parcels)


def test_case3_apparent_contradiction_explained_by_basis_and_time(delhivery):
    _, rels, by_id = delhivery
    basis = _cross(rels, by_id, RelationshipType.CONTEXTUALLY_EXPLAINED, "financial.ebitda", "SCOPE_OR_BASIS")
    assert basis, "EBITDA vs adjusted EBITDA for the same year should be explained, not contradicted"
    status = _cross(rels, by_id, RelationshipType.CONTEXTUALLY_EXPLAINED, "governance.board_status", "STATUS_CHANGED_OVER_TIME")
    assert any(a.entity == "Donald Francis Colleran" or b.entity == "Donald Francis Colleran" for _, a, b in status)


def test_case2_attribute_values_genuinely_differ_but_read_as_a_revision(delhivery):
    """The two documents do give Delhivery different CINs ('U...' vs 'L...') - that part is a
    real difference, not noise. But a single-character drift in an identifier between a 2022
    prospectus and a 2024 annual report is best read as the identifier being revised, not as the
    two sources contradicting each other; the fix that makes this CONTEXTUALLY_EXPLAINED instead
    of CONTRADICTION means the corpus should have no CONTRADICTION left at all."""
    _, rels, by_id = delhivery
    cin = _cross(rels, by_id, RelationshipType.CONTEXTUALLY_EXPLAINED, "attr.corporate_identity_number_cin",
                 "IDENTIFIER_REVISED")
    assert cin and {cin[0][1].raw_value[0], cin[0][2].raw_value[0]} == {"U", "L"}
    assert not _cross(rels, by_id, RelationshipType.CONTRADICTION, "attr.corporate_identity_number_cin")


def test_case4_low_confidence_facts_are_kept_but_not_matched(delhivery):
    facts, rels, by_id = delhivery
    low = [f for f in facts if f.confidence < MATCH_THRESHOLD]
    assert low, "the pipeline should surface its own uncertain extractions"
    matched = {r.fact_a_id for r in rels} | {r.fact_b_id for r in rels}
    assert not any(f.id in matched for f in low)


def test_macro_reports_agree_on_gdp_growth_and_explain_estimate_vintage(macro):
    _, rels, by_id = macro
    agree = _cross(rels, by_id, RelationshipType.CORROBORATED, "macro.gdp_growth")
    assert any(a.normalized_numeric_value == b.normalized_numeric_value == 6.5 for _, a, b in agree)
    vintage = _cross(rels, by_id, RelationshipType.CONTEXTUALLY_EXPLAINED, "macro.gdp_growth", "DATA_VINTAGE")
    assert any({a.normalized_numeric_value, b.normalized_numeric_value} == {6.4, 6.5} for _, a, b in vintage)


def test_incremental_ingestion_adds_only_new_pairs(delhivery):
    files = sorted(glob.glob(os.path.join(DATASETS, "delhivery", "*.pdf")))
    existing = []
    for pdf in files[:2]:
        existing.extend(FactExtractor.extract_from_document(PDFParser.parse_pdf(pdf, os.path.basename(pdf))))
    new = FactExtractor.extract_from_document(PDFParser.parse_pdf(files[2], os.path.basename(files[2])))
    rels = CrossDocumentMatcher.match_incremental(new, existing)
    new_ids = {f.id for f in new}
    assert rels and all(r.fact_a_id in new_ids or r.fact_b_id in new_ids for r in rels)


def test_insights_queue_has_no_contradictions_left_to_rank(delhivery):
    """The review queue ranks CONTRADICTION above INSUFFICIENT_EVIDENCE, and cross-document above
    internal - but after the identifier-revision fix, the Delhivery corpus has no CONTRADICTION
    rows at all (the CIN pair, its one former example, is now CONTEXTUALLY_EXPLAINED and rightly
    stays out of the queue: an explained pair doesn't need review). This pins that the queue
    reflects that -  zero CONTRADICTION rows, and the CIN pair specifically absent - so the test
    still fails the moment a spurious contradiction (CIN or otherwise) reappears."""
    facts, rels, by_id = delhivery
    rows = []
    for r in rels:
        a, b = by_id[r.fact_a_id], by_id[r.fact_b_id]
        cross = a.document_id != b.document_id
        base = {(RelationshipType.CONTRADICTION, True): 100, (RelationshipType.CONTRADICTION, False): 55,
                (RelationshipType.INSUFFICIENT_EVIDENCE, True): 45, (RelationshipType.INSUFFICIENT_EVIDENCE, False): 35}.get(
            (r.relationship_type, cross))
        if base is None:
            continue
        spread = min(25.0, (r.delta_percentage or 0) / 4.0) if r.delta_percentage is not None else (
            12.0 if a.normalized_unit == "TEXT" else 0.0)
        rows.append((base * r.confidence + spread, cross, r, a, b))
    rows.sort(key=lambda t: -t[0])

    assert rows, "there should still be something to review (the insufficient-evidence pairs)"
    assert all(r.relationship_type != RelationshipType.CONTRADICTION for _, _, r, _, _ in rows)
    assert not any(r.metric_canonical == "attr.corporate_identity_number_cin" for _, _, r, _, _ in rows), (
        "the CIN pair is explained, not a queue item")


def test_every_queue_item_can_be_shown_with_evidence(delhivery):
    """Anything the UI puts in the queue must carry both quotes and page numbers."""
    facts, rels, by_id = delhivery
    actionable = [r for r in rels if r.relationship_type in
                  (RelationshipType.CONTRADICTION, RelationshipType.INSUFFICIENT_EVIDENCE)]
    assert actionable
    for r in actionable:
        for fid in (r.fact_a_id, r.fact_b_id):
            f = by_id[fid]
            assert f.source_evidence.exact_quote and f.source_evidence.page_number >= 1
        assert r.reasoning_trace.summary
        assert len(r.reasoning_trace.checks) == 5


def test_a_quarterly_figure_is_not_contradicted_by_a_full_year_one(macro):
    """'1.3 per cent of the GDP recorded in Q2 of FY24' is a quarter. It must not be compared
    against the IMF's full-year FY2023/24 current account deficit as if the periods matched."""
    _, rels, by_id = macro
    for r in rels:
        if r.relationship_type != RelationshipType.CONTRADICTION:
            continue
        a, b = by_id[r.fact_a_id], by_id[r.fact_b_id]
        quotes = a.source_evidence.exact_quote + b.source_evidence.exact_quote
        assert "Q2 of FY" not in quotes, f"quarter compared as a full year: {r.reasoning_trace.summary}"


@pytest.fixture(scope="module")
def macro_docs():
    pdfs = sorted(glob.glob(os.path.join(DATASETS, "india-macroeconomy", "*.pdf")))
    if not pdfs:
        pytest.skip("sample dataset not present")
    docs = []
    for pdf in pdfs:
        doc = PDFParser.parse_pdf(pdf, os.path.basename(pdf))
        FactExtractor.resolve_document_context(doc)
        docs.append(doc)
    return docs


def test_each_report_is_attributed_to_its_publisher(macro_docs):
    """These excerpts open on a contents table rather than a cover page. Before the fix the RBI
    report was attributed to 'Food Corporation' (a body it cites) and the Economic Survey to the
    contents heading 'Chapter No. Page No. Name of the Chapter'."""
    by_file = {d.metadata.original_name: d.metadata.primary_entity for d in macro_docs}
    assert by_file["02-rbi-annual-report-2024-25-excerpt.pdf"] == "Reserve Bank of India"
    assert by_file["01-india-economic-survey-2024-25-excerpt.pdf"] == "Economic Survey"
    assert by_file["03-imf-india-2025-article-iv-excerpt.pdf"] == "International Monetary Fund"


def test_two_institutions_forecasting_the_same_year_differently_is_a_contradiction(macro):
    """The Economic Survey carries the RBI's 4.2 per cent headline inflation forecast for FY26;
    the IMF forecasts 2.8 per cent for the same year. Both are projections, neither is a revision
    of the other, and nothing in either document reconciles them - so this is a real disagreement
    between two papers, not a difference of data vintage."""
    _, rels, by_id = macro
    conflicts = _cross(rels, by_id, RelationshipType.CONTRADICTION, "macro.inflation")
    for r, a, b in conflicts:
        if a.period.canonical == "FY2026":
            assert {a.numeric_value, b.numeric_value} == {4.2, 2.8}
            assert r.reasoning_trace.primary_divergence_factor == "COMPETING_ESTIMATES"
            assert "4.2 per cent in FY26" in a.source_evidence.exact_quote + b.source_evidence.exact_quote
            return
    raise AssertionError("the FY26 inflation forecasts of the Survey and the IMF must not be explained away")


@pytest.fixture(scope="module")
def synthetic():
    if not glob.glob(os.path.join(DATASETS, "synthetic-conflict", "*.pdf")):
        pytest.skip("sample dataset not present")
    return _run("synthetic-conflict")


def test_case2_a_real_disagreement_is_still_reported_as_a_contradiction(synthetic):
    """The reconciliation rules must not explain everything away. Two documents that state
    different revenue for the same year, with no scope, basis, vintage or window to separate
    them, have to come out as a contradiction - while the figures they agree on corroborate."""
    _, rels, by_id = synthetic
    conflicts = _cross(rels, by_id, RelationshipType.CONTRADICTION, "financial.revenue")
    assert conflicts, "a plain disagreement on the same metric and period must not be explained away"
    _, a, b = conflicts[0]
    assert {a.normalized_numeric_value, b.normalized_numeric_value} == {12.4e9, 11.8e9}
    assert _cross(rels, by_id, RelationshipType.CORROBORATED, "financial.ebitda")
