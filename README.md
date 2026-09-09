# Fact Knowledge Layer

Upload PDFs. The system pulls out numerical and semantic facts, ties each one to the page and sentence it came from, and then compares facts across documents: it says when two documents corroborate each other, when they contradict, and when an apparent contradiction is explained by period, scope, accounting basis, data vintage or a status that changed over time. Every verdict comes with a five-step reasoning trace you can read.

Submission for the Superjoin VIT 2026 engineering-intern assignment.

## Setup and run instructions

Prerequisites: Python 3.9+, Node 18+. No API key and no paid service are required.

Backend (FastAPI, PyMuPDF):

```bash
python -m venv backend/.venv
source backend/.venv/bin/activate      # Windows: backend\.venv\Scripts\activate
pip install -r backend/requirements.txt
python -m pytest backend/tests -q      # 81 tests, ~35 s (they parse the sample PDFs)
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Run both commands from the repository root: the tests and the app import `backend.app.*`, so the root has to be on `sys.path`.

Frontend (React + Vite), in a second terminal:

```bash
cd frontend
npm install
npm run dev -- --port 5173
```

Open http://localhost:5173 and drop PDFs on the panel. Interactive API docs are at http://localhost:8000/docs. If the API runs elsewhere, set `VITE_API_URL` (default `http://127.0.0.1:8000/api`).

To try it without the UI:

```bash
# load a sample set: any sub-folder of starter-datasets/ works
curl -X POST "http://127.0.0.1:8000/api/load-preset?name=delhivery"
curl -X POST "http://127.0.0.1:8000/api/load-preset?name=india-macroeconomy&replace=false"
curl -X POST "http://127.0.0.1:8000/api/load-preset?name=synthetic-conflict&replace=false"

# or add your own, compared against everything already loaded
curl -F "files=@my-filing.pdf" "http://127.0.0.1:8000/api/documents/upload?incremental=true"

curl http://127.0.0.1:8000/api/cases
```

The knowledge layer is snapshotted to `backend/data/store.json`, so a restart keeps what was loaded.

### Optional: the LLM step

The pipeline is deterministic and runs fully without a key. One optional step uses Gemini, and only for grouping metric **labels** — never values, never page text (details under [The LLM step](#the-llm-step)). To enable it, create `backend/.env`:

```
GEMINI_API_KEY=your-key
LLM_MODE=hybrid      # `off` disables the step; anything without a key falls back to off
```

`backend/.env` is git-ignored and no key is committed. With no key the system reports `"llm_enabled": false` on `GET /api/status` and runs the same pipeline with exact-key label grouping instead. On the three sample sets loaded together (8 PDFs, 597 facts) that is the only difference in output:

| | facts | comparison pairs | corroborated | contradiction | context-explained | needs review |
| --- | --- | --- | --- | --- | --- | --- |
| No key (`off`) | 597 | 508 | 21 | 2 | 457 | 28 |
| With key (`hybrid`) | 597 | 557 | 22 | 2 | 500 | 33 |

Same facts, same verdicts on all four required cases; the LLM only widens which pairs are worth comparing. Every number below was reproduced in both modes.

## Approach

### What a fact is

The documents decide. A fact is any statement the extractor can ground in one sentence or one KPI tile of one page, with a subject (a company, a country, or a person), a label, a value and, where the text gives one, a period and context. Values are numeric (money in any scale and currency, percentages, counts, tonnage) or textual (an address, an identifier, a board status). The schema is documented in `docs/fact-schema.md`.

### The interface

A sidebar dashboard with six views, all driven by the same API. Documents are added and removed on the Overview, or dropped anywhere on it.

| View | What it answers |
| --- | --- |
| **Overview** | What should I look at? Documents in and out, a ranked review queue whose rows open the evidence in place, a matrix of which documents agree or disagree, which figures more than one source confirms, and how much of each document made it in. |
| **Examples** | What does the engine conclude, and why? Worked examples of agreement, conflict, context-explained difference, and uncertain extraction. |
| **All comparisons** | Every pair the engine evaluated, filterable by verdict, metric, and whether the pair crosses documents. The verdict filter separates figures that disagree for the same period — whether or not a reason was found — from pairs that simply cover different periods. |
| **Facts** | The full fact table with evidence, confidence and CSV export. |
| **Trends** | One figure tracked across every document and period, as a dot plot with per-point tooltips plus the underlying table. |
| **Fact types** | The built-in vocabulary next to the labels learned from your documents. |

Clicking any value opens the source page with the exact sentence highlighted. The statistics in the header are filters: "conflicts" opens the comparisons filtered to contradictions, a matrix cell opens one document pair, a metric row opens that metric.

### Pipeline

```
PDF ──► layout-aware parse ──► extraction ──► normalisation ──► bucketed matching ──► 5-step reasoning ──► evidence + trace
```

1. **Parse with layout** (`backend/app/services/parser.py`). PyMuPDF gives text blocks with positions. Blocks are ordered column by column and continuation lines are joined, which fixes the two-column interleaving in the RBI and IMF reports ("4.6 / per cent during 2024-25" becomes one sentence). Numeric-only blocks are paired with the label block sitting under or above them, which is how slide decks and annual-report highlight pages state their figures ("₹8,142 Cr" above "FY24 revenue from services"). pypdf is a fallback if PyMuPDF is missing.

2. **Extract** (`extractor.py`). Five deterministic sources, all document-agnostic:
   - *Taxonomy sentences*: a known metric keyword, a unit-bearing value of the right kind and a period in the same sentence. Each value is paired with the nearest period mention, so "₹81,415.38 million for FY24 from ₹72,253.01 million for FY23" yields two facts.
   - *Open-schema sentences*: `<label> was/stood at/of <value> [period]` for labels the taxonomy has never seen. The label becomes a generated `metric.<slug>` key. This is what makes the layer work on documents outside finance.
   - *KPI tiles*: value block + label block. Unknown labels are kept under generated keys.
   - *Board events*: "<person> resigned / ceased to be / was appointed ... with effect from <date>" and prospectus profiles ("X is a Non-Executive Director of our Company").
   - *Attributes*: identifiers (CIN, ISIN), registered office, auditor, and `Key: value` lines on cover pages.

   The document's own entity is resolved from the text, never from the filename when text is available: a corporate "<Name> Limited/Inc/..." string repeated through the document, or a known institution named in the opening pages, or — for an excerpt with no cover page at all — the organisation name the document repeats across pages, or the PDF's own author metadata. An organisation named up front outranks a company that appears only in the body, because a report cites others far more often than it names itself. Macro metrics are attributed to the country the report is about, so three publishers writing about India can be compared.

3. **Normalise** (`normalizer.py`). Crore, lakh, million, billion, K and bps to base units; `FY24`, `FY 2023-24`, `2024-25`, `Fiscal 2021`, `FY2024/25`, `Q4 FY24`, `Q2 of FY24`, `H1 FY25`, `nine months ended December 31, 2021`, `as of March 31, 2024`, `September 2025` to canonical periods; accounting negatives `(452)` and "loss of" to signed numbers; entity names to slugs.

4. **Confidence, not filtering.** Every fact gets a confidence that starts at 0.9 and drops for each risk the extractor can see: no period in the sentence, no scale word next to an amount, the value sitting next to "food"/"excluding"/"contribution", an activity count ("12,104 employees trained"), a per-day capacity, a change instead of a level ("improved by 781 bps"), an industry figure, or a sentence that begins with a page's running header and is therefore a fragment stitched across a chart break. Facts under 0.6 are kept and shown but never matched. This is the honest answer to case 4.

5. **Match** (`matcher.py`). Facts are bucketed by (entity, predicate family, unit); only facts in the same bucket are compared, so a layer with thousands of facts does not do N² work. Incremental upload compares new facts against the existing index and leaves existing pairs untouched.

6. **Reason** (`reasoner.py`). For each candidate pair: entity, metric, period, context, value. Tolerance for values is 1% or the rounding of the coarser figure ("1.4 Mn" allows ±0.05 Mn, so 1,429K tonnes agrees with it); percentages use 0.05 points. Text attributes use token overlap, so an address written two ways still corroborates. Verdicts and the reason for each are written into the trace the UI shows.

### The LLM step

`canonicalizer.py` is the one place a model is called, and it is optional. It groups the *labels* the extractor produced into predicate families — "Revenue", "Revenue from services", "Topline" — so that two documents naming one quantity differently land in the same comparison bucket. The prompt sees a sorted list of label strings and nothing else: no values, no periods, no page text. It therefore cannot put a wrong figure into the knowledge layer; the worst it can do is suggest a comparison, which the deterministic five-step reasoner then judges on its own.

Every response is cached on disk under a key that includes the model, the prompt version and the schema version. Any failure — no key, no network, a malformed response, an unparseable shape — raises `LLMUnavailable` and the family falls back to the exact metric key, which is what the system did before the model existed. `GET /api/status` reports which mode is live.

### The four cases, as produced by the system

Everything below is copied from `/api/cases` and `/api/relationships` with all three sample sets loaded into one layer (8 documents, 597 facts, 557 comparisons). Page numbers are PDF page indices in the excerpt files.

**1. Corroborated across documents, even though expressed differently.**

| Annual report FY24, p. 36 | Q4 FY24 earnings deck, p. 6 |
| --- | --- |
| "Revenues from customers increased by 12.68% to ₹81,415.38 million for FY24 from ₹72,253.01 million for FY23." | KPI tile "₹8,142 Cr" labelled "FY24 revenue from services" |

Trace: entity Delhivery = Delhivery; metric financial.revenue; period FY2024 = FY2024; context same; value 81,415,380,000 vs 81,420,000,000 INR, difference 0.01%. Verdict: corroborated.

The same pair of documents also corroborates express parcel volume ("740Mn – Express parcels shipped" vs "740 Mn – Express parcel shipments in FY24"), EBITDA (₹1,266 Mn vs ₹127 Cr), adjusted EBITDA (₹758 Mn vs ₹76 Cr), FY23 EBITDA ("a loss of ₹4,516 million in FY23" vs "₹(452) Cr"), PTL tonnage ("1,429K tonnes" vs "1.4 Mn Tons", accepted through rounding tolerance), and the registered office written two different ways ("... New Delhi – 110037, India" vs "... New Delhi 110037"). Across publishers, the RBI annual report (p. 8, "real gross domestic product (GDP) growth moderated to 6.5 per cent in 2024-25") and the IMF Article IV report (p. 3, "Following economic growth of 6.5 percent in FY2024/25") corroborate on GDP growth, and both give 4.6 per cent average headline inflation for the same year.

**2. A genuine contradiction, between two of the starter documents.**

| Economic Survey 2024-25, p. 87 | IMF Article IV 2025, p. 13 |
| --- | --- |
| "Assuming a normal monsoon and no further external or policy shocks, the RBI expects headline inflation to be **4.2 per cent** in FY26." | "Headline inflation is expected to remain benign and average **2.8 percent** in FY2025/26, below the 4-percent target but within the RBI's tolerance band." |

Trace: entity India = India; metric macro.inflation; period FY2026 = FY2026; context same — both are forward-looking, so neither is a later revision of the other; value 4.2 vs 2.8 percentage points, 1.4 points apart. Verdict: contradiction, factor `COMPETING_ESTIMATES`, and the summary says so plainly — *"Likely contradiction: two estimates for Headline CPI inflation in FY2026 disagree."* Two institutions publish different numbers for the same year and nothing in either document reconciles them. That is the honest reading, and the engine now reaches it: `test_two_institutions_forecasting_the_same_year_differently_is_a_contradiction` pins it.

Finding it took fixing a reasoning failure. The reconciler excuses a difference when one side is a projection and the other a reported actual. It read "*is* expected to ... average 2.8 percent" as a projection but not "the RBI *expects* ... to be 4.2 per cent", so a disagreement between two forecasts was filed as a difference of data vintage. A verb form should not decide that; the forward-looking verbs are now recognised in all their forms.

A second contradiction, in `starter-datasets/synthetic-conflict/`, exercises the plain case: two one-page PDFs about a company that does not exist, stating Rs. 1,240 crore and Rs. 1,180 crore of FY24 revenue with no scope, basis, vintage or window between them, while corroborating on EBITDA. They are labelled synthetic in their own README and generated by a committed script. They exist because a contradiction that survives every reconciliation rule is rare in real filings — of 557 comparisons across these eight documents only two are contradictions — and a test that depends on finding one in someone else's PDFs is not a test. `test_case2_a_real_disagreement_is_still_reported_as_a_contradiction` is the guard against the reasoner learning to explain everything away.

**Where the rest of the disagreements are.** 16 cross-document pairs state different figures for the same entity, metric and period; two of them are contradictions and the other fourteen carry a reason. Those fourteen are the interesting ones, and they used to be invisible: the Comparisons view lumped them in with 63 pairs that merely covered different periods and were never in conflict. The verdict filter now separates *Differs, with a reason* from *Different periods*, so the reconciled disagreements can be read with their citations instead of being buried.

**3. Apparent contradictions explained by context.**

- *Scope* — annual report p. 22, "The revenue from operations on standalone basis for FY24 stood at ₹ 74,540.82 million", against the deck's ₹8,142 Cr for FY24. Steps 1–3 pass, step 4 fails on "scope standalone vs unspecified", so the 8.45% gap is explained rather than flagged.
- *Accounting basis* — annual report p. 4 tiles "₹1,266Mn – EBITDA" and "₹758Mn – Adjusted EBITDA", both FY24. Step 4 fails on "basis 'As reported' vs 'Non-GAAP (adjusted)'".
- *Data vintage* — Economic Survey p. 4, "As per the first advance estimates of national accounts, India's real GDP is estimated to grow by 6.4 per cent in FY25", against RBI p. 8, "growth moderated to 6.5 per cent in 2024-25 ... based on the Second Advance Estimates". Same entity, metric and period; one carries an `estimate` qualifier and the other does not.
- *Partial period* — Economic Survey p. 76, "retail inflation moderated from 5.4 per cent in FY24 to 4.9 per cent in FY25 (April-December)", against the IMF's "4.6 percent (FY2024/25 average)" on p. 10. The window is written onto the value's own period, so the fact keeps a `partial_period` qualifier and the 0.3-point gap is coverage, not conflict.
- *An identifier revised over time* — prospectus p. 30, "Corporate Identity Number: U63090DL2011PLC221234", against annual report p. 30, "CIN: L63090DL2011PLC221234". Same entity, same identifier key, 1 character different out of 21, and the documents are two years apart. The engine reports an identifier revision rather than a contradiction. (In reality the prefix changed from U to L when the company listed — the engine cannot know that, and does not claim to; it says only that an identifier of this shape changed between two dated documents.)
- *A status that changed* — prospectus p. 87, "Sandeep Kumar Barasia is an Executive Director and Chief Business Officer of our Company" (as of 2022-05-14), against annual report p. 24, "Mr. Sandeep Kumar Barasia ... resigned from the office of Executive Director & Chief Business Officer, with effect from July 01, 2024". Donald Francis Colleran and Suvir Suren Sujan produce the same pattern.

**4. Extraction and reasoning failures, and what was done about them.**

The Examples tab, under "Extraction was unsure", lists all 249 facts held below the matching threshold with the reason each was held, from live data. Three failures found by reading the output against the PDFs, and what each cost:

- *A reasoning failure: a forecast that did not look like one.* The reconciler excuses a value difference when one figure is a projection and the other a reported actual. `ESTIMATE_WORDS` held "is expected" and "expected to" but not "expects", so "the RBI **expects** headline inflation to be 4.2 per cent in FY26" was treated as a reported figure and its disagreement with the IMF's 2.8 per cent forecast was filed as data vintage — the one genuine contradiction between the starter documents, explained away by a verb form. Fixed by recognising the forward-looking verbs in all their forms; the pair is now a contradiction and a test pins it. This is the failure mode to watch in a system like this: a reconciliation rule that is slightly too easy to satisfy is indistinguishable from correct behaviour until you read the pairs it silenced.
- *A quarter read as a full year.* Economic Survey p. 62 says India's CAD "moderated slightly to 1.2 per cent of GDP in Q2 of FY25 against 1.3 per cent of the GDP recorded in Q2 of FY24". The period parser matched `Q<n> FY<yy>` but not `Q<n> of FY<yy>`, so "Q2 of FY24" normalised to the whole of FY2024 and was compared against the IMF's full-year 0.7 per cent — a 0.6-point "contradiction" that was the first thing shown on the Overview. Fixed in the period patterns; `test_a_quarter_written_with_of_is_not_read_as_the_full_year` pins it.
- *A sentence glued onto a running header.* A chart splits a sentence on RBI p. 40, and reflow joins the tail to the page header: "ECONOMIC REVIEW headline inflation during 2024-25 as compared with 61 per cent a year ago". The fragment parses cleanly and looks like a headline-inflation claim of 61 per cent, which then contradicted the real 5.4 per cent by 55 points. The subject the number belonged to is on the other side of the chart and cannot be recovered, so instead of guessing, a sentence that starts with an all-caps running header now loses 0.3 confidence and is excluded from matching. It is still visible in the fact table with the reason attached. One fact in ~600 is affected across the samples, which is the right blast radius for a rule this blunt.
- *A contents table read as the document's subject.* Both macro excerpts open on a contents page rather than a cover, so the RBI report was attributed to "Food Corporation" (a body it cites four times, deep in the text) and the Economic Survey to the heading "Chapter No. Page No. Name of the Chapter". Neither is document-specific: the fix is that an organisation named in the opening pages outranks a company that never appears there, and a contents-table line is not a candidate title. They now resolve to "Reserve Bank of India" and "Economic Survey".

Failure modes that remain, contained rather than solved:

- *Activity counts read as headcount*: "In FY24, we trained 12,104 employees" matches the `employees` keyword. A weak keyword needs stock-style phrasing ("were 23,381 as on March 31, 2024"); verbs like trained/promoted/receiving after the count drop confidence to 0.55. The true headcount fact stays at 0.9.
- *A subject in the previous block*: the RBI sentence "6.7 per cent in 2024-25, with intermittent spikes ..." is about food inflation, but those words sit in the preceding block, so it reads as headline inflation. The value-before-keyword penalty lowers it to 0.55 and it never matches.
- *Table rows as sentences*: financial-statement rows produce numbers with no unit and dates as column headers. Sentences with seven or more numbers are skipped, and bare numbers are never taken as KPI tiles.
- *Open-schema labels reused for different subjects*: "he received an aggregate compensation of ₹30.31 million" appears once per director — same label, different subjects. Same-document conflicts on generated keys are marked "needs review", not contradiction. 33 pairs sit in that bucket.

### Engineering decisions and trade-offs

| Decision | Why | Cost |
| --- | --- | --- |
| Rules for extraction and for every verdict; the model only groups labels | Deterministic, free, fast (8 PDFs, 513 pages, in 11 s with no key), every verdict explainable line by line, and evaluators can run the whole thing without an account. | Recall is bounded by the patterns; some prose escapes. Confidence scoring makes the misses visible. |
| The LLM step sees labels only, never values or page text | The worst a bad response can do is propose a comparison the reasoner then rejects. A model that saw values could put a wrong figure into the layer. | It cannot help with extraction, which is where most of the remaining loss is. |
| A small taxonomy plus an open-schema path | The taxonomy makes common financial and macro figures comparable across wording; the open path keeps the system usable on documents the taxonomy never saw. | Open-schema keys only match when two documents use the same label, unless the LLM step groups them. |
| Confidence gate rather than deletion | Uncertain facts are the assignment's case 4; hiding them would hide the failure modes. | The Facts table contains noise below 0.6 by design. |
| Bucketed matching and in-memory state with a JSON snapshot | Enough for hundreds of pages and many documents; incremental upload does not rebuild. | Not multi-user; a database would replace `store.json`. |
| Layout-aware parsing | Two-column reports and KPI slides are the norm, not the exception. | Reading order is a heuristic; unusual layouts can still merge or split sentences — see the running-header failure above. |

### AI tools used

The first version of this repository was generated with Google Gemini. A fact check against the assignment PDF and the sample documents showed its four cases were invented (quotes and figures that do not exist in the PDFs) and its extractor mostly returned years and footnote numbers as values. The backend extraction, normalisation and reasoning, the frontend, the tests and this README were then rewritten with Claude Code (Claude Fable 5.1), with every case checked against the actual PDF text.

Gemini is also used at runtime for the one optional step described under [The LLM step](#the-llm-step) — grouping metric labels into families. It is off by default, off whenever no key is present, and no verdict depends on it.

## Limitations and next steps

What does not work yet:

- Contradictions that survive every reconciliation rule are rare in real filings: two in 557 comparisons here. The reconciliation is where the risk lies, so `Differs, with a reason` deserves as much scrutiny as the conflicts bucket.
- Prose whose subject is stated in an earlier sentence or a heading is attributed to the metric keyword found nearby (the food-inflation example above), and a sentence severed by a chart cannot be repaired — only distrusted.
- Financial statement tables are not parsed as tables. A row-and-column table extractor would add a large number of well-formed facts.
- Without the LLM step, open-schema labels match only on identical normalised text: "aggregate compensation" and "total remuneration" stay apart. With it they are grouped, but the grouping is a retrieval hint, not a claim of identity.
- Currency conversion is not attempted, so an INR figure and a USD figure for the same metric never relate.
- Scope (standalone vs consolidated) is read from the sentence only; section headings are not used.
- The entity model is one subject per document (plus people and countries). Subsidiaries mentioned in a parent's report are attributed to the parent.

What I would build next, in order: a table extractor, heading-aware scope and subject tracking, a learned alias map for open-schema keys that persists what the label-grouping step discovers, and a PDF viewer that draws the evidence box from the stored block coordinates.

## Additional notes

- Checked on documents outside the samples: a one-page synthetic company update ("Acme Robotics Inc. ... Total revenue for 2024 was $48.2 million. Headcount stood at 310 employees as of December 31, 2024. Jane Doe resigned as Chief Financial Officer with effect from March 3, 2024 ...") yields revenue, headcount, a board event, the registered office and an open-schema "fleet uptime" fact. The assignment brief itself, uploaded as a PDF, yields one attribute (`attr.the_challenge` = "Build a Fact Knowledge Layer", read from its own heading), no errors and no relationships — it shares no entity with anything else in the layer, which is the correct outcome. Unit-less counts ("12,400 units") and unfamiliar units ("92 kWh") are not picked up yet.
- The sample sets under `starter-datasets/` are just samples. Nothing in the code refers to their filenames, entities or values; any folder of PDFs placed there can be loaded with `POST /api/load-preset?name=<folder>`, and the UI itself only ever sees uploads.
- `GET /api/schema` shows the built-in taxonomy next to every metric key discovered from the loaded documents, so you can see the schema grow as new kinds of facts appear.
- `GET /api/insights` returns the ranked review queue, the document-pair matrix, metric coverage and per-document extraction health. The ranking is deterministic: a disagreement between two documents outranks one inside a single document, scaled by confidence and the size of the gap.
- `GET /api/documents/{id}/pages/{n}` returns the reflowed page text; the UI's evidence drawer highlights the exact span of every quote in it.
- No credentials are committed, and none are needed to run or evaluate the project.
