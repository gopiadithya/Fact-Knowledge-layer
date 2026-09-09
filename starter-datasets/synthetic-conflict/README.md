# Synthetic conflict pair

These two one-page PDFs are **written for this repository**, not sourced from a real company.
Northwind Logistics Limited does not exist.

They exist to keep the plain case under test. The macro set does contain one real contradiction
(the Economic Survey and the IMF publish 4.2 per cent and 2.8 per cent headline inflation for
FY26), but a conflict that survives every reconciliation rule is rare in real filings - two in
557 comparisons across the eight sample documents - and a regression test that depends on
finding one in someone else's PDFs is not a regression test.

This pair disagrees on one figure and agrees on the others, so the path can be demonstrated and
pinned:

| Fact | Investor update | Annual report extract | Verdict |
| --- | --- | --- | --- |
| Revenue from operations, FY24 | Rs. 1,240 crore | Rs. 1,180 crore | contradiction |
| EBITDA, FY24 | Rs. 96 crore | Rs. 96 crore | corroborated |
| Express parcel volume, FY24 | 210 million shipments | 210 million shipments | corroborated |

Neither document carries a scope word, an accounting basis, an estimate qualifier or a partial
period, so there is nothing for the reasoner to reconcile the revenue gap with - which is exactly
what makes it a contradiction rather than an explained difference.

Regenerate with `python starter-datasets/synthetic-conflict/make_pdfs.py`.
