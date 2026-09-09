import React, { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../services/api';
import { Fact, MetricAvailable, TimelinePoint } from '../types';
import { shortDoc } from './Shared';

interface Props { facts: Fact[]; onOpen: (f: Fact) => void; }

/** Categorical slots, in the order that keeps adjacent pairs apart for colour-vision deficiency. */
const SERIES = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#4a3aa7', '#e34948'];

const SYMBOL: Record<string, string> = { INR: '₹', USD: '$', EUR: '€', GBP: '£' };

/** Round axis bounds outward to a readable step so ticks are not arbitrary decimals. */
function niceScale(lo: number, hi: number): { lo: number; hi: number; ticks: number[] } {
  const span = hi - lo || 1;
  const raw = span / 4;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((v) => v >= raw) ?? 10 * mag;
  const start = Math.floor(lo / step) * step;
  const end = Math.ceil(hi / step) * step;
  const ticks: number[] = [];
  for (let v = start; v <= end + step / 2; v += step) ticks.push(+v.toFixed(10));
  return { lo: start, hi: end, ticks };
}

function compact(value: number, unit: string): string {
  if (unit === 'PERCENT') return `${(+value.toFixed(2))}%`;
  const sign = value < 0 ? '-' : '';
  const n = Math.abs(value);
  const sym = SYMBOL[unit] || '';
  const suffix = unit === 'TONNES' ? ' t' : '';
  const scale: [number, string][] = [[1e12, 'tn'], [1e9, 'bn'], [1e6, 'm'], [1e3, 'k']];
  for (const [size, tag] of scale) {
    if (n >= size) return `${sign}${sym}${+(n / size).toFixed(n / size < 10 ? 1 : 0)}${tag}${suffix}`;
  }
  return `${sign}${sym}${+n.toFixed(2)}${suffix}`;
}

export const Trends: React.FC<Props> = ({ facts, onOpen }) => {
  const [metrics, setMetrics] = useState<MetricAvailable[]>([]);
  const [metric, setMetric] = useState('');
  const [points, setPoints] = useState<TimelinePoint[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [tip, setTip] = useState<{ x: number; y: number; p: TimelinePoint } | null>(null);
  const wrap = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.metricsAvailable()
      .then((m) => { setMetrics(m); setMetric((cur) => (m.find((x) => x.canonical === cur) ? cur : m[0]?.canonical ?? '')); })
      .catch((e) => setError(e.message));
  }, [facts.length]);

  useEffect(() => {
    if (!metric) { setPoints([]); return; }
    api.timeline(metric).then((t) => setPoints(t.points)).catch((e) => setError(e.message));
  }, [metric, facts.length]);

  const chart = useMemo(() => {
    const usable = points.filter((p) => p.normalized_numeric_value != null);
    if (usable.length < 1) return null;
    const periods = Array.from(new Set(usable.map((p) => p.canonical_period)));
    const docs = Array.from(new Set(usable.map((p) => p.document_name)));
    const values = usable.map((p) => p.normalized_numeric_value as number);
    let lo = Math.min(...values), hi = Math.max(...values);
    if (lo > 0) lo = 0;
    if (hi < 0) hi = 0;
    if (lo === hi) { lo -= 1; hi += 1; }
    const scale = niceScale(lo, hi);
    return { usable, periods, docs, lo: scale.lo, hi: scale.hi, ticks: scale.ticks, unit: usable[0].unit };
  }, [points]);

  const label = metrics.find((m) => m.canonical === metric)?.label ?? metric;

  if (!facts.length) return <div className="empty"><h2>No facts yet</h2><p>Add PDFs to follow a figure across documents and periods.</p></div>;

  const W = 860, H = 320, L = 74, R = 40, T = 14, B = 74;
  const px = (i: number) => (chart && chart.periods.length > 1 ? L + (i * (W - L - R)) / (chart.periods.length - 1) : (L + W - R) / 2);
  const py = (v: number) => (chart ? T + (H - T - B) * (1 - (v - chart.lo) / (chart.hi - chart.lo)) : 0);
  const ticks = chart?.ticks ?? [];

  return (
    <div>
      <div className="tr-top">
        <select value={metric} onChange={(e) => setMetric(e.target.value)} aria-label="Figure to track" style={{ padding: '6px 10px', border: '1px solid var(--rule-strong)', borderRadius: 7, background: 'var(--sheet)', fontSize: 14 }}>
          {metrics.map((m) => <option key={m.canonical} value={m.canonical}>{m.label} ({m.facts})</option>)}
        </select>
        <span className="note">{points.length} dated values · select a dot for its source</span>
      </div>

      {error && <div className="status error">{error}</div>}

      {chart && (
        <div className="tr-card" ref={wrap}>
          <div className="tr-title">{label}</div>
          <div className="tr-sub">Values in {chart.unit === 'PERCENT' ? 'per cent' : chart.unit.toLowerCase()}, oldest period first.</div>
          <svg className="tr-svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${label} by period and source`}>
            {ticks.map((t, i) => (
              <g key={i}>
                <line className="grid" x1={L} x2={W - R} y1={py(t)} y2={py(t)} />
                <text className="axis" x={L - 8} y={py(t) + 4} textAnchor="end">{compact(t, chart.unit)}</text>
              </g>
            ))}
            {chart.lo < 0 && chart.hi > 0 && <line x1={L} x2={W - R} y1={py(0)} y2={py(0)} stroke="var(--rule-strong)" strokeWidth={1} />}
            {chart.periods.map((p, i) => {
              const short = p.replace('9M-ended-', '9M to ').replace('6M-ended-', '6M to ').replace('3M-ended-', '3M to ');
              return (
                <text key={p} className="axis" x={px(i)} y={H - B + 16} textAnchor="end" transform={`rotate(-38 ${px(i)} ${H - B + 16})`}>{short}</text>
              );
            })}
            {chart.docs.map((doc, di) => {
              const mine = chart.usable.filter((p) => p.document_name === doc);
              const single = new Set(mine.map((p) => p.canonical_period)).size === mine.length;
              const path = single
                ? mine.map((p, k) => `${k ? 'L' : 'M'}${px(chart.periods.indexOf(p.canonical_period))},${py(p.normalized_numeric_value as number)}`).join(' ')
                : '';
              return (
                <g key={doc}>
                  {path && mine.length > 1 && <path d={path} fill="none" stroke={SERIES[di % SERIES.length]} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" opacity={0.75} />}
                  {mine.map((p) => (
                    <circle
                      key={p.fact_id}
                      cx={px(chart.periods.indexOf(p.canonical_period))}
                      cy={py(p.normalized_numeric_value as number)}
                      r={5.5}
                      fill={SERIES[di % SERIES.length]}
                      stroke="var(--sheet)"
                      strokeWidth={2}
                      tabIndex={0}
                      role="button"
                      aria-label={`${p.value} in ${p.period}, ${shortDoc(p.document_name)}, page ${p.page_number}`}
                      style={{ cursor: 'pointer' }}
                      onMouseEnter={(e) => setTip({ x: e.clientX, y: e.clientY, p })}
                      onMouseMove={(e) => setTip({ x: e.clientX, y: e.clientY, p })}
                      onMouseLeave={() => setTip(null)}
                      onFocus={(e) => { const r = (e.target as SVGCircleElement).getBoundingClientRect(); setTip({ x: r.x, y: r.y, p }); }}
                      onBlur={() => setTip(null)}
                      onClick={() => { const f = facts.find((x) => x.id === p.fact_id); if (f) onOpen(f); }}
                    />
                  ))}
                </g>
              );
            })}
          </svg>
          <ul className="tr-legend">
            {chart.docs.map((d, i) => <li key={d}><i className="dot" style={{ background: SERIES[i % SERIES.length] }} />{shortDoc(d)}</li>)}
          </ul>
        </div>
      )}

      {tip && (
        <div className="tr-tip" style={{ left: Math.min(tip.x + 14, window.innerWidth - 300), top: tip.y + 14 }}>
          <b>{tip.p.value}</b>
          <span>{tip.p.period} · {shortDoc(tip.p.document_name)}, page {tip.p.page_number}</span>
          {(tip.p.scope !== 'unspecified' || tip.p.accounting_standard !== 'As reported') && (
            <span>{[tip.p.scope !== 'unspecified' ? tip.p.scope : '', tip.p.accounting_standard !== 'As reported' ? tip.p.accounting_standard : ''].filter(Boolean).join(', ')}</span>
          )}
        </div>
      )}

      <div className="tablewrap">
        <table className="table">
          <thead><tr><th>Period</th><th>Entity</th><th className="num">Value</th><th>Context</th><th>Source</th><th className="num">Confidence</th></tr></thead>
          <tbody>
            {points.map((p) => {
              const f = facts.find((x) => x.id === p.fact_id);
              return (
                <tr key={p.fact_id} className="click" onClick={() => f && onOpen(f)}>
                  <td>{p.period}<div className="dim">{p.canonical_period}</div></td>
                  <td>{p.entity}</td>
                  <td className="num val">{p.value}</td>
                  <td className="dim">{[p.scope !== 'unspecified' ? p.scope : '', p.accounting_standard !== 'As reported' ? p.accounting_standard : ''].filter(Boolean).join(', ') || '—'}</td>
                  <td className="quote">{p.exact_quote.length > 130 ? `${p.exact_quote.slice(0, 130)}…` : p.exact_quote}<div className="dim" style={{ fontFamily: 'var(--sans)' }}>{shortDoc(p.document_name)}, p. {p.page_number}</div></td>
                  <td className="num">{p.confidence.toFixed(2)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};
