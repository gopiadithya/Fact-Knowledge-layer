import { Cases, DocumentMetadata, EvaluationMetrics, Fact, Insights, MetricAvailable, PageText, Preset, RelationshipEnriched, SchemaInfo, TimelinePoint } from '../types';

const BASE = (import.meta as any).env?.VITE_API_URL || 'http://127.0.0.1:8000/api';

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, init);
  } catch {
    throw new Error(`The API at ${BASE} is not reachable. Start the backend (see README) and reload.`);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  presets: () => call<Preset[]>('/presets'),
  loadPreset: (name: string) => call<any>(`/load-preset?name=${encodeURIComponent(name)}`, { method: 'POST' }),
  upload: (files: File[], incremental: boolean) => {
    const fd = new FormData();
    files.forEach((f) => fd.append('files', f));
    return call<DocumentMetadata[]>(`/documents/upload?incremental=${incremental}`, { method: 'POST', body: fd });
  },
  documents: () => call<DocumentMetadata[]>('/documents'),
  deleteDocument: (id: string) => call<any>(`/documents/${id}`, { method: 'DELETE' }),
  page: (docId: string, page: number) => call<PageText>(`/documents/${docId}/pages/${page}`),
  facts: () => call<Fact[]>('/facts'),
  relationships: () => call<RelationshipEnriched[]>('/relationships'),
  cases: () => call<Cases>('/cases'),
  insights: () => call<Insights>('/insights'),
  metrics: () => call<EvaluationMetrics>('/evaluation-metrics'),
  metricsAvailable: () => call<MetricAvailable[]>('/metrics-available'),
  timeline: (metric: string) => call<{ metric: string; points: TimelinePoint[] }>(`/timeline?metric=${encodeURIComponent(metric)}`),
  schema: () => call<SchemaInfo>('/schema'),
  clear: () => call<any>('/clear', { method: 'DELETE' }),
};
