import React, { useEffect, useState } from 'react';
import { api } from '../services/api';
import { SchemaInfo } from '../types';

interface Props { version: number; onPickMetric: (metric: string) => void; }

const GROUPS: { id: string; title: string; blurb: string; match: (c: string) => boolean }[] = [
  { id: 'flow', title: 'Money over a period', blurb: 'Figures covering a span of time.', match: (c) => c === 'financial_flow' },
  { id: 'stock', title: 'Money at a date', blurb: 'Balances true as of a date.', match: (c) => c === 'financial_stock' },
  { id: 'ops', title: 'Operations', blurb: 'Volumes, headcount and reach.', match: (c) => c === 'operational' },
  { id: 'macro', title: 'Economy-wide', blurb: 'About a country, not a company.', match: (c) => c === 'general' },
  { id: 'gov', title: 'People and boards', blurb: 'Appointments and departures, dated.', match: (c) => c === 'governance' },
  { id: 'corp', title: 'Identity', blurb: 'Identifiers that should never differ.', match: (c) => c === 'corporate' },
];

export const FactTypes: React.FC<Props> = ({ version, onPickMetric }) => {
  const [schema, setSchema] = useState<SchemaInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api.schema().then(setSchema).catch((e) => setError(e.message)); }, [version]);

  if (error) return <div className="status error">{error}</div>;
  if (!schema) return null;

  const most = Math.max(1, ...schema.taxonomy.map((t) => t.facts), ...schema.discovered.map((d) => d.facts));
  const bar = (n: number) => <span className="ft-bar"><i style={{ width: `${Math.max(2, (n / most) * 100)}%` }} /></span>;

  return (
    <div>
      <div className="ft-grid">
        {GROUPS.map((g) => {
          const rows = schema.taxonomy.filter((t) => g.match(t.category));
          if (!rows.length) return null;
          const found = rows.reduce((n, r) => n + r.facts, 0);
          return (
            <section key={g.id} className="ft-group">
              <h3>{g.title}</h3>
              <p className="sub">{g.blurb} {found ? `${found} found here.` : 'None found in these documents.'}</p>
              {rows.map((r) => (
                <div key={r.canonical} className={`ft-row ${r.facts ? 'click' : ''}`} onClick={() => r.facts && onPickMetric(r.canonical)}>
                  <span className="ft-name">{r.label}<span className="ft-key">{r.canonical}</span></span>
                  <span className={`ft-n ${r.facts ? '' : 'zero'}`}>{r.facts || '—'}</span>
                  {!!r.facts && bar(r.facts)}
                </div>
              ))}
            </section>
          );
        })}

        <section className="ft-group" style={{ gridColumn: '1 / -1' }}>
          <h3>Learned from your documents</h3>
          <p className="sub">
            Read straight from a sentence or a slide tile. {schema.discovered.length ? `${schema.discovered.length} in play.` : 'None yet.'}
          </p>
          <div className="ft-grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: '0 20px' }}>
            {schema.discovered.slice(0, 24).map((d) => (
              <div key={d.canonical} className="ft-row click" onClick={() => onPickMetric(d.canonical)}>
                <span className="ft-name">{d.label}<span className="ft-key">{d.canonical}</span></span>
                <span className="ft-n">{d.facts}</span>
              </div>
            ))}
          </div>
          {schema.discovered.length > 24 && <p className="sub" style={{ marginTop: 10 }}>and {schema.discovered.length - 24} more.</p>}
        </section>
      </div>

      <p className="note" style={{ marginTop: 14 }}>Facts below {schema.match_threshold} confidence are shown but never compared.</p>
    </div>
  );
};
