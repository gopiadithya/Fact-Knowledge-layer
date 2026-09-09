import React, { useState } from 'react';
import { Cases, Fact, RelationshipEnriched, RelationshipType } from '../types';
import { Evidence, TieOut, Verdict, factorLabel, tone } from './Shared';

interface Props { cases: Cases | null; onOpen: (f: Fact) => void; }

type Kind = 'agree' | 'conflict' | 'explained' | 'unsure';

const KINDS: { id: Kind; title: string; blurb: string; type: RelationshipType }[] = [
  { id: 'agree', title: 'Two sources agree', blurb: 'The same figure in different documents, often in different units or wording.', type: 'CORROBORATED' },
  { id: 'conflict', title: 'Sources disagree', blurb: 'Same entity, metric, period and context, but the statements do not match.', type: 'CONTRADICTION' },
  { id: 'explained', title: 'Difference has a reason', blurb: 'Numbers differ, but period, scope, accounting basis or data vintage accounts for it.', type: 'CONTEXTUALLY_EXPLAINED' },
  { id: 'unsure', title: 'Extraction was unsure', blurb: 'Facts held back from matching, with the reason the confidence dropped.', type: 'INSUFFICIENT_EVIDENCE' },
];

const readKind = (): Kind => {
  const k = new URLSearchParams(window.location.search).get('ex') as Kind;
  return (['agree', 'conflict', 'explained', 'unsure'] as Kind[]).includes(k) ? k : 'agree';
};

export const Examples: React.FC<Props> = ({ cases, onOpen }) => {
  const [kind, setKindState] = useState<Kind>(readKind);
  const setKind = (k: Kind) => {
    setKindState(k);
    const url = new URL(window.location.href);
    url.searchParams.set('ex', k);
    window.history.replaceState(null, '', url.toString());
  };
  if (!cases) return <div className="empty"><h2>Nothing to show yet</h2><p>Add PDFs and the engine will work through them.</p></div>;

  const sets: Record<Kind, RelationshipEnriched[]> = {
    agree: cases.corroborated,
    conflict: cases.contradiction,
    explained: cases.contextually_explained,
    unsure: cases.needs_review,
  };
  const counts: Record<Kind, number> = {
    agree: sets.agree.length, conflict: sets.conflict.length,
    explained: sets.explained.length, unsure: cases.extraction_failures.count,
  };
  const items = sets[kind];

  return (
    <div>
      <div className="ex-pick">
        {KINDS.map((k) => (
          <button key={k.id} type="button" aria-pressed={kind === k.id} className={`ex-card ${tone[k.type]}`} onClick={() => setKind(k.id)}>
            <b>{counts[k.id]}</b>
            <span className="t">{k.title}</span>
            <span className="d">{k.blurb}</span>
          </button>
        ))}
      </div>

      {kind === 'unsure' ? (
        <div>
          <section className="ex-item ex-reasons">
            <div className="top">
              <strong>Why confidence dropped</strong>
              <span className="ex-why">Held back from matching, so a weak reading cannot invent a conflict.</span>
            </div>
            <div className="reasons">
              {cases.extraction_failures.reasons.map((r) => <div key={r.reason} className="reason"><span>{r.reason}</span><b>{r.count}</b></div>)}
            </div>
          </section>
          <h3 style={{ margin: '20px 0 10px' }}>Examples of what was held back</h3>
          {cases.extraction_failures.examples.map((f) => (
            <div key={f.id} className="ex-item">
              <Evidence fact={f} type="INSUFFICIENT_EVIDENCE" onOpen={onOpen} />
              {f.notes && <p className="ex-why" style={{ marginTop: 8 }}>{f.notes}</p>}
            </div>
          ))}
          {cases.needs_review.length > 0 && (
            <>
              <h3 style={{ margin: '18px 0 10px' }}>Overlaps the engine will not call either way</h3>
              {cases.needs_review.map((it) => (
                <div key={it.relationship.id} className="ex-item">
                  <div className="top"><Verdict type={it.relationship.relationship_type} /><strong>{it.fact_a.metric}</strong></div>
                  <TieOut a={it.fact_a} b={it.fact_b} rel={it.relationship} onOpen={onOpen} />
                </div>
              ))}
            </>
          )}
        </div>
      ) : items.length === 0 ? (
        <div className="empty"><h2>No examples of this yet</h2><p>Load a second document about the same entity and comparisons will appear.</p></div>
      ) : (
        items.map((it) => (
          <div key={it.relationship.id} className="ex-item">
            <div className="top">
              <Verdict type={it.relationship.relationship_type} />
              <strong>{it.fact_a.metric}</strong>
              <span className="ex-why">{it.fact_a.entity}</span>
              {it.relationship.reasoning_trace.primary_divergence_factor && (
                <span className="pill">{factorLabel[it.relationship.reasoning_trace.primary_divergence_factor] || it.relationship.reasoning_trace.primary_divergence_factor}</span>
              )}
              <span className="pill">{it.cross_document ? 'two documents' : 'one document'}</span>
            </div>
            <TieOut a={it.fact_a} b={it.fact_b} rel={it.relationship} onOpen={onOpen} />
          </div>
        ))
      )}
    </div>
  );
};
