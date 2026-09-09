from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
import uuid


class Sentence(BaseModel):
    text: str
    start: int   # char offset into DocumentPage.text
    end: int


class Tile(BaseModel):
    """A KPI tile: a numeric block and the label block sitting next to it (decks, highlight pages)."""
    value_text: str
    label_text: str
    bbox: List[float] = []
    start: int = 0   # offset of value_text in DocumentPage.text
    end: int = 0


class DocumentPage(BaseModel):
    page_number: int
    text: str
    char_count: int = 0
    token_estimate: int = 0
    sentences: List[Sentence] = []
    tiles: List[Tile] = []
    period_hint: Optional[str] = None   # e.g. "FY2024" when a page carries a stand-alone period heading


class DocumentMetadata(BaseModel):
    id: str = Field(default_factory=lambda: f"doc_{uuid.uuid4().hex[:8]}")
    filename: str
    original_name: str
    page_count: int
    file_size_bytes: int
    upload_time: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    status: str = "uploaded"  # "uploaded", "parsed", "extracted", "error"
    error_message: Optional[str] = None
    extracted_facts_count: int = 0
    parser: str = "pymupdf"
    pdf_title: Optional[str] = None           # from the PDF's own metadata, when the file carries any
    pdf_author: Optional[str] = None
    # Resolved from the document itself (never from the filename alone when text is available)
    primary_entity: Optional[str] = None      # company / institution named on the cover
    subject_country: Optional[str] = None     # for macro reports: the economy being described
    document_date: Optional[str] = None       # ISO date the document speaks "as of", if detectable
    document_kind: Optional[str] = None       # prospectus | annual_report | presentation | macro_report | unknown
    document_period: Optional[str] = None     # e.g. "FY2024" for an annual report; used for KPI tiles without a period label


class Document(BaseModel):
    metadata: DocumentMetadata
    pages: List[DocumentPage] = []
