# Generalising the fact knowledge layer to unseen documents

Design, 2026-09-09.

## Why

The starter PDFs are samples. Evaluators will upload documents this system has
never seen, and the assignment forbids document-specific rules. Today the system
does not meet that bar, and the gap is measurable.

An unseen document is, to this code, a document whose words miss the metric
taxonomy. Simulating that by disabling the taxonomy and re-running the two
starter corpora:

| Corpus | Taxonomy matches | Taxonomy blind |
|---|---|---|
| Delhivery, 3 docs | 293 facts, 210 cross-document pairs, 11 corroborated | 175 facts, **6** pairs, 1 corroborated |
| India macro, 3 docs | 299 facts, 70 cross-document pairs, 3 corroborated | 250 facts, **0** pairs, **0** corroborated |

Extraction degrades. Relationships collapse. Relationships are the graded
output, so on an unseen corpus the system currently produces almost nothing.

The cause is `matcher._key()`, which buckets facts on an exact
`(entity_canonical, metric_canonical, normalized_unit)` tuple. Open-schema keys
are slugs built from whatever words a sentence used, so `metric.loss_for_the_year`
and `metric.profit_after_tax` never land in the same bucket even when they
describe one claim. Across both corpora, 19 of 25 taxonomy keys appear in two or
more documents against 7 of 302 open keys. The hand-written taxonomy is doing all
the cross-document work, and it contains entries (`ops.ptl_tonnage`,
`ops.pin_codes`, `macro.*`) that exist only because of the samples.

## The invariant

Everything below serves one rule.

**The LLM never originates a number, a date, or a quote.**

The model receives text and returns judgements *about* text. Values are re-derived
from the source by the existing deterministic parser, and every quote must be
found verbatim in the page before a fact is stored. A model that fabricates a
figure produces a rejected claim, never a wrong fact.

Precedence is fixed and one-directional:

```
source text -> deterministic parser -> normalised value -> stored fact
                                                  ^
                                    LLM interpretation may label it,
                                    never supply or overrule it
```

This is not a prompt instruction. It is a gate the claim must pass through.

The repository has already been burned once from this direction: `README.md`
records that the first, Gemini-authored version of this project invented its four
cases, quoting figures that do not appear in the source PDFs. That history is the
reason the guarantee is structural.

## Architecture

```
                        DOCUMENT
                           |
              +------------+------------+
              v                         v
      DETERMINISTIC                 LLM CLAIM
      EXTRACTION                    EXTRACTION
              |                         |
              +------------+------------+
                           v
                    VERIFICATION GATE
                           |
                           v
                    FACT / CLAIM STORE
                           |
              +------------+------------+
              v                         v
      Predicate families        Entity resolution
              |                         |
              +------------+------------+
                           v
                  Candidate retrieval
                           |
                           v
                  Context equivalence
                           |
                           v
                Relationship reasoning
                           |
          +----------------+----------------+
          v                v                v
     Corroborate       Conflict      Contextual difference
```

The load-bearing idea: **canonicalisation retrieves candidates, it does not
decide truth.** Two claims may sit in one family without being one fact.

## Three levels of predicate, kept distinct

Collapsing these three concepts is what produces false conflicts.

| Level | Field | Source | Used for |
|---|---|---|---|
| Raw predicate | `metric` | the words in the document | display, evidence |
| Predicate family | `predicate_family` | LLM clustering | candidate retrieval only |
| Canonical predicate | `metric_canonical` | taxonomy hit or generated slug | high-confidence comparison |

`predicate_family` is broad on purpose: `"loss for the year"` and `"share of
associate's net profit"` both belong to a profitability family. That makes them
worth *examining* together. It never makes them the same fact.

## Phase A: predicate families

New module `backend/app/services/canonicalizer.py`.

After extraction, collect the distinct raw predicate labels across the layer
(a few hundred short strings) and make one Gemini call that groups them into
families with a confidence per member. The prompt contains **labels only** — no
values, no numbers, no page text.

Response schema:

```json
{
  "families": [
    {
      "family_id": "profitability_result",
      "family_label": "Profitability result",
      "members": [
        {"raw": "loss for the year", "confidence": 0.88},
        {"raw": "profit after tax", "confidence": 0.91}
      ]
    }
  ]
}
```

Rules stated in the prompt: never place a level and a change in one family;
never merge across clearly different scopes; leave a label in a family of its own
when unsure. Members below a confidence floor (0.6) are dropped to singleton
families.

`matcher._key()` becomes `(entity_canonical, predicate_family, normalized_unit)`,
which is the single change that ends the relationship collapse. Every
relationship records how the two facts met.

### Match basis policy

A relationship's permitted verdicts depend on how strong the equivalence
evidence is. This table is the governing rule.

| Match basis | Corroborate | Contradict | Needs review |
|---|---|---|---|
| Exact canonical predicate + same context | yes | **yes** | on ambiguity |
| LLM family + strong context match | yes, with reduced confidence | **never** | yes |
| Semantic similarity only | no | no | yes |
| Same surface label only | no | no | yes |

A family-only match may corroborate solely when entity, period, scope, basis,
unit and qualifiers all agree *and* the normalised values agree. A family-only
match with differing values is `INSUFFICIENT_EVIDENCE` — never `CONTRADICTION`.
Uncertainty introduced by clustering must not be laundered into a confident
claim of conflict.

`FactRelationship` gains `match_basis: "exact" | "family" | "surface"`, surfaced
in the reasoning trace so the UI can explain why two facts were compared.

## Phase B: tiered LLM claim extraction

New module `backend/app/services/llm_extractor.py`.

Phase B runs across **all** pages, not only pages where deterministic extraction
found little. "Few numbers" does not mean "little information": a page saying
*"the company transitioned to a subscription-based operating model"* carries a
real semantic fact and would be skipped by a value-triggered heuristic. The
assignment allows facts to be numerical or semantic.

Cost is controlled by tiering rather than by skipping pages.

| Pass | Engine | Scope |
|---|---|---|
| 1 | deterministic | every page: text, tables, numbers, units, dates, currencies |
| 2 | fast model | every page batch: semantic claims and context |
| 3 | strong model | only ambiguous claims, candidate contradictions, unresolved context, uncertain families, verification failures worth retrying |

Model ids are configuration, never constants in code. Google's line-up moves;
`gemini-3.8-flash` and the Flash-Lite tier are today's defaults, and both are
overridable by environment variable.

### Modes

| `LLM_MODE` | Behaviour |
|---|---|
| `off` | deterministic only; the pipeline as it exists today |
| `hybrid` | deterministic + fast-model extraction on all pages + strong model on ambiguous cases |
| `full` | full LLM extraction and verification, strong model throughout |

`hybrid` is the demo default. `full` is for development and evaluation.
`off` is the automatic fallback when no key is configured.

### Claim schema

The model returns, per claim: `subject`, `predicate`, `value_text` exactly as
written, `unit_text`, `period_text`, `scope`, `qualifiers[]`, `exact_quote`,
and the provenance of subject and period (see below).

## The verification gate

New module `backend/app/services/verification.py`. Every proposed claim must
survive every check.

| # | Check | On failure |
|---|---|---|
| 1 | `exact_quote` occurs in the page text, whitespace-normalised | discard, `quote_not_found` |
| 2 | `value_text` occurs inside that quote | discard, `value_not_in_quote` |
| 3 | `parse_values` re-derives a number from the quote | discard, `value_unparseable` |
| 4 | parser's number differs from the model's | **parser wins**, `value_corrected` |
| 5 | model's unit and scale agree with the parser's after normalisation | discard, `unit_scale_error` |

Check 5 is the specific guard against a plausible-looking scale error. `₹2.49
billion` proposed against a source reading `₹2,491.86 million` is *accepted*,
because both normalise to the same quantity. `₹2.49 billion` against a parser
result of `₹2.49186 million` is rejected as a scale error rather than silently
stored.

Claims that pass are stored with `extraction_method="llm_verified"` and a
confidence ceiling below that of a clean deterministic extraction.

### Provenance, not quote-literalism

Numbers and quotes get the strictest validation. Context legitimately comes from
elsewhere on the page, and discarding it would be wrong: a table column headed
`FY2024` establishes the period for every cell beneath it, and a quote reading
*"revenue increased 14% during the year"* names no fiscal year at all.

So `FactPeriod` and the subject each gain a `source` field:

```
quote | table_header | section_header | page_context | document_metadata | inferred
```

Confidence scales with provenance strength, and the UI shows where the context
came from. The principle: **every assertion must be traceable to source
material, but not necessarily to the exact quote string.** What may never happen
is a period or subject that appears nowhere in the document.

### Merging with deterministic facts

Union, then dedup on `(predicate_family, period, normalised value, page)`.
Deterministic facts win ties: they are exact by construction. This also means a
Gemini outage degrades the system to today's behaviour instead of breaking it.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `GEMINI_API_KEY` | unset | absent means `LLM_MODE=off`, whatever else is configured |
| `LLM_MODE` | `hybrid` | `off` / `hybrid` / `full` |
| `LLM_MODEL_FAST` | a Flash-Lite tier model | pass 2 |
| `LLM_MODEL_STRONG` | `gemini-3.8-flash` | pass 3 |

Read from the environment and from a gitignored `backend/.env`. The key is never
committed. `.env` and `backend/.llm-cache/` are in `.gitignore`.

SDK: `google-genai` (`pip install google-genai`, `from google import genai`,
`genai.Client()`), with structured output requested via a Pydantic
`model_json_schema()`.

`GET /api/status` reports the active mode so the running configuration is
visible without reading logs:

```json
{"llm_enabled": true, "mode": "hybrid",
 "model_fast": "...", "model_strong": "gemini-3.8-flash"}
```

The UI shows the mode, so a keyless evaluator sees a working deterministic app
and an honest label rather than a stack trace.

## Caching

Responses are cached on disk under `backend/.llm-cache/`, keyed by

```
SHA256(model_id + prompt_version + schema_version + input_text)
```

Every component matters: without `prompt_version` and `schema_version` a prompt
edit silently reuses stale responses shaped for the old contract. The cache is
gitignored — it holds uploaded document text and model output.

## Observability

Extraction reports rejection counts per document and per corpus:

```
Claims proposed:  412
Accepted:         383
Rejected:          29
    11 quote not found
     7 value not in quote
     6 value mismatch (parser won)
     5 unit/scale error
```

This is exposed through the API and the UI. It is a stronger statement than
"Gemini extracted 383 facts", because it demonstrates the model is subordinate to
the verification system, and it doubles as the assignment's required
extraction-failure discussion.

## Testing

The gate is testable without any API key, and most of the suite must stay that
way so the project builds on a machine with no credentials.

1. **Adversarial gate tests, no key required.** Hand-written claim payloads: a
   fabricated number; a quote absent from the page; a value absent from its
   quote; correct digits at the wrong scale; a period naming a year the document
   never mentions. Each must be rejected with the right reason code.
2. **Recorded-fixture replay.** One real Gemini response per phase, committed as
   a JSON fixture and replayed through the gate, so contract changes surface as
   test failures.
3. **The blind experiment as a regression test.** Taxonomy disabled, Phase A
   families supplied from a fixture: cross-document relationships must return far
   above the current floor of 6 and 0.
4. **Precision guards.** No family-only match may ever produce a
   `CONTRADICTION`. The existing 25 tests must stay green with no key set.
5. **Unseen-domain smoke test.** At least one PDF from a domain outside finance
   and macroeconomics, checked for sane entity resolution, grounded quotes and
   zero fabricated values.

## Cost

One call for Phase A per corpus. Phase B at roughly two pages per call: a
27-page deck is about 14 fast-model calls, with the strong model touching only
the ambiguous minority. Caching makes repeat runs and the test suite free.

## Risks

- **Bad families.** Mitigated by the match-basis policy: a family-only match can
  never assert a contradiction, and low-confidence members fall back to
  singletons.
- **Latency on upload.** Phase B is per page. Batching and caching help; if it
  proves too slow for a live demo, `LLM_MODE` drops to `off` and the app still
  works.
- **Model availability drift.** Model ids are configuration, and `off` is always
  a working fallback.
- **Key exposure.** The development key was shared in plain text and must be
  rotated before submission.

## Out of scope

Renaming `metric` to `predicate` across the API and UI; replacing the
deterministic extractor; a vector store. The deterministic path stays as the
precision floor and the offline fallback.

## Open question

The taxonomy keeps its accelerator role, but `ops.ptl_tonnage`, `ops.pin_codes`
and the `macro.*` entries were written for the sample documents. Once families
carry retrieval, these can be demoted to plain aliases or deleted. That is a
follow-up, to be decided on evidence after Phase A lands.
