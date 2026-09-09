import React, { useEffect, useMemo, useState } from 'react';
import { Fact, RelationshipEnriched, RelationshipType } from '../types';
import { TieOut, Verdict, factorLabel, isDisagreement, verdictLabel } from './Shared';

export type ExternalFilter =
  | { kind: 'pair'; a: string; b: string; label: string }
  | { kind: 'metric'; metric: string; label: string }
  | { kind: 'verdict'; verdict: RelationshipType; scope: 'all' | 'cross'; label: string };

interface Props { items: RelationshipEnriched[]; onOpen: (f: Fact) => void; external?: ExternalFilter | null; onClearExternal?: () => void; }

/** The verdict chips. "Explained by context" is split in two, because a pair whose figures differ
 *  for the same period is a disagreement someone should read - with the reason attached - while a
 *  pair about two different periods was never a disagreement at all, and there are far more of
 *  those. Folding both into one bucket hides every reconciled conflict behind the noise. */
type FilterKey = RelationshipType | 'ALL' | 'RECONCILED' | 'NOT_COMPARABLE';

const CHIPS: { key: FilterKey; label: string }[] = [
  { key: 'CORROBORATED', label: verdictLabel.CORROBORATED },
  { key: 'CONTRADICTION', label: verdictLabel.CONTRADICTION },
  { key: 'RECONCILED', label: 'Differs, with a reason' },
  { key: 'NOT_COMPARABLE', label: 'Different periods' },
  { key: 'INSUFFICIENT_EVIDENCE', label: verdictLabel.INSUFFICIENT_EVIDENCE },
];

const matches = (key: FilterKey, i: RelationshipEnriched): boolean => {
  const r = i.relationship;
  if (key === 'ALL') return true;
  if (key === 'RECONCILED') return r.relationship_type === 'CONTEXTUALLY_EXPLAINED' && isDisagreement(r);
  if (key === 'NOT_COMPARABLE') return r.relationship_type === 'CONTEXTUALLY_EXPLAINED' && !isDisagreement(r);
  return r.relationship_type === key;
};

export const RelationshipList: React.FC<Props> = ({ items, onOpen, external, onClearExternal }) => {
  const [type, setType] = useState<FilterKey>('ALL');
  const [scope, setScope] = useState<'all' | 'cross' | 'intra'>('cross');
  useEffect(() => {
    if (!external) return;
    setMetric('ALL'); setQ('');
    if (external.kind === 'verdict') { setType(external.verdict); setScope(external.scope); }
    else { setType('ALL'); setScope('all'); }
  }, [external]);
  const [metric, setMetric] = useState('ALL');
  const [q, setQ] = useState('');
  const [open, setOpen] = useState<string | null>(null);
  const [limit, setLimit] = useState(40);

  const metrics = useMemo(() => Array.from(new Set(items.map((i) => i.relationship.metric_canonical))).sort(), [items]);
  const filtered = useMemo(() => items.filter((i) => {
    if (external?.kind === 'pair') {
      const docs = [i.fact_a.document_id, i.fact_b.document_id].sort().join('|');
      if (docs !== [external.a, external.b].sort().join('|')) return false;
    }
    if (external?.kind === 'metric' && i.relationship.metric_canonical !== external.metric) return false;
    if (!matches(type, i)) return false;
    if (scope === 'cross' && !i.cross_document) return false;
    if (scope === 'intra' && i.cross_document) return false;
    if (metric !== 'ALL' && i.relationship.metric_canonical !== metric) return false;
    if (q) {
      const s = q.toLowerCase();
      const hay = [i.fact_a.raw_value, i.fact_b.raw_value, i.fact_a.entity, i.relationship.metric_canonical, i.fact_a.source_evidence.exact_quote, i.fact_b.source_evidence.exact_quote].join(' ').toLowerCase();
      if (!hay.includes(s)) return false;
    }
    return true;
  }), [items, type, scope, metric, q, external]);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    items.forEach((i) => {
      if (scope === 'cross' && !i.cross_document) return;
      if (scope === 'intra' && i.cross_document) return;
      CHIPS.forEach(({ key }) => { if (matches(key, i)) c[key] = (c[key] || 0) + 1; });
    });
    return c;
  }, [items, scope]);

  return (
    <div>
      {external && (
        <div className="chip-row">
          <span className="chip">{external.label}<button type="button" aria-label="Clear this filter" onClick={onClearExternal}>×</button></span>
          <span className="note">{filtered.length} of {items.length} comparisons</span>
        </div>
      )}
      <div className="filters">
        <div className="seg" role="group" aria-label="Scope">
          <button type="button" aria-pressed={scope === 'cross'} onClick={() => setScope('cross')}>Between documents</button>
          <button type="button" aria-pressed={scope === 'intra'} onClick={() => setScope('intra')}>Within a document</button>
          <button type="button" aria-pressed={scope === 'all'} onClick={() => setScope('all')}>All</button>
        </div>
        <div className="seg" role="group" aria-label="Verdict">
          <button type="button" aria-pressed={type === 'ALL'} onClick={() => setType('ALL')}>Any verdict</button>
          {CHIPS.map(({ key, label }) => (
            <button key={key} type="button" aria-pressed={type === key} onClick={() => setType(key)}>{label} {counts[key] ? `(${counts[key]})` : ''}</button>
          ))}
        </div>
        <select value={metric} onChange={(e) => setMetric(e.target.value)} aria-label="Metric">
          <option value="ALL">Any metric</option>
          {metrics.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
        <input type="search" placeholder="Search values, quotes, entities" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      {filtered.length === 0 ? (
        <div className="empty">
          {type === 'CONTRADICTION' && counts.RECONCILED
            ? `No unexplained conflict here. ${counts.RECONCILED} pair${counts.RECONCILED > 1 ? 's' : ''} did disagree for the same period - open "Differs, with a reason" to read the figures and what reconciles them.`
            : `No relationships match these filters.${scope === 'cross' && items.length > 0 ? ' Try "Within a document" or load a second document about the same entity.' : ''}`}
        </div>
      ) : filtered.slice(0, limit).map((i) => {
        const r = i.relationship;
        const isOpen = open === r.id;
        return (
          <article key={r.id} className="rel">
            <div className="head">
              <Verdict type={r.relationship_type} />
              <span className="metric">{i.fact_a.metric}</span>
              <span className="dim">{i.fact_a.entity}</span>
              {r.reasoning_trace.primary_divergence_factor && <span className="pill">{factorLabel[r.reasoning_trace.primary_divergence_factor] || r.reasoning_trace.primary_divergence_factor}</span>}
              <span className="pill">{i.cross_document ? 'two documents' : 'same document'}</span>
              <span className="dim">confidence {r.confidence.toFixed(2)}</span>
              <button type="button" className="open" onClick={() => setOpen(isOpen ? null : r.id)}>{isOpen ? 'Hide the reasoning' : 'Show the reasoning'}</button>
            </div>
            <TieOut a={i.fact_a} b={i.fact_b} rel={r} onOpen={onOpen} compact={!isOpen} showSummary={isOpen} />
          </article>
        );
      })}
      {filtered.length > limit && (
        <button type="button" className="btn" onClick={() => setLimit(limit + 40)}>Show {Math.min(40, filtered.length - limit)} more of {filtered.length - limit} remaining</button>
      )}
    </div>
  );
};
