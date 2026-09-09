import React, { useState } from 'react';
import { DocumentMetadata, Fact, Insights, QueueItem } from '../types';
import { DocumentStrip } from './DocumentStrip';
import { TieOut, Verdict, factorLabel, shortDoc, verdictLabel } from './Shared';

interface Props {
  documents: DocumentMetadata[];
  busy: boolean;
  onUpload: (files: File[], incremental: boolean) => void;
  onDeleteDocument: (id: string) => void;
  onClearDocuments: () => void;
  insights: Insights | null;
  onOpen: (f: Fact) => void;
  onReviewAll: () => void;
  onPickPair: (a: string, b: string) => void;
  onPickMetric: (metric: string) => void;
}

const tone: Record<string, string> = {
  CORROBORATED: 'ok', CONTRADICTION: 'bad', CONTEXTUALLY_EXPLAINED: 'ctx', INSUFFICIENT_EVIDENCE: 'review', NO_MATCH: '',
};

type DocRef = { name: string; entity: string | null; kind: string | null; period: string | null };

const KIND: Record<string, string> = {
  prospectus: 'Prospectus', annual_report: 'Annual report', presentation: 'Investor deck', macro_report: 'Report',
};

/** Name each document by whatever actually tells them apart: the publisher when they differ, else the kind. */
function labeller(docs: DocRef[]): (d: DocRef) => string {
  const entities = new Set(docs.map((d) => d.entity || ''));
  const byEntity = entities.size === docs.length && !entities.has('');
  return (d) => {
    const head = byEntity ? d.entity! : (d.kind && KIND[d.kind]) || shortDoc(d.name);
    return [head, d.period].filter(Boolean).join(' ');
  };
}

/** One sentence that adapts to what is actually there, instead of printing zeroes.
 *
 * A disagreement between two documents and a label reused inside one report are different
 * questions, so they are counted separately rather than added together — most of what needs
 * review is usually the second kind, and calling it all "cross-document" overstates the case. */
function headline(h: Insights['headline']): React.ReactNode {
  if (h.needs_attention === 0) return <>Nothing is in conflict.</>;
  const parts: React.ReactNode[] = [];
  if (h.conflicts > 0) parts.push(<><b>{h.conflicts}</b> {h.conflicts === 1 ? 'conflict' : 'conflicts'}</>);
  if (h.review_cross > 0) parts.push(<><b>{h.review_cross}</b> ambiguous {h.review_cross === 1 ? 'overlap' : 'overlaps'} across documents</>);
  if (h.review_intra > 0) parts.push(<><b>{h.review_intra}</b> repeated {h.review_intra === 1 ? 'label' : 'labels'} within one report</>);
  if (parts.length === 0) return <>Nothing is in conflict.</>;
  return <>{parts.map((p, i) => (
    <React.Fragment key={i}>{i > 0 && (i === parts.length - 1 ? ' and ' : ', ')}{p}</React.Fragment>
  ))} to review.</>;
}

const QueueRow: React.FC<{ item: QueueItem; rank: number; onOpen: (f: Fact) => void }> = ({ item, rank, onOpen }) => {
  const [open, setOpen] = useState(false);
  const factor = item.relationship.reasoning_trace.primary_divergence_factor;
  const reason = factor ? factorLabel[factor] : undefined;
  return (
    <li
      className={`q-row t-${tone[item.verdict]}`}
      aria-expanded={open}
      onClick={() => setOpen(!open)}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOpen(!open); } }}
      tabIndex={0}
      role="button"
    >
      <span className="q-rank">{rank}</span>
      <div className="q-main">
        <div className="q-line">
          <Verdict type={item.verdict} />
          <span className={`q-scope ${item.cross_document ? 'cross' : 'intra'}`}>
            {item.cross_document ? 'Across documents' : 'Within one document'}
          </span>
          <strong>{item.what}</strong>
          {item.also > 0 && <span className="q-more">+{item.also} like it</span>}
        </div>
        <div className="q-values">
          <span>{item.values[0]}</span>
          <span className="q-vs">against</span>
          <span>{item.values[1]}</span>
          {item.delta_percentage != null && item.delta_percentage > 0 && <span className="q-delta">{item.delta_percentage.toFixed(1)}% apart</span>}
        </div>
        <p className="q-where">
          {/* why the engine stopped short of a verdict, in words, not just which documents were involved */}
          {reason && <span className="q-reason">{reason}</span>}
          {item.headline} · {item.documents.map(shortDoc).join(' and ')}
        </p>
      </div>
      <span className="q-open">{open ? 'Hide' : 'Evidence'}</span>
      {open && (
        <div className="q-detail" onClick={(e) => e.stopPropagation()}>
          <TieOut a={item.fact_a} b={item.fact_b} rel={item.relationship} onOpen={onOpen} />
        </div>
      )}
    </li>
  );
};

export const Overview: React.FC<Props> = ({ documents, busy, onUpload, onDeleteDocument, onClearDocuments, insights, onOpen, onReviewAll, onPickPair, onPickMetric }) => {
  if (!insights) return <div className="empty">Load documents to see what needs checking.</div>;
  const h = insights.headline;
  const top = insights.queue[0];
  const docs = insights.matrix.documents;
  const cell = (i: number, j: number) => insights.matrix.cells.find((c) => c.a === Math.min(i, j) && c.b === Math.max(i, j));
  const label = labeller(docs);
  // shade against the busiest pair of *different* documents; a filing compared with itself is a different question
  const busiest = Math.max(1, ...insights.matrix.cells.filter((c) => c.a !== c.b).map((c) => c.total));
  const mostFacts = Math.max(1, ...insights.coverage.map((c) => c.facts));

  return (
    <div className="ov">
      <DocumentStrip documents={documents} busy={busy} onUpload={onUpload} onDelete={onDeleteDocument} onClear={onClearDocuments} />
      <section className="ov-lead">
        <p className="ov-sentence">{headline(h)}</p>
        <p className="ov-support">
          {h.agreements > 0
            ? <>{h.agreements} {h.agreements === 1 ? 'fact' : 'facts'} corroborated by a second source. {h.unconfirmed} that no other document mentions.</>
            : <>Nothing is confirmed by a second source yet. Add another document about the same subject.</>}
        </p>
      </section>

      {top && (
        <section className="ov-block">
          <div className="ov-head"><h2>Start here</h2></div>
          <div className={`ov-top t-${tone[top.verdict]}`}>
            <div className="ov-top-line">
              <Verdict type={top.verdict} />
              <strong>{top.what}</strong>
              <span className="q-dim">{top.headline}</span>
            </div>
            <TieOut a={top.fact_a} b={top.fact_b} rel={top.relationship} onOpen={onOpen} />
          </div>
        </section>
      )}

      <section className="ov-block">
        <div className="ov-head">
          <h2>Needs your attention</h2>
          <p>Between documents first, then within one. Select a row for the evidence.</p>
          <button type="button" className="btn" onClick={onReviewAll}>See all</button>
        </div>
        <ol className="q-list">
          {insights.queue.map((item, i) => <QueueRow key={item.relationship_id} item={item} rank={i + 1} onOpen={onOpen} />)}
        </ol>
        {insights.queue.length === 0 && <div className="empty">Nothing needs a decision.</div>}
      </section>

      <div className="ov-split">
        <div>
        <section className="ov-block">
          <div className="ov-head">
            <h2>How the documents line up</h2>
            <p>Select a pair to read its comparisons.</p>
          </div>
          <div className="mx-wrap">
            <table className="mx">
              <caption className="sr-only">Relationships found between each pair of documents</caption>
              <thead>
                <tr>
                  <td />
                  {docs.map((d, j) => <th key={d.id} scope="col" title={d.name}><span>{label(d)}</span></th>)}
                </tr>
              </thead>
              <tbody>
                {docs.map((d, i) => (
                  <tr key={d.id}>
                    <th scope="row" title={d.name}>{label(d)}</th>
                    {docs.map((e, j) => {
                      const c = cell(i, j);
                      const total = c?.total ?? 0;
                      const self = i === j;
                      const shade = total === 0 ? 0 : 0.05 + 0.57 * Math.min(1, total / busiest);
                      return (
                        <td key={e.id} className={`${total ? 'has' : ''} ${self ? 'self' : ''}`}>
                          <button
                            type="button"
                            disabled={!total}
                            onClick={() => onPickPair(d.id, e.id)}
                            style={{ background: total && !self ? `rgba(58, 70, 64, ${shade.toFixed(3)})` : undefined }}
                            title={c ? `${total} comparisons: ${c.corroborated} agree, ${c.contradiction} conflict, ${c.explained} explained, ${c.review} to review` : 'No comparisons'}
                          >
                            <span className="mx-total">{total || '—'}</span>
                            {!!c?.contradiction && <span className="mx-bad">{c.contradiction} conflict{c.contradiction > 1 ? 's' : ''}</span>}
                            {!c?.contradiction && !!c?.corroborated && <span className="mx-ok">{c.corroborated} agree</span>}
                          </button>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="ov-foot">Outlined cells are a document against itself.</p>
        </section>

        <section className="ov-block">
          <div className="ov-head">
            <h2>Extraction coverage</h2>
          </div>
          <ul className="hl-legend">
            <li><i className="sw ok" />matched to another fact</li>
            <li><i className="sw mid" />usable, nothing to compare with</li>
            <li><i className="sw low" />below the confidence threshold</li>
          </ul>
          <table className="hl">
            <tbody>
              {insights.health.map((r) => {
                const unmatched = r.matchable - r.linked;
                return (
                  <tr key={r.document_id}>
                    <th scope="row">{shortDoc(r.name)}<span className="hl-sub">{r.pages} pages</span></th>
                    <td>
                      <span className="hl-bar" title={`${r.linked} matched, ${unmatched} usable, ${r.low_confidence} low confidence`}>
                        <i className="ok" style={{ width: `${(r.linked / r.facts) * 100}%` }} />
                        <i className="mid" style={{ width: `${(unmatched / r.facts) * 100}%` }} />
                        <i className="low" style={{ width: `${(r.low_confidence / r.facts) * 100}%` }} />
                      </span>
                    </td>
                    <td className="num hl-num">{r.linked} / {r.facts}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
        </div>

        <section className="ov-block">
          <div className="ov-head">
            <h2>What more than one source confirms</h2>
            <p>A figure with a single source cannot be checked.</p>
          </div>
          <table className="cov">
            <thead><tr><th>Metric</th><th className="num">Facts</th><th>Sources</th></tr></thead>
            <tbody>
              {insights.coverage.map((c) => (
                <tr key={c.metric} className="click" onClick={() => onPickMetric(c.metric)}>
                  <td>
                    {c.label}
                    <span className="cov-bar" aria-hidden="true"><i style={{ width: `${Math.max(4, (c.facts / mostFacts) * 100)}%` }} /></span>
                  </td>
                  <td className="num">{c.facts}</td>
                  <td>
                    {c.documents === 1
                      ? <span className="tag warn">one source only</span>
                      : <span className="tag">{c.documents} documents{c.corroborated ? `, ${c.corroborated} agree` : ''}</span>}
                    {!!c.conflicting && <span className="tag bad">{c.conflicting} conflict{c.conflicting > 1 ? 's' : ''}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>

    </div>
  );
};
