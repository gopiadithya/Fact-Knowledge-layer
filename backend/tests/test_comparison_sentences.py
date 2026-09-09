"""A single sentence can state several claims. Binding each value to the right period is a
grammar problem, not a proximity problem: "<subject period> M increased by <delta> to <new>
from <old> in <old period>". These checks pin that down on real sentences."""
import os

import pytest

from backend.app.services.extractor import FactExtractor
from backend.app.services.parser import PDFParser

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DECK = os.path.join(ROOT, "starter-datasets", "delhivery",
                    "03-delhivery-q4-fy24-earnings-presentation.pdf")


@pytest.fixture(scope="module")
def page5_facts():
    doc = PDFParser.parse_pdf(DECK, os.path.basename(DECK))
    facts = FactExtractor.extract_from_document(doc)
    # the "FY24 EBITDA increased by Rs. 578 Cr to Rs. 127 Cr from Rs. (452 Cr) in FY23" sentence
    return [f for f in facts if f.source_evidence.page_number == 5 and "578" in f.source_evidence.exact_quote]


def _levels(facts, canonical):
    """(value, period) for facts that state a level of the metric, excluding change/delta facts."""
    return {(f.numeric_value, f.period.canonical)
            for f in facts if f.metric_canonical == canonical}


def test_ebitda_levels_carry_their_own_fiscal_year(page5_facts):
    """127 belongs to FY24 (the subject period), -452 to FY23 (the period after "from")."""
    assert _levels(page5_facts, "financial.ebitda") == {(127.0, "FY2024"), (-452.0, "FY2023")}


def test_pat_values_are_not_attributed_to_ebitda(page5_facts):
    """The PAT clause later in the sentence must not be hoovered up by the EBITDA mention."""
    ebitda_values = {f.numeric_value for f in page5_facts if f.metric_canonical.startswith("financial.ebitda")}
    assert not ({759.0, -1008.0} & ebitda_values)


def test_change_is_a_distinct_metric_not_a_level(page5_facts):
    """"increased by Rs. 578 Cr" is a delta; recording it as an EBITDA level would create a
    false intra-document contradiction against the real FY24 level of 127."""
    changes = [f for f in page5_facts if f.metric_canonical == "financial.ebitda.change"]
    assert [(f.numeric_value, f.period.canonical) for f in changes] == [(578.0, "FY2024")]


def test_annual_facts_do_not_inherit_partial_period_from_a_quarter_elsewhere(page5_facts):
    """The sentence also mentions "Q3 FY24"; that must not mark the full-year facts partial."""
    annual = [f for f in page5_facts if f.period.canonical in ("FY2023", "FY2024")]
    assert annual
    for f in annual:
        assert "partial_period" not in f.context.qualifiers, f"{f.metric} {f.numeric_value} {f.period.canonical}"
