from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from enum import Enum
import uuid

class RelationshipType(str, Enum):
    CORROBORATED = "CORROBORATED"
    CONTRADICTION = "CONTRADICTION"
    CONTEXTUALLY_EXPLAINED = "CONTEXTUALLY_EXPLAINED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NO_MATCH = "NO_MATCH"

class ReasoningCheck(BaseModel):
    step_number: int
    name: str                       # e.g., "Entity Alignment", "Metric Alignment", "Temporal Alignment", "Scope & Accounting", "Value & Unit Equivalence"
    passed: bool
    explanation: str                # e.g., "Both entities refer to Acme Corp (confidence: 0.98)"
    details: Optional[Dict[str, Any]] = None

class ReasoningTrace(BaseModel):
    checks: List[ReasoningCheck] = []
    summary: str
    primary_divergence_factor: Optional[str] = None # e.g. "DIFFERENT_PERIODS", "GAAP_VS_NON_GAAP", "VALUE_DISCREPANCY"
    confidence: float = 0.95

class FactRelationship(BaseModel):
    id: str = Field(default_factory=lambda: f"rel_{uuid.uuid4().hex[:8]}")
    fact_a_id: str
    fact_b_id: str
    
    relationship_type: RelationshipType
    confidence: float
    
    # Reasoning Trace
    reasoning_trace: ReasoningTrace
    
    # Quick metadata for display
    entity_canonical: str
    metric_canonical: str
    # How the two facts were found comparable. "family" means an LLM grouping
    # brought them together, which is never strong enough to assert a conflict.
    match_basis: str = "exact"   # exact | family | surface
    delta_percentage: Optional[float] = None
    delta_absolute: Optional[float] = None
    
    # Timestamps
    created_at: str = Field(default_factory=lambda: "")
