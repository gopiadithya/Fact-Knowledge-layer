"""Write the two-document pair used to demonstrate a genuine contradiction.

The starter documents reconcile: once the engine accounts for period, scope, accounting basis
and data vintage, nothing is left over that it can honestly call a conflict. That is a result,
not a gap - but it leaves the contradiction path undemonstrated. These two short documents,
about a company that does not exist, disagree on one figure and agree on another, so the path
can be shown end to end and pinned by a test.

Run:  python starter-datasets/synthetic-conflict/make_pdfs.py
"""
import os

import pymupdf

HERE = os.path.dirname(os.path.abspath(__file__))

INVESTOR_UPDATE = """Northwind Logistics Limited
Investor Update

Northwind Logistics Limited operates a parcel and freight network across twelve states.

Revenue from operations was Rs. 1,240 crore for FY24.

EBITDA was Rs. 96 crore for FY24.

Express parcel volume was 210 million shipments for FY24.

The registered office of Northwind Logistics Limited is 14 Harbour Road, Pune 411001.
"""

ANNUAL_EXTRACT = """Northwind Logistics Limited
Extract from the Annual Report

Northwind Logistics Limited is a logistics company incorporated in Maharashtra.

Revenue from operations was Rs. 1,180 crore for FY24.

EBITDA was Rs. 96 crore for FY24.

Express parcel volume was 210 million shipments for FY24.

The registered office of Northwind Logistics Limited is 14, Harbour Road, Pune - 411 001.
"""


def write(name: str, body: str) -> str:
    doc = pymupdf.open()
    page = doc.new_page()
    y = 90
    for line in body.strip().split("\n"):
        if line.strip():
            page.insert_text((72, y), line.strip(), fontsize=12, fontname="helv")
        y += 24
    path = os.path.join(HERE, name)
    doc.save(path)
    doc.close()
    return path


if __name__ == "__main__":
    for path in (write("01-northwind-investor-update.pdf", INVESTOR_UPDATE),
                 write("02-northwind-annual-report-extract.pdf", ANNUAL_EXTRACT)):
        print("wrote", path)
