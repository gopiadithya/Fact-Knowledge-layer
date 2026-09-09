# Sample datasets

Three sets of PDFs used to exercise the layer. They are samples, not the target: the extractor does not know their filenames, entities or figures, and the same code runs on any PDF you upload.

- `delhivery/` – a 2022 prospectus excerpt, the FY24 annual report excerpt and the Q4 FY24 earnings deck of one company. Public documents.
- `india-macroeconomy/` – the Economic Survey 2024-25, the RBI Annual Report 2024-25 and the IMF 2025 Article IV report, three publishers describing the same economy. Public documents.
- `synthetic-conflict/` – **not real**: two one-page PDFs written for this repository, about a company that does not exist, so that the contradiction path has something to demonstrate. The two public sets reconcile completely once period, scope, basis and vintage are accounted for. See that folder's README.

Each folder's README lists provenance and, for the public sets, which pages of the originals were kept.

Any sub-folder placed here with PDFs in it can be loaded with `POST /api/load-preset?name=<folder>` (add `&replace=false` to add it to what is already loaded). The UI itself only takes uploads and drops.
