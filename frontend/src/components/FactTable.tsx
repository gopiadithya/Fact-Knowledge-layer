import React, { useMemo, useState } from 'react';
import { Fact } from '../types';
import { contextParts, shortDoc } from './Shared';

interface Props { facts: Fact[]; threshold: number; onOpen: (f: Fact) => void; }

export const FactTable: React.FC<Props> = ({ facts, threshold, onOpen }) => {
  const [q, setQ] = useState('');
  const [doc, setDoc] = useState('ALL');
  const [method, setMethod] = useState('ALL');
  const [onlyMatchable, setOnlyMatchable] = useState(false);
  const [limit, setLimit] = useState(60);

  const docs = useMemo(() => Array.from(new Set(facts.map((f) => f.document_name))), [facts]);
  const methods = useMemo(() => Array.from(new Set(facts.map((f) => f.extraction_method))), [facts]);
  const rows = useMemo(() => facts.filter((f) => {
    if (doc !== 'ALL' && f.document_name !== doc) return false;
    if (method !== 'ALL' && f.extraction_method !== method) return false;
    if (onlyMatchable && f.confidence < threshold) return false;
    if (q) {
      const s = q.toLowerCase();
      if (![f.metric, f.metric_canonical, f.entity, f.raw_value, f.source_evidence.exact_quote].join(' ').toLowerCase().includes(s)) return false;
    }
    return true;
  }).sort((a, b) => {
    const ga = a.metric_canonical.startsWith('attr.') ? 1 : 0, gb = b.metric_canonical.startsWith('attr.') ? 1 : 0;
    return ga - gb || b.confidence - a.confidence || a.metric_canonical.localeCompare(b.metric_canonical) || a.period.canonical.localeCompare(b.period.canonical);
  }), [facts, doc, method, onlyMatchable, q, threshold]);

  const exportCsv = () => {
    const head = ['document', 'page', 'entity', 'metric', 'metric_key', 'value', 'normalized_value', 'unit', 'period', 'scope', 'basis', 'confidence', 'method', 'quote'];
    const esc = (v: any) => `"${String(v ?? '').replace(/"/g, '""')}"`;
    const lines = rows.map((f) => [f.document_name, f.source_evidence.page_number, f.entity, f.metric, f.metric_canonical, f.raw_value, f.normalized_numeric_value ?? '', f.normalized_unit, f.period.canonical, f.context.scope, f.context.accounting_standard, f.confidence, f.extraction_method, f.source_evidence.exact_quote].map(esc).join(','));
    const blob = new Blob([[head.join(','), ...lines].join('\n')], { type: 'text/csv' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'facts.csv';
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const methodName: Record<string, string> = { rule_sentence: 'sentence (taxonomy)', rule_open_schema: 'sentence (open schema)', rule_tile: 'KPI tile', rule_governance: 'board event', rule_attribute: 'attribute' };

  return (
    <div>
      <div className="filters">
        <input type="search" placeholder="Search metrics, values, quotes" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={doc} onChange={(e) => setDoc(e.target.value)} aria-label="Document"><option value="ALL">Any document</option>{docs.map((d) => <option key={d} value={d}>{shortDoc(d)}</option>)}</select>
        <select value={method} onChange={(e) => setMethod(e.target.value)} aria-label="How it was found"><option value="ALL">Any method</option>{methods.map((m) => <option key={m} value={m}>{methodName[m] || m}</option>)}</select>
        <label className="toggle"><input type="checkbox" checked={onlyMatchable} onChange={(e) => setOnlyMatchable(e.target.checked)} /> Only facts above the matching threshold ({threshold})</label>
        <button type="button" className="btn" onClick={exportCsv} disabled={!rows.length}>Download CSV</button>
        <span className="note">{rows.length} of {facts.length}</span>
      </div>
      <div className="tablewrap">
        <table className="table">
          <thead><tr><th>Metric</th><th>Entity</th><th className="num">Value</th><th>Period</th><th>Context</th><th>Evidence</th><th className="num">Confidence</th></tr></thead>
          <tbody>
            {rows.slice(0, limit).map((f) => (
              <tr key={f.id} className="click" onClick={() => onOpen(f)} title="Open the source page">
                <td className="metric">{f.metric}<div className="dim">{f.metric_canonical}</div></td>
                <td className="entity">{f.entity}</td>
                <td className={f.numeric_value != null ? 'num val' : 'val'}>{f.raw_value}{f.normalized_numeric_value != null && f.normalized_unit !== 'PERCENT' && <div className="dim">{f.normalized_numeric_value.toLocaleString()} {f.normalized_unit}</div>}</td>
                <td>{f.period.raw || f.period.canonical}</td>
                <td className="dim">{contextParts(f).slice(1).join(', ') || '—'}</td>
                <td className="quote">{f.source_evidence.exact_quote.length > 160 ? f.source_evidence.exact_quote.slice(0, 160) + '…' : f.source_evidence.exact_quote}<div className="dim" style={{ fontFamily: 'var(--sans)' }}>{shortDoc(f.document_name)}, p. {f.source_evidence.page_number}, {methodName[f.extraction_method] || f.extraction_method}</div></td>
                <td className="num" style={{ color: f.confidence < threshold ? 'var(--bad)' : 'inherit' }}>{f.confidence.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > limit && <button type="button" className="btn" style={{ marginTop: 10 }} onClick={() => setLimit(limit + 60)}>Show more</button>}
    </div>
  );
};
