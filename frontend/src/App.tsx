import React, { useCallback, useEffect, useState } from 'react';
import { api } from './services/api';
import { Cases, DocumentMetadata, EvaluationMetrics, Fact, Insights, RelationshipEnriched } from './types';
import { Overview } from './components/Overview';
import { Examples } from './components/Examples';
import { ExternalFilter, RelationshipList } from './components/RelationshipList';
import { FactTable } from './components/FactTable';
import { Trends } from './components/Trends';
import { FactTypes } from './components/FactTypes';
import { EvidenceDrawer } from './components/EvidenceDrawer';
import { EmptyDrop } from './components/EmptyDrop';

type Tab = 'overview' | 'examples' | 'comparisons' | 'facts' | 'trends' | 'types';

const TABS: Tab[] = ['overview', 'examples', 'comparisons', 'facts', 'trends', 'types'];
const TITLES: Record<Tab, { title: string; sub: string }> = {
  overview: { title: 'Overview', sub: 'What needs checking' },
  examples: { title: 'Examples', sub: 'How each verdict is reached' },
  comparisons: { title: 'Comparisons', sub: 'Every pair the engine evaluated' },
  facts: { title: 'Facts', sub: 'Everything extracted, with its evidence' },
  trends: { title: 'Trends', sub: 'One figure across documents and periods' },
  types: { title: 'Fact types', sub: 'The vocabulary, and what it learned' },
};
const fromHash = (): Tab => {
  const h = window.location.hash.replace('#', '') as Tab;
  return TABS.includes(h) ? h : 'overview';
};

export function App() {
  const [tab, setTabState] = useState<Tab>(fromHash);
  const setTab = (t: Tab) => { setTabState(t); window.history.replaceState(null, '', `#${t}`); };

  const [documents, setDocuments] = useState<DocumentMetadata[]>([]);
  const [facts, setFacts] = useState<Fact[]>([]);
  const [relationships, setRelationships] = useState<RelationshipEnriched[]>([]);
  const [cases, setCases] = useState<Cases | null>(null);
  const [insights, setInsights] = useState<Insights | null>(null);
  const [metrics, setMetrics] = useState<EvaluationMetrics | null>(null);
  const [external, setExternal] = useState<ExternalFilter | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<{ kind: 'ok' | 'error' | 'info'; text: string } | null>(null);
  const [open, setOpen] = useState<Fact | null>(null);
  const [version, setVersion] = useState(0);

  const refresh = useCallback(async () => {
    const [d, f, r, m] = await Promise.all([api.documents(), api.facts(), api.relationships(), api.metrics()]);
    setDocuments(d); setFacts(f); setRelationships(r); setMetrics(m);
    if (d.length) {
      const [c, ins] = await Promise.all([api.cases(), api.insights()]);
      setCases(c); setInsights(ins);
    } else { setCases(null); setInsights(null); }
    setVersion((v) => v + 1);
  }, []);

  useEffect(() => {
    refresh().then(() => {
      const id = new URLSearchParams(window.location.search).get('open');
      if (id) api.facts().then((all) => { const f = all.find((x) => x.id === id); if (f) setOpen(f); });
    }).catch((e) => setStatus({ kind: 'error', text: e.message }));
    const onHash = () => setTabState(fromHash());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, [refresh]);

  const run = async (label: string, fn: () => Promise<any>, done: (r: any) => string) => {
    setBusy(true); setStatus({ kind: 'info', text: label });
    try { const r = await fn(); await refresh(); setStatus({ kind: 'ok', text: done(r) }); }
    catch (e: any) { setStatus({ kind: 'error', text: e.message || 'Something went wrong' }); }
    finally { setBusy(false); }
  };

  const onUpload = (files: File[], incremental: boolean) => run(
    `Reading ${files.length} PDF${files.length > 1 ? 's' : ''}…`,
    () => api.upload(files, incremental),
    (metas: DocumentMetadata[]) => `Added ${metas.map((m) => `${m.original_name} (${m.extracted_facts_count} facts)`).join(', ')}`,
  );
  const onDelete = (id: string) => run('Removing…', () => api.deleteDocument(id), () => 'Removed and comparisons rebuilt');
  const onClear = () => { if (window.confirm('Remove all documents and everything derived from them?')) run('Clearing…', () => api.clear(), () => 'Everything removed'); };

  const go = (f: ExternalFilter | null) => { setExternal(f); setTab('comparisons'); };
  const showPair = (a: string, b: string) => {
    const name = (id: string) => (documents.find((d) => d.id === id)?.original_name || id).replace(/\.pdf$/i, '').replace(/^\d+-/, '').replace(/-/g, ' ');
    go({ kind: 'pair', a, b, label: a === b ? `Inside ${name(a)}` : `${name(a)} against ${name(b)}` });
  };
  const showMetric = (metric: string) => go({ kind: 'metric', metric, label: `Only ${metric}` });

  const nav: { id: Tab; label: string; count?: number; alert?: boolean }[] = [
    { id: 'overview', label: 'Overview', count: insights?.headline.needs_attention, alert: !!insights?.headline.needs_attention },
    { id: 'examples', label: 'Examples' },
    { id: 'comparisons', label: 'Comparisons', count: relationships.length },
    { id: 'facts', label: 'Facts', count: facts.length },
    { id: 'trends', label: 'Trends' },
    { id: 'types', label: 'Fact types' },
  ];

  const empty = documents.length === 0;
  const page = TITLES[tab];

  return (
    <div className="app">
      <aside className="side">
        <div className="side-brand">
          <span className="mark" aria-hidden="true">✓</span>
          <div>
            <h1>Fact Layer</h1>
            <span>Cross-document evidence</span>
          </div>
        </div>
        {!empty && (
          <nav className="nav" aria-label="Sections">
            {nav.map((n) => (
              <button key={n.id} type="button" aria-current={tab === n.id ? 'page' : undefined} onClick={() => setTab(n.id)}>
                {n.label}
                {n.count != null && n.count > 0 && <span className={`n ${n.alert ? 'alert' : ''}`}>{n.count}</span>}
              </button>
            ))}
          </nav>
        )}
      </aside>

      <div className="main">
        <header className="phead">
          <div>
            <h1>{empty ? 'Fact Knowledge Layer' : page.title}</h1>
            <p className="sub">{empty ? 'Grounded facts from PDFs, compared across documents' : page.sub}</p>
          </div>
          {metrics && metrics.total_documents > 0 && (
            <div className="kpis">
              <button type="button" className="kpi" onClick={() => setTab('facts')}>
                <b>{metrics.total_facts}</b><span>facts, {metrics.matchable_facts} matchable</span>
              </button>
              <button type="button" className="kpi" onClick={() => go(null)}>
                <b>{metrics.relationships.cross_document}</b><span>pairs across documents</span>
              </button>
              <button type="button" className="kpi ok" onClick={() => go({ kind: 'verdict', verdict: 'CORROBORATED', scope: 'all', label: 'Corroborated only' })}>
                <b>{metrics.relationships.corroborations}</b><span>corroborated</span>
              </button>
              <button type="button" className="kpi bad" onClick={() => go({ kind: 'verdict', verdict: 'CONTRADICTION', scope: 'all', label: 'Conflicts only' })}>
                <b>{metrics.relationships.contradictions}</b><span>conflicts</span>
              </button>
            </div>
          )}
        </header>

        {status && <div className={`status ${status.kind === 'info' ? '' : status.kind}`} role="status">{status.text}</div>}

        <main className="view">
          {empty ? (
            <EmptyDrop busy={busy} onUpload={(files) => onUpload(files, false)} />
          ) : (
            <>
              {tab === 'overview' && <Overview documents={documents} busy={busy} onUpload={onUpload} onDeleteDocument={onDelete} onClearDocuments={onClear} insights={insights} onOpen={setOpen} onReviewAll={() => go(null)} onPickPair={showPair} onPickMetric={showMetric} />}
              {tab === 'examples' && <Examples cases={cases} onOpen={setOpen} />}
              {tab === 'comparisons' && (relationships.length
                ? <RelationshipList items={relationships} onOpen={setOpen} external={external} onClearExternal={() => setExternal(null)} />
                : <div className="empty"><h2>Nothing to compare yet</h2><p>Add a second document about the same entity.</p></div>)}
              {tab === 'facts' && <FactTable facts={facts} threshold={metrics?.match_threshold ?? 0.6} onOpen={setOpen} />}
              {tab === 'trends' && <Trends facts={facts} onOpen={setOpen} />}
              {tab === 'types' && <FactTypes version={version} onPickMetric={showMetric} />}
            </>
          )}
        </main>
      </div>

      <EvidenceDrawer fact={open} onClose={() => setOpen(null)} />
    </div>
  );
}
