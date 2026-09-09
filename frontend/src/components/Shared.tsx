import React from 'react';
import { Fact, FactRelationship, RelationshipType } from '../types';

export const verdictLabel: Record<RelationshipType, string> = {
  CORROBORATED: 'Corroborated',
  CONTRADICTION: 'Contradiction',
  CONTEXTUALLY_EXPLAINED: 'Explained by context',
  INSUFFICIENT_EVIDENCE: 'Needs review',
  NO_MATCH: 'No match',
};

export const factorLabel: Record<string, string> = {
  DIFFERENT_PERIODS: 'different periods',
  SCOPE_OR_BASIS: 'scope or accounting basis',
  DATA_VINTAGE: 'estimate against a reported figure',
  PARTIAL_PERIOD: 'partial period',
  STATUS_CHANGED_OVER_TIME: 'status changed over time',
  VALUE_CONFLICT: 'values differ',
  COMPETING_ESTIMATES: 'competing estimates',
  ATTRIBUTE_CONFLICT: 'attribute differs',
  STATUS_CONFLICT: 'status differs',
  PARTIAL_TEXT_MATCH: 'partial text match',
  SAME_LABEL_DIFFERENT_SUBJECTS: 'same label, probably different subjects',
  IDENTIFIER_REVISED: 'identifier revised',
  EXCLUSIONS_DIFFER: 'different exclusions',
  DIFFERENT_DEFINITION: 'defined differently',
  FAMILY_MATCH_ONLY: 'related labels, not the same measurement',
};

/** A pair whose figures actually disagree: same period, different values, whether or not the
 *  engine found a reason. Pairs about different periods were never in conflict to begin with. */
export const isDisagreement = (r: { relationship_type: RelationshipType; reasoning_trace: { primary_divergence_factor?: string | null } }): boolean =>
  r.relationship_type === 'CONTRADICTION'
  || (r.relationship_type === 'CONTEXTUALLY_EXPLAINED' && r.reasoning_trace.primary_divergence_factor !== 'DIFFERENT_PERIODS');

export const tone: Record<RelationshipType, string> = {
  CORROBORATED: 'ok', CONTRADICTION: 'bad', CONTEXTUALLY_EXPLAINED: 'ctx', INSUFFICIENT_EVIDENCE: 'review', NO_MATCH: '',
};

export const Verdict: React.FC<{ type: RelationshipType }> = ({ type }) => (
  <span className={`verdict v-${type}`}>{verdictLabel[type]}</span>
);

export function shortDoc(name: string): string {
  return name.replace(/\.pdf$/i, '').replace(/^\d+-/, '').replace(/-/g, ' ');
}

export function contextParts(f: Fact): string[] {
  const parts: string[] = [];
  if (f.period.raw || f.period.canonical !== 'unknown') parts.push(f.period.raw || f.period.canonical);
  if (f.context.scope && !['unspecified', 'corporate'].includes(f.context.scope)) parts.push(f.context.scope);
  if (f.context.accounting_standard && !['As reported', 'n/a'].includes(f.context.accounting_standard)) parts.push(f.context.accounting_standard);
  f.context.qualifiers.filter((q) => ['estimate', 'partial_period', 'pro_forma'].includes(q)).forEach((q) => parts.push(q.replace('_', ' ')));
  return parts;
}

export const Evidence: React.FC<{ fact: Fact; type?: RelationshipType; onOpen: (f: Fact) => void; compact?: boolean }> = ({ fact, type, onOpen, compact }) => {
  const parts = contextParts(fact);
  if (fact.confidence < 0.6) parts.push(`confidence ${fact.confidence.toFixed(2)}`);
  return (
    <div className={`evidence ${type ? tone[type] : ''}`}>
      <div className="src">
        <span className="doc" title={fact.document_name}>{shortDoc(fact.document_name)}</span>
        <span className="page">p. {fact.source_evidence.page_number} <button type="button" onClick={() => onOpen(fact)}>Open page</button></span>
      </div>
      <div className={`val ${fact.normalized_unit === 'TEXT' && fact.raw_value.length > 20 ? 'text' : ''}`}>
        {fact.raw_value}
        {fact.numeric_value != null && fact.numeric_value < 0 && !/[-(]/.test(fact.raw_value) && <small>a loss, read as negative</small>}
        {!compact && <small>{fact.metric}{fact.entity ? `, ${fact.entity}` : ''}</small>}
      </div>
      <q>{fact.source_evidence.exact_quote}</q>
      {parts.length > 0 && <div className="ctxline">{parts.map((p, i) => <span key={i}>{p}</span>)}</div>}
    </div>
  );
};

function shortCheck(c: { name: string; passed: boolean; explanation: string; details?: any }, rel: FactRelationship, unit: string): string {
  const d = c.details || {};
  switch (c.name) {
    case 'Entity': return c.passed ? 'same' : c.explanation;
    case 'Metric': return c.passed ? rel.metric_canonical.replace(/^(attr|metric|ops|financial|macro|governance)\./, '') : c.explanation;
    case 'Period': return d.a && d.b ? (d.a === d.b ? d.a : `${d.a} vs ${d.b}`) : c.explanation;
    case 'Context': return c.passed ? 'same scope and basis' : c.explanation;
    case 'Text': return c.passed ? 'same wording' : (d.similarity != null ? `different wording, ${Math.round(d.similarity * 100)}% of words shared` : 'different wording');
    case 'Status': return c.passed ? 'same status' : 'status differs';
    case 'Value':
      if (d.delta_pct == null) return c.explanation;
      return unit === 'PERCENT' ? `${Number(d.delta_abs).toFixed(2)} pp apart` : `${Number(d.delta_pct).toFixed(2)}% apart`;
    default: return c.explanation;
  }
}

export const Spine: React.FC<{ rel: FactRelationship; compact?: boolean; unit?: string }> = ({ rel, compact, unit = '' }) => (
  <div className={`spine ${compact ? 'compact' : ''}`} aria-label="Checks">
    {rel.reasoning_trace.checks.map((c) => (
      <div key={c.step_number} className={`check ${c.passed ? 'pass' : 'fail'}`} title={c.explanation}>
        <span className="glyph" aria-label={c.passed ? 'same' : 'differs'}>{c.passed ? '✓' : '≠'}</span>
        <span className="name">{c.name}</span>
        <span className="exp">{shortCheck(c, rel, unit)}</span>
      </div>
    ))}
  </div>
);

/** Two pieces of evidence tied together by the five checks between them. */
export const TieOut: React.FC<{ a: Fact; b: Fact; rel: FactRelationship; onOpen: (f: Fact) => void; compact?: boolean; showSummary?: boolean }> = ({ a, b, rel, onOpen, compact, showSummary = true }) => (
  <div className="tieout">
    <Evidence fact={a} type={rel.relationship_type} onOpen={onOpen} compact={compact} />
    <Spine rel={rel} compact={compact} unit={a.normalized_unit} />
    <Evidence fact={b} type={rel.relationship_type} onOpen={onOpen} compact={compact} />
    {showSummary && <p className="summary-line">{rel.reasoning_trace.summary}</p>}
  </div>
);

/** Arrow rising out of a tray. Stroked so it sits at the same weight as the type around it. */
export const UploadIcon: React.FC<{ size?: number }> = ({ size = 26 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
       strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    <path d="M12 15.5V4.2" />
    <path d="M7.6 8.6 12 4.2l4.4 4.4" />
    <path d="M4 14.6v2.2A3.2 3.2 0 0 0 7.2 20h9.6a3.2 3.2 0 0 0 3.2-3.2v-2.2" />
  </svg>
);

export function formatBytes(n: number): string {
  if (n > 1e6) return `${(n / 1e6).toFixed(1)} MB`;
  if (n > 1e3) return `${Math.round(n / 1e3)} KB`;
  return `${n} B`;
}
