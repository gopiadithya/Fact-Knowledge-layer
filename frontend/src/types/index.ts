export interface DocumentMetadata {
  id: string;
  filename: string;
  original_name: string;
  page_count: number;
  file_size_bytes: number;
  upload_time: string;
  status: string;
  extracted_facts_count: number;
  parser?: string;
  primary_entity?: string | null;
  subject_country?: string | null;
  document_date?: string | null;
  document_kind?: string | null;
  document_period?: string | null;
}

export interface FactPeriod { raw: string; period_type: string; canonical: string; start_date?: string | null; end_date?: string | null; }
export interface FactContext { scope: string; accounting_standard: string; qualifiers: string[]; segment_name?: string | null; }
export interface SourceEvidence { document_id: string; document_name: string; page_number: number; exact_quote: string; context_window: string; char_start?: number | null; char_end?: number | null; }

export interface Fact {
  id: string;
  document_id: string;
  document_name: string;
  entity: string;
  entity_canonical: string;
  metric: string;
  metric_canonical: string;
  metric_category: string;
  raw_value: string;
  numeric_value?: number | null;
  normalized_numeric_value?: number | null;
  unit: string;
  normalized_unit: string;
  period: FactPeriod;
  context: FactContext;
  source_evidence: SourceEvidence;
  confidence: number;
  extraction_method: string;
  notes?: string | null;
}

export interface ReasoningCheck { step_number: number; name: string; passed: boolean; explanation: string; details?: any; }
export interface ReasoningTrace { checks: ReasoningCheck[]; summary: string; primary_divergence_factor?: string | null; confidence: number; }
export type RelationshipType = 'CORROBORATED' | 'CONTRADICTION' | 'CONTEXTUALLY_EXPLAINED' | 'INSUFFICIENT_EVIDENCE' | 'NO_MATCH';

export interface FactRelationship {
  id: string;
  fact_a_id: string;
  fact_b_id: string;
  relationship_type: RelationshipType;
  confidence: number;
  reasoning_trace: ReasoningTrace;
  entity_canonical: string;
  metric_canonical: string;
  delta_percentage?: number | null;
  delta_absolute?: number | null;
  created_at: string;
}

export interface RelationshipEnriched { relationship: FactRelationship; fact_a: Fact; fact_b: Fact; cross_document: boolean; }

export interface EvaluationMetrics {
  total_documents: number;
  total_facts: number;
  matchable_facts: number;
  low_confidence_facts: number;
  facts_with_evidence: number;
  avg_confidence_pct: number;
  relationships: { total: number; cross_document: number; corroborations: number; contradictions: number; contextually_explained: number; needs_review: number; };
  match_threshold: number;
}

export interface Cases {
  corroborated: RelationshipEnriched[];
  contradiction: RelationshipEnriched[];
  contextually_explained: RelationshipEnriched[];
  needs_review: RelationshipEnriched[];
  extraction_failures: { count: number; reasons: { reason: string; count: number }[]; examples: Fact[] };
}

export interface TimelinePoint {
  fact_id: string; period: string; canonical_period: string; value: string; normalized_numeric_value?: number | null; unit: string;
  document_name: string; page_number: number; exact_quote: string; scope: string; accounting_standard: string; confidence: number; entity: string;
}
export interface MetricAvailable { canonical: string; label: string; facts: number; }
export interface SchemaInfo {
  taxonomy: { canonical: string; label: string; category: string; value_kind: string; subject: string; facts: number }[];
  discovered: { canonical: string; facts: number; label: string }[];
  match_threshold: number;
}
export interface PageText { document_id: string; document_name: string; page_number: number; page_count: number; text: string; }
export interface Preset { name: string; files: string[]; }

export interface QueueItem {
  relationship_id: string;
  severity: number;
  headline: string;
  what: string;
  entity: string;
  verdict: RelationshipType;
  cross_document: boolean;
  values: [string, string];
  documents: string[];
  delta_percentage?: number | null;
  summary: string;
  also: number;
  fact_a: Fact;
  fact_b: Fact;
  relationship: FactRelationship;
}

export interface MatrixCell { a: number; b: number; total: number; corroborated: number; contradiction: number; explained: number; review: number; }
export interface CoverageRow { metric: string; label: string; facts: number; documents: number; corroborated: number; conflicting: number; }
export interface HealthRow { document_id: string; name: string; pages: number; facts: number; matchable: number; low_confidence: number; linked: number; }

export interface Insights {
  headline: { needs_attention: number; conflicts: number; review_cross: number; review_intra: number; documents: number; facts: number; matchable: number; unconfirmed: number; agreements: number; agreement_pairs: number; top: string | null };
  queue: QueueItem[];
  matrix: { documents: { id: string; name: string; entity: string | null; kind: string | null; period: string | null }[]; cells: MatrixCell[] };
  coverage: CoverageRow[];
  health: HealthRow[];
}
