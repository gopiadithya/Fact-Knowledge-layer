"""
PDF parsing with layout awareness.

Why not `page.get_text()`? Two-column reports (RBI, IMF) interleave columns
line by line, and slide decks put a KPI number in one block and its label in
another. Both break sentence-level extraction. This parser:

1. reads text blocks with their bounding boxes (PyMuPDF),
2. orders them column by column, joining continuation lines,
3. splits the reflowed page into sentences with char offsets, and
4. pairs numeric-only blocks with the adjacent label block ("tiles").

pypdf is a fallback when PyMuPDF is missing; it gives sentences but no tiles.
"""
import os
import re
import uuid
import datetime
from collections import Counter
from typing import Dict, List, Optional, Tuple

from ..models.document import Document, DocumentMetadata, DocumentPage, Sentence, Tile
from .normalizer import find_periods, parse_values, prenormalize

_ABBREV = r"(?:Mr|Mrs|Ms|Dr|Prof|Rs|No|Nos|Ltd|Inc|Pvt|Co|Corp|vs|approx|i\.e|e\.g|Adj|St|Jr|Sr|w\.e\.f|Fig|Vol|pp|cf|et al|[A-Z])"
_SENT_SPLIT = re.compile(r"(?<=[.!?])[\"”’)]?\s+(?=[A-Z₹$€£“\"(\[]|\d)")


def _split_sentences(text: str) -> List[Sentence]:
    """Split reflowed text into sentences; newlines are hard boundaries."""
    out: List[Sentence] = []
    pos = 0
    for para in text.split("\n"):
        if para.strip():
            cursor = 0
            pieces: List[Tuple[int, int]] = []
            for m in _SENT_SPLIT.finditer(para):
                before = para[:m.start()]
                # do not split after an abbreviation or a decimal point
                if re.search(rf"\b{_ABBREV}\.$", before):
                    continue
                pieces.append((cursor, m.start() + 1))
                cursor = m.end()
            pieces.append((cursor, len(para)))
            for a, b in pieces:
                seg = para[a:b]
                lead = len(seg) - len(seg.lstrip())
                seg = seg.strip()
                if len(seg) >= 12:
                    out.append(Sentence(text=seg, start=pos + a + lead, end=pos + a + lead + len(seg)))
        pos += len(para) + 1
    return out


import unicodedata


def _strip_bullet(line: str) -> str:
    """Drop leading bullet glyphs (dingbats, private-use glyphs from symbol fonts, dashes) and a page-number prefix."""
    line = line.strip()
    line = re.sub(r"^\d{1,3}\s+(?=[^\w\s(₹$€£\"'“‘])", "", line)   # "7 <glyph>Mr. ..." page number before a bullet
    line = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", line).strip()   # symbol fonts leak control codes for bullets
    while line and (unicodedata.category(line[0]) in ("So", "Co", "Cn", "Cc") or line[0] in "•▪■●◦►✓✔–—-*>\ufffd"):
        line = line[1:].lstrip()
    return line


# Some filings embed the rupee sign with a font encoding that decodes to a bare "I",
# so the extracted text says "I 650.02 million" where the page shows "₹650.02 million".
# Restore it only when a number and a scale word follow, which is where the confusion is unambiguous.
_RUPEE_GLYPH = re.compile(r"\bI\s(?=[\d,]+(?:\.\d+)?\s*(?:million|billion|crore|lakh|mn|bn|cr)\b)", re.IGNORECASE)


def _join_lines(block_text: str) -> str:
    lines = [_strip_bullet(l) for l in block_text.split("\n")]
    lines = [l for l in lines if l and not (re.fullmatch(r"\d{1,3}", l) and len(lines) > 1)]
    lines = [_RUPEE_GLYPH.sub("₹", l) for l in lines]
    joined = ""
    for line in lines:
        if not joined:
            joined = line
        elif joined.endswith("-") and line[:1].islower():
            joined = joined[:-1] + line          # de-hyphenate "double-\ndeck"
        else:
            joined += " " + line
    return joined


_LABEL_STOP = {"nil", "na", "n/a", "index", "million", "millions", "crore", "crores", "lakh", "lakhs", "total", "current",
               "noncurrent", "non-current", "particulars", "year", "ended", "march", "december", "september", "june",
               "as", "at", "on", "of", "the", "and", "in", "for", "to", "fy", "cy", "note", "notes"}


def _is_label(text: str) -> bool:
    """A tile label is short prose with real words, not a row of numbers or date headers."""
    t = text.replace("\n", " ").strip()
    if len(t) > 140 or len(re.findall(r"\d[\d,]*\.?\d*", t)) > 3:
        return False
    words = [w.lower().strip(".,:;()") for w in re.findall(r"[A-Za-z][A-Za-z\-/.]+", t)]
    words = [w for w in words if w and w not in _LABEL_STOP and w not in ("jan", "feb", "mar", "apr", "may", "jun", "jul",
                                                                          "aug", "sep", "sept", "oct", "nov", "dec")]
    return len(words) >= 1 and any(len(w) >= 4 for w in words)


def _is_value_only(text: str) -> bool:
    """True when a block is just one or two unit-bearing numbers, e.g. '₹8,142 Cr' or '₹127Cr / 1.6%'."""
    t = prenormalize(text.replace("\n", " ").strip())
    if len(t) > 40 or not any(c.isdigit() for c in t):
        return False
    vals = parse_values(t)
    if not vals or len(vals) > 2:
        return False
    if any(v.unit == "count" for v in vals):      # bare numbers are table cells, not KPI tiles
        return False
    if len(vals) == 2 and "/" not in t:
        return False
    rest = t
    for v in sorted(vals, key=lambda v: -v.start):
        rest = rest[:v.start] + rest[v.end:]
    rest = re.sub(r"[\s/|:+~,\-()]+", "", rest).lower()
    return rest in ("", "+", "tons", "tonnes", "yoy", "cr", "mn", "k", "bn", "sqft", "sq.ft.")


def _reflow_page(page) -> Tuple[str, List[Tile], Optional[str]]:
    """Return (reflowed_text, tiles, period_hint) for a PyMuPDF page."""
    width = page.rect.width or 1.0
    raw_blocks = [b for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()]

    blocks = []
    for x0, y0, x1, y1, txt, _no, _typ in raw_blocks:
        wide = (x1 - x0) > 0.6 * width
        col = 0 if wide else (0 if (x0 + x1) / 2 < width / 2 else 1)
        blocks.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1, "text": txt, "col": col, "wide": wide})

    # --- tiles: numeric block + adjacent label block ---------------------------------
    tiles: List[Tile] = []
    used_labels = set()
    for i, b in enumerate(blocks):
        if not _is_value_only(b["text"]):
            continue
        best, best_d = None, 1e9
        for j, c in enumerate(blocks):
            if j == i or j in used_labels or _is_value_only(c["text"]) or not _is_label(c["text"]):
                continue
            h_overlap = c["x0"] < b["x1"] and c["x1"] > b["x0"]
            if not h_overlap or abs(c["x0"] - b["x0"]) > 40:
                continue
            below = c["y0"] - b["y1"]
            above = b["y0"] - c["y1"]
            if -6 <= below <= 28:
                d = below + 0          # prefer labels below
            elif -6 <= above <= 28:
                d = above + 10
            else:
                continue
            if d < best_d:
                best, best_d = j, d
        if best is not None:
            used_labels.add(best)
            tiles.append(Tile(value_text=_join_lines(b["text"]), label_text=_join_lines(blocks[best]["text"]),
                              bbox=[round(b["x0"]), round(b["y0"]), round(b["x1"]), round(b["y1"])]))

    # --- reading order & reflow --------------------------------------------------------
    order = sorted(range(len(blocks)), key=lambda k: (blocks[k]["col"], round(blocks[k]["y0"] / 4), blocks[k]["x0"]))
    parts: List[str] = []
    prev_col = None
    for k in order:
        t = _join_lines(blocks[k]["text"])
        if parts and blocks[k]["col"] == prev_col:
            prev = parts[-1]
            if prev and not re.search(r"[.!?:;]$", prev) and (t[:1].islower() or prev.endswith(",") or prev.endswith("and")):
                parts[-1] = prev + " " + t
                prev_col = blocks[k]["col"]
                continue
        parts.append(t)
        prev_col = blocks[k]["col"]
    text = "\n".join(parts)

    # locate tile values inside the reflowed text (for evidence offsets)
    for tile in tiles:
        idx = text.find(tile.value_text)
        if idx >= 0:
            tile.start, tile.end = idx, idx + len(tile.value_text)

    # --- period hint: a short stand-alone period label such as "FY24" -----------------
    hint = None
    for b in blocks:
        short = b["text"].strip()
        if len(short) <= 14:
            hits = find_periods(short)
            if hits and hits[0][1] - hits[0][0] >= len(short) - 2:
                hint = hits[0][2].canonical
                break
    return text, tiles, hint


def _pdf_info(raw) -> Dict[str, str]:
    """Title and author from the PDF's own metadata, if the file carries any.

    Both parsers expose this differently and either may raise on a damaged file, so every
    failure degrades to "no metadata" rather than losing the document.
    """
    out: Dict[str, str] = {}
    for key in ("title", "author"):
        try:
            value = raw.get(key) if isinstance(raw, dict) else getattr(raw, key, None)
        except Exception:
            value = None
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()
    return out


class PDFParser:
    @staticmethod
    def parse_pdf(file_path: str, original_filename: str) -> Document:
        file_size = os.path.getsize(file_path)
        doc_id = f"doc_{uuid.uuid4().hex[:8]}"
        pages: List[DocumentPage] = []
        parser_used = "pymupdf"

        try:
            import pymupdf  # PyMuPDF >= 1.24
        except ImportError:
            try:
                import fitz as pymupdf  # older PyMuPDF
            except ImportError:
                pymupdf = None

        pdf_info: Dict[str, str] = {}

        if pymupdf is not None:
            pdf = pymupdf.open(file_path)
            pdf_info = _pdf_info(pdf.metadata)
            for idx, page in enumerate(pdf):
                text, tiles, hint = _reflow_page(page)
                pages.append(DocumentPage(
                    page_number=idx + 1, text=text, char_count=len(text), token_estimate=len(text.split()),
                    sentences=_split_sentences(text), tiles=tiles, period_hint=hint,
                ))
        else:
            parser_used = "pypdf"
            import pypdf
            reader = pypdf.PdfReader(file_path)
            pdf_info = _pdf_info(reader.metadata)
            for idx, page in enumerate(reader.pages):
                raw = page.extract_text() or ""
                text = _join_lines(raw)
                pages.append(DocumentPage(page_number=idx + 1, text=text, char_count=len(text),
                                          token_estimate=len(text.split()), sentences=_split_sentences(text)))

        metadata = DocumentMetadata(
            id=doc_id, filename=os.path.basename(file_path), original_name=original_filename,
            page_count=len(pages), file_size_bytes=file_size,
            upload_time=datetime.datetime.utcnow().isoformat(), status="parsed", parser=parser_used,
            pdf_title=pdf_info.get("title"), pdf_author=pdf_info.get("author"),
        )
        return Document(metadata=metadata, pages=pages)
