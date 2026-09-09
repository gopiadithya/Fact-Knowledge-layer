# Fact schema

Every fact the layer stores is one JSON object with the same shape, whatever kind of document it came from. The example below is a real output from the Delhivery annual report sample.

```json
{
  "id": "fact_3f2a9c1e",
  "document_id": "doc_8b1c2d3e",
  "document_name": "02-delhivery-annual-report-fy24-excerpt.pdf",
  "entity": "Delhivery Limited",
  "entity_canonical": "delhivery",
  "metric": "Revenue",
  "metric_canonical": "financial.revenue",
  "metric_category": "financial_flow",
  "raw_value": "₹81,415.38 million",
  "numeric_value": 81415.38,
  "normalized_numeric_value": 81415380000.0,
  "unit": "INR Million",
  "normalized_unit": "INR",
  "period": { "raw": "FY24", "period_type": "fiscal_year", "canonical": "FY2024", "start_date": "2023-04-01", "end_date": "2024-03-31" },
  "context": { "scope": "unspecified", "accounting_standard": "As reported", "qualifiers": [], "segment_name": null },
  "source_evidence": {
    "document_id": "doc_8b1c2d3e",
    "document_name": "02-delhivery-annual-report-fy24-excerpt.pdf",
    "page_number": 36,
    "exact_quote": "Revenues from customers increased by 12.68% to ₹81,415.38 million for FY24 from ₹72,253.01 million for FY23.",
    "char_start": 1412,
    "char_end": 1521
  },
  "confidence": 0.9,
  "extraction_method": "rule_sentence",
  "notes": null
}
```

## Fields that matter for comparison

| Field | What it is | Used by |
| --- | --- | --- |
| `entity_canonical` | Lower-cased entity slug with corporate suffixes removed (`Delhivery Limited` and `DELHIVERY LIMITED` both become `delhivery`). Macro metrics use the country the report is about, not the publisher. People (board events) use their own name. | Reasoner step 1 |
| `metric_canonical` | A taxonomy key (`financial.revenue`, `ops.headcount`, `macro.gdp_growth`, ...) when the label matched a known keyword; otherwise a generated key: `metric.<slug>` for numeric labels read from the text, `attr.<slug>` for identifiers and addresses, `governance.board_status` for board events. A period-over-period movement gets its own key, the level's key plus `.change` (`financial.ebitda.change`), so step 2 never compares a movement against a level. | Reasoner step 2, bucketing |
| `normalized_numeric_value`, `normalized_unit` | Base units: currency in whole units (`INR`, `USD`), `PERCENT`, `COUNT`, `TONNES`. Crore, lakh, million, billion, K and bps are resolved here. Losses are negative. | Reasoner step 5; units must be identical to compare |
| `period.canonical` | `FY2024`, `FY2024-Q4`, `FY2025-H1`, `9M-ended-2021-12-31`, `2024-03-31`, `2025-09`, `CY2024`, or `unknown`. Indian fiscal years (`2024-25`, `FY 2023-24`, `Fiscal 2021`) and IMF style (`FY2024/25`) all map to the year they end in. | Reasoner step 3 |
| `context.scope` | `consolidated`, `standalone`, `segment`, `corporate`, or `unspecified` (from the sentence itself). | Reasoner step 4 |
| `context.accounting_standard` | `As reported` or `Non-GAAP (adjusted)` when "adjusted"/"adj." sits next to the metric. | Reasoner step 4 |
| `context.qualifiers` | `estimate`, `partial_period`, `pro_forma`, `exclusions_apply`, `rate_or_capacity`, `cumulative`, `yoy_growth_present`, `adjusted_non_gaap`. | Reasoner step 4 and confidence |
| `confidence` | Starts at 0.9 for a sentence with keyword, unit-bearing value and period, 0.85 for a KPI tile, and is reduced for each risk the extractor can see (no period, no scale word, value next to a component word, activity counts, industry figures). Facts under the matching threshold (0.6) are shown but never matched. | Matcher |
| `extraction_method` | `rule_sentence`, `rule_open_schema`, `rule_tile`, `rule_governance`, `rule_attribute`. | Facts table |

## Relationship

```json
{
  "id": "rel_1a2b3c4d",
  "fact_a_id": "fact_3f2a9c1e",
  "fact_b_id": "fact_9d8e7f6a",
  "relationship_type": "CORROBORATED",
  "confidence": 0.9,
  "reasoning_trace": {
    "checks": [
      { "step_number": 1, "name": "Entity", "passed": true, "explanation": "Both facts are about 'Delhivery Limited'" },
      { "step_number": 2, "name": "Metric", "passed": true, "explanation": "'Revenue' and 'Revenue' both map to financial.revenue" },
      { "step_number": 3, "name": "Period", "passed": true, "explanation": "Same reporting period (FY2024)" },
      { "step_number": 4, "name": "Context", "passed": true, "explanation": "Same scope, basis and qualifiers" },
      { "step_number": 5, "name": "Value", "passed": true, "explanation": "₹81,415.38 million = 81,415,380,000 vs ₹8,142 Cr = 81,420,000,000 INR (difference 0.01%)" }
    ],
    "summary": "Both sources report Revenue for FY2024 as the same value (₹81,415.38 million in 02-delhivery-annual-report-fy24-excerpt.pdf; ₹8,142 Cr in 03-delhivery-q4-fy24-earnings-presentation.pdf).",
    "primary_divergence_factor": null
  },
  "delta_percentage": 0.01
}
```

`relationship_type` is one of `CORROBORATED`, `CONTRADICTION`, `CONTEXTUALLY_EXPLAINED`, `INSUFFICIENT_EVIDENCE`. `primary_divergence_factor` says why a pair is not a plain corroboration: `DIFFERENT_PERIODS`, `SCOPE_OR_BASIS`, `DATA_VINTAGE`, `PARTIAL_PERIOD`, `STATUS_CHANGED_OVER_TIME`, `VALUE_CONFLICT`, `COMPETING_ESTIMATES`, `ATTRIBUTE_CONFLICT`, `STATUS_CONFLICT`, `PARTIAL_TEXT_MATCH`, `SAME_LABEL_DIFFERENT_SUBJECTS`.
