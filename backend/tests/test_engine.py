"""Unit tests for normalisation and pairwise reasoning (no PDFs needed)."""
from backend.app.models.document import Document, DocumentMetadata, DocumentPage
from backend.app.models.fact import Fact, FactContext, FactPeriod, PeriodType, SourceEvidence
from backend.app.models.relationship import RelationshipType
from backend.app.services.extractor import FactExtractor
from backend.app.services.parser import _split_sentences
from backend.app.services.normalizer import (find_periods, normalize_entity, normalize_metric, normalize_period,
                                              normalize_value_and_unit, parse_values, prenormalize)
from backend.app.services.reasoner import DeterministicReasoningEngine, rounding_half_unit, text_similarity


def test_units_normalise_to_base_currency():
    assert normalize_value_and_unit("Rs. 150 Crore")[1] == normalize_value_and_unit("Rs. 1500 Mn")[1] == 1.5e9
    assert normalize_value_and_unit("₹8,142 Cr")[1] == 81_420_000_000
    assert normalize_value_and_unit("₹81,415Mn")[1] == 81_415_000_000
    assert normalize_value_and_unit("1,429K tonnes")[3] == "TONNES"
    v = parse_values(prenormalize("FY23: ₹(452) Cr / (6.3%)"))
    assert [x.normalized for x in v] == [-4.52e9, -6.3]
    assert parse_values("781 bps")[0].normalized == 7.81


def test_years_and_footnote_numbers_are_not_values():
    vals = parse_values("revenue as of Fiscal 2021 grew, see note 3")
    assert vals == []


def test_period_parsing_across_styles():
    assert normalize_period("FY 2023-24").canonical == normalize_period("FY24").canonical == "FY2024"
    assert normalize_period("Fiscal 2021").canonical == "FY2021"
    assert normalize_period("2024-25").canonical == normalize_period("FY2024/25").canonical == "FY2025"
    assert normalize_period("Q4 FY24").canonical == "FY2024-Q4"
    assert normalize_period("as of March 31, 2024").canonical == "2024-03-31"
    assert normalize_period("nine months period ended December 31, 2021").canonical == "9M-ended-2021-12-31"
    assert normalize_period("in 2024").canonical == "CY2024"
    # a spread of years is not a fiscal year
    assert [p.canonical for _, _, p in find_periods("between 2015-2030")] == []


def test_a_quarter_written_with_of_is_not_read_as_the_full_year():
    """'Q2 of FY24' is a quarter, not FY2024; otherwise a quarterly figure is
    compared against a full-year one and reported as a contradiction."""
    assert normalize_period("Q2 of FY24").canonical == "FY2024-Q2"
    assert normalize_period("Q3 of fiscal 2025").canonical == "FY2025-Q3"
    sentence = ("India's current account deficit (CAD) moderated slightly to 1.2 per cent of GDP "
                "in Q2 of FY25 against 1.3 per cent of the GDP recorded in Q2 of FY24.")
    assert [p.canonical for _, _, p in find_periods(sentence)] == ["FY2025-Q2", "FY2024-Q2"]


def test_entities_and_metrics():
    assert normalize_entity("Delhivery Limited") == normalize_entity("DELHIVERY LIMITED") == "delhivery"
    assert normalize_metric("Revenue from Operations")[0] == normalize_metric("Topline")[0] == "financial.revenue"
    assert normalize_metric("Adjusted EBITDA")[0] == "financial.ebitda"
    assert normalize_metric("Something the taxonomy has never seen")[0].startswith("metric.")


def _fact(value: float, raw: str, unit: str, period: str, doc: str, scope: str = "unspecified",
          basis: str = "As reported", qualifiers=None, metric: str = "financial.revenue", numeric=True, conf: float = 0.9) -> Fact:
    return Fact(document_id=doc, document_name=doc, entity="Acme", entity_canonical="acme", metric=metric, metric_canonical=metric,
                raw_value=raw, numeric_value=value if numeric else None, normalized_numeric_value=value if numeric else None,
                unit=unit, normalized_unit=unit if numeric else "TEXT",
                period=FactPeriod(raw=period, period_type=PeriodType.FISCAL_YEAR, canonical=period),
                context=FactContext(scope=scope, accounting_standard=basis, qualifiers=qualifiers or []),
                source_evidence=SourceEvidence(document_id=doc, document_name=doc, page_number=1, exact_quote=raw), confidence=conf)


def test_reasoner_verdicts():
    R = DeterministicReasoningEngine.evaluate_fact_pair
    a = _fact(1.5e9, "Rs. 150 Cr", "INR", "FY2024", "ar.pdf")
    b = _fact(1.5e9, "Rs. 1500 Mn", "INR", "FY2024", "deck.pdf")
    assert R(a, b).relationship_type == RelationshipType.CORROBORATED
    # different period -> explained, not a contradiction
    assert R(a, _fact(1.2e9, "Rs. 120 Cr", "INR", "FY2023", "deck.pdf")).reasoning_trace.primary_divergence_factor == "DIFFERENT_PERIODS"
    # same period, different basis -> explained
    adj = _fact(1.8e9, "Rs. 180 Cr", "INR", "FY2024", "deck.pdf", basis="Non-GAAP (adjusted)")
    assert R(a, adj).reasoning_trace.primary_divergence_factor == "SCOPE_OR_BASIS"
    # estimate vs reported -> explained by vintage
    est = _fact(6.4, "6.4 per cent", "PERCENT", "FY2025", "survey.pdf", qualifiers=["estimate"], metric="macro.gdp_growth")
    act = _fact(6.5, "6.5 percent", "PERCENT", "FY2025", "imf.pdf", metric="macro.gdp_growth")
    assert R(est, act).reasoning_trace.primary_divergence_factor == "DATA_VINTAGE"
    # same everything, different value -> contradiction
    c = _fact(1.7e9, "Rs. 170 Cr", "INR", "FY2024", "deck.pdf")
    assert R(a, c).relationship_type == RelationshipType.CONTRADICTION
    # units that cannot be compared yield nothing
    assert R(a, _fact(1.5e9, "$1.5 bn", "USD", "FY2024", "deck.pdf")) is None


def test_rounding_tolerance_and_text_similarity():
    assert rounding_half_unit("1.4 Mn") == 50_000
    coarse = _fact(1_400_000, "1.4 Mn Tons", "TONNES", "FY2024", "deck.pdf", metric="ops.ptl_tonnage")
    fine = _fact(1_429_000, "1,429K tonnes", "TONNES", "FY2024", "ar.pdf", metric="ops.ptl_tonnage")
    assert DeterministicReasoningEngine.evaluate_fact_pair(coarse, fine).relationship_type == RelationshipType.CORROBORATED
    assert text_similarity("42 Bandra-Kurla Complex, Mumbai 400051", "42, Bandra Kurla Complex, Mumbai - 400051") > 0.6


def test_status_change_over_time_is_not_a_contradiction():
    active = _fact(0, "active", "", "2022-05-14", "prospectus.pdf", metric="governance.board_status", numeric=False)
    ceased = _fact(0, "ceased", "", "2023-09-27", "ar.pdf", metric="governance.board_status", numeric=False)
    rel = DeterministicReasoningEngine.evaluate_fact_pair(active, ceased)
    assert rel.relationship_type == RelationshipType.CONTEXTUALLY_EXPLAINED
    assert rel.reasoning_trace.primary_divergence_factor == "STATUS_CHANGED_OVER_TIME"


# --- identifier-aware similarity (Task 7b) ------------------------------------------


def test_one_character_identifier_drift_is_a_revision_not_a_contradiction():
    """A CIN-shaped identifier differing by one character, from documents with different dates,
    must read as a revision - not a value conflict."""
    old = _fact(0, "U63090DL2011PLC221234", "", "2022-05-14", "prospectus.pdf",
                metric="attr.corporate_identity_number_cin", numeric=False)
    new = _fact(0, "L63090DL2011PLC221234", "", "2024-03-31", "annual-report.pdf",
                metric="attr.corporate_identity_number_cin", numeric=False)
    rel = DeterministicReasoningEngine.evaluate_fact_pair(old, new)
    assert rel.relationship_type == RelationshipType.CONTEXTUALLY_EXPLAINED
    assert rel.reasoning_trace.primary_divergence_factor == "IDENTIFIER_REVISED"
    summary = rel.reasoning_trace.summary
    assert "U63090DL2011PLC221234" in summary and "L63090DL2011PLC221234" in summary
    assert "prospectus.pdf" in summary and "annual-report.pdf" in summary
    assert "1 character" in summary and "21" in summary


def test_genuinely_different_identifiers_still_contradict():
    """The identifier fix must not blanket-suppress real conflicts: two identifiers that share
    almost no characters are still a contradiction."""
    a = _fact(0, "INE040A01034", "", "2022-05-14", "prospectus.pdf",
              metric="attr.isin", numeric=False)
    b = _fact(0, "XJ998877ZZQ1", "", "2024-03-31", "annual-report.pdf",
              metric="attr.isin", numeric=False)
    rel = DeterministicReasoningEngine.evaluate_fact_pair(a, b)
    assert rel.relationship_type == RelationshipType.CONTRADICTION
    assert rel.reasoning_trace.primary_divergence_factor == "ATTRIBUTE_CONFLICT"


def test_identical_identifiers_still_corroborate():
    a = _fact(0, "U63090DL2011PLC221234", "", "2022-05-14", "prospectus.pdf",
              metric="attr.corporate_identity_number_cin", numeric=False)
    b = _fact(0, "U63090DL2011PLC221234", "", "2024-03-31", "annual-report.pdf",
              metric="attr.corporate_identity_number_cin", numeric=False)
    rel = DeterministicReasoningEngine.evaluate_fact_pair(a, b)
    assert rel.relationship_type == RelationshipType.CORROBORATED


def test_prose_attribute_values_still_use_token_set_similarity():
    """Pin that identifier handling did not leak into prose (multi-word) attribute values: a
    registered address with partial word overlap must still use token-set Jaccard and land on
    the same 'needs review' verdict it did before this change."""
    a = _fact(0, "Registered office at Plot 5, Sector 44, Gurugram", "", "2022-05-14", "prospectus.pdf",
              metric="attr.registered_office", numeric=False)
    b = _fact(0, "Corporate office at Plot 5, Industrial Area, Gurugram", "", "2024-03-31", "annual-report.pdf",
              metric="attr.registered_office", numeric=False)
    rel = DeterministicReasoningEngine.evaluate_fact_pair(a, b)
    expected_sim = text_similarity(a.raw_value.lower(), b.raw_value.lower())
    assert 0.3 <= expected_sim < 0.6
    assert rel.relationship_type == RelationshipType.INSUFFICIENT_EVIDENCE
    assert rel.reasoning_trace.primary_divergence_factor == "PARTIAL_TEXT_MATCH"
    assert rel.reasoning_trace.checks[-1].details["similarity"] == round(expected_sim, 2)


def test_identifier_revision_is_structural_not_tuned_to_cins():
    """A completely different identifier shape (a VAT-style id, not a CIN) must be handled the
    same way, proving the rule is about shape and character distance, not one document set."""
    a = _fact(0, "GB123456789", "", "2022-05-14", "prospectus.pdf", metric="attr.vat_id", numeric=False)
    b = _fact(0, "GB123456780", "", "2024-03-31", "annual-report.pdf", metric="attr.vat_id", numeric=False)
    rel = DeterministicReasoningEngine.evaluate_fact_pair(a, b)
    assert rel.relationship_type == RelationshipType.CONTEXTUALLY_EXPLAINED
    assert rel.reasoning_trace.primary_divergence_factor == "IDENTIFIER_REVISED"


def test_attribute_facts_carry_no_meaningful_period():
    """An attr.* fact's period is inherited from the document's publication date, not stated next
    to the value - so it must be marked NOT_APPLICABLE, not POINT_IN_TIME, or the UI would print
    a misleading 'reporting period' for an identity attribute."""
    md = DocumentMetadata(filename="prospectus.pdf", original_name="prospectus.pdf", page_count=1,
                          file_size_bytes=1, primary_entity="Acme Corporation Limited", document_date="2022-05-14")
    page = DocumentPage(page_number=1, text="Registered Office: 42 Example Street, Example City")
    doc = Document(metadata=md, pages=[page])
    facts = FactExtractor.extract_from_document(doc)
    attribute_facts = [f for f in facts if f.metric_canonical.startswith("attr.")]
    assert attribute_facts, "the KV-line extractor should have produced at least one attr.* fact"
    for f in attribute_facts:
        assert f.period.period_type == PeriodType.NOT_APPLICABLE


def _doc_from_pages(texts, original_name="report.pdf", **md_kwargs):
    md = DocumentMetadata(filename=original_name, original_name=original_name,
                          page_count=len(texts), file_size_bytes=1, **md_kwargs)
    pages = [DocumentPage(page_number=i + 1, text=t, sentences=_split_sentences(t))
             for i, t in enumerate(texts)]
    return Document(metadata=md, pages=pages)


def test_publisher_named_up_front_beats_a_company_mentioned_only_in_the_body():
    """A central bank's report cites other companies far more often than it names itself in full.
    An organisation named in the opening pages outranks a corporate name that never appears there."""
    doc = _doc_from_pages([
        "CONTENTS\nPART TWO: THE WORKING AND OPERATIONS OF THE RESERVE BANK OF INDIA",
        "I. ASSESSMENT AND PROSPECTS", "II. ECONOMIC REVIEW",
        "Procurement by Food Corporation was higher. Food Corporation held record stocks.",
        "Food Corporation offtake rose. Stocks with Food Corporation stayed above the norm.",
    ])
    FactExtractor.resolve_document_context(doc)
    assert doc.metadata.primary_entity == "Reserve Bank of India"


def test_a_table_of_contents_line_is_not_taken_as_the_entity():
    """When an excerpt has no cover page, the first title-like line is a contents-table heading.
    Fall through to the name the document repeats across its pages instead."""
    doc = _doc_from_pages([
        "CONTENTS\nChapter No.\t Page No. Name of the Chapter\nState of the Economy",
        "Acme Institute finds growth steady. Acme Institute expects the trend to hold.",
        "Acme Institute notes prices eased. The Acme Institute survey covers all states.",
        "Acme Institute projects a stable outlook. Acme Institute publishes this annually.",
    ])
    FactExtractor.resolve_document_context(doc)
    assert doc.metadata.primary_entity == "Acme Institute"


def test_pdf_author_names_the_entity_when_the_text_does_not():
    """A short document with no repeated organisation name still has a publisher in its PDF
    metadata; that beats guessing at a heading."""
    doc = _doc_from_pages(["Engineering Intern Hiring Assignment", "Build a fact knowledge layer."],
                          pdf_author="Superjoin")
    FactExtractor.resolve_document_context(doc)
    assert doc.metadata.primary_entity == "Superjoin"


def test_a_sentence_stitched_onto_a_running_header_is_not_trusted():
    """Chart and column blocks split a sentence, and reflow glues the tail onto the page's running
    header. The tail reads as a sentence but has lost the subject its number belonged to, so the
    fact must stay below the matching threshold instead of contradicting a real figure."""
    from backend.app.services.extractor import MATCH_THRESHOLD
    fragment = "ECONOMIC REVIEW headline inflation during 2024-25 as compared with 61 per cent a year ago."
    doc = _doc_from_pages(["Prices and inflation.", fragment])
    facts = FactExtractor.extract_from_document(doc)
    inflation = [f for f in facts if f.numeric_value == 61]
    assert inflation, "the extractor should still record the figure, just not trust it"
    for f in inflation:
        assert f.confidence < MATCH_THRESHOLD, f"fragment fact kept confidence {f.confidence}"
        assert "running header" in (f.notes or "")
    # a fragment that carries no other risk must also fall below the gate on the header alone
    clean = _doc_from_pages(["Prices and inflation.",
                             "ECONOMIC REVIEW headline inflation was 4.6 per cent during 2024-25."])
    for f in FactExtractor.extract_from_document(clean):
        assert f.confidence < MATCH_THRESHOLD, f"fragment fact kept confidence {f.confidence}"


def test_a_window_written_next_to_the_year_keeps_the_period_partial():
    """'4.9 per cent in FY25 (April-December)' is nine months of FY25, not the year. The window is
    attached to this value's own period, so the fact must keep the partial_period qualifier and be
    reconcilable against a full-year average instead of contradicting it."""
    doc = _doc_from_pages([
        "Retail inflation moderated from 5.4 per cent in FY24 to 4.9 per cent in FY25 (April-December).",
    ])
    facts = FactExtractor.extract_from_document(doc)
    partial = [f for f in facts if f.numeric_value == 4.9]
    assert partial, "the 4.9 per cent figure should be extracted"
    assert all("partial_period" in f.context.qualifiers for f in partial)


def test_a_quarter_named_elsewhere_does_not_make_a_full_year_partial():
    """The counter-case: a sentence may carry claims about several periods. A figure whose own
    period is the full year stays whole even though a quarter is named later in the sentence."""
    doc = _doc_from_pages([
        "Revenue was 250 million in FY23, and the company turned profitable in Q3 FY24.",
    ])
    facts = FactExtractor.extract_from_document(doc)
    full_year = [f for f in facts if f.numeric_value == 250]
    assert full_year, "the 250 million figure should be extracted"
    assert all("partial_period" not in f.context.qualifiers for f in full_year)
