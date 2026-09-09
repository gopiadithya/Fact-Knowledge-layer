from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from enum import Enum
import uuid

class MetricCategory(str, Enum):
    FINANCIAL_FLOW = "financial_flow"      # Revenue, Net Income, EBITDA over a period
    FINANCIAL_STOCK = "financial_stock"    # Cash, Debt, Assets as of a date
    OPERATIONAL = "operational"            # Headcount, Customers, Locations
    GOVERNANCE = "governance"              # Director status, Auditors, Executive roles
    CORPORATE = "corporate"                # Registered address, CIN, founding date
    GENERAL = "general"                    # Uncategorized semantic facts

class PeriodType(str, Enum):
    FISCAL_YEAR = "fiscal_year"
    QUARTER = "quarter"
    POINT_IN_TIME = "point_in_time"
    DATE_RANGE = "date_range"
    UNKNOWN = "unknown"
    # An identity/attribute fact (an identifier, a registered address, ...) has no reporting
    # period of its own. Use this instead of stamping the document's publication date as if it
    # were the fact's period - that date describes the document, not the value.
    NOT_APPLICABLE = "not_applicable"

class FactPeriod(BaseModel):
    raw: str = ""
    period_type: PeriodType = PeriodType.UNKNOWN
    canonical: str = ""                    # e.g., "2024", "2024-03-31", "2024-Q4"
    start_date: Optional[str] = None       # ISO YYYY-MM-DD
    end_date: Optional[str] = None         # ISO YYYY-MM-DD
    source: str = "quote"   # quote | table_header | section_header | page_context | document_metadata | inferred

class FactContext(BaseModel):
    scope: str = "unspecified"             # consolidated | standalone | segment | unspecified | corporate
    accounting_standard: str = "As reported"  # "As reported" | "Non-GAAP (adjusted)" | "n/a"
    qualifiers: List[str] = []             # e.g. ["ex_stock_compensation", "pro_forma", "continuing_ops"]
    segment_name: Optional[str] = None

class SourceEvidence(BaseModel):
    document_id: str
    document_name: str
    page_number: int
    exact_quote: str
    context_window: str = ""
    char_start: Optional[int] = None
    char_end: Optional[int] = None

class Fact(BaseModel):
    id: str = Field(default_factory=lambda: f"fact_{uuid.uuid4().hex[:8]}")
    document_id: str
    document_name: str
    
    # Entity & Metric
    entity: str                            # e.g. "Acme Corporation Limited"
    entity_canonical: str                  # normalized slug, e.g. "acme_corp"
    entity_source: str = "document"   # document | quote | page_context | inferred
    metric: str                            # e.g. "Revenue from Operations"
    metric_canonical: str                  # normalized e.g. "financial.revenue"
    metric_category: MetricCategory = MetricCategory.FINANCIAL_FLOW
    sub_metric: Optional[str] = None       # e.g. "total", "other_revenue", "segment_ecommerce", "operations", "net_capex"
    # Retrieval-only grouping. A family says two claims are worth comparing;
    # it never says they are the same fact. See metric_canonical for that.
    predicate_family: Optional[str] = None
    predicate_family_confidence: float = 0.0
    
    # Values & Units
    raw_value: str                         # original string, e.g. "₹150.0 Crore"
    numeric_value: Optional[float] = None  # 150.0
    normalized_numeric_value: Optional[float] = None # in base units (e.g. 1500000000.0)
    unit: str = ""                         # "INR Crore"
    normalized_unit: str = ""              # "INR", "USD", "COUNT", "PERCENT", "TEXT"
    
    # Period & Context
    period: FactPeriod = Field(default_factory=FactPeriod)
    context: FactContext = Field(default_factory=FactContext)
    
    # Source & Auditability
    source_evidence: SourceEvidence
    confidence: float = 0.95
    extraction_method: str = "rule_sentence"  # rule_sentence | rule_tile | rule_governance
    notes: Optional[str] = None
