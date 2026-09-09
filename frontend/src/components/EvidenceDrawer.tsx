import React, { useEffect, useRef, useState } from 'react';
import { api } from '../services/api';
import { Fact, PageText } from '../types';

interface Props { fact: Fact | null; onClose: () => void; }

function highlight(text: string, fact: Fact): React.ReactNode {
  const quote = fact.source_evidence.exact_quote;
  const needle = quote.includes(' — ') ? quote.split(' — ')[0] : quote;   // tiles are stored as "value — label"
  let start = fact.source_evidence.char_start ?? -1;
  if (start < 0 || text.slice(start, start + needle.length) !== needle) start = text.indexOf(needle);
  if (start < 0) {
    const firstWords = needle.split(/\s+/).slice(0, 6).join(' ');
    start = text.indexOf(firstWords);
    if (start < 0) return text;
    return <>{text.slice(0, start)}<mark id="ev-mark">{text.slice(start, start + firstWords.length)}</mark>{text.slice(start + firstWords.length)}</>;
  }
  return <>{text.slice(0, start)}<mark id="ev-mark">{text.slice(start, start + needle.length)}</mark>{text.slice(start + needle.length)}</>;
}

export const EvidenceDrawer: React.FC<Props> = ({ fact, onClose }) => {
  const [page, setPage] = useState<PageText | null>(null);
  const [pageNo, setPageNo] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const body = useRef<HTMLDivElement>(null);

  useEffect(() => { if (fact) setPageNo(fact.source_evidence.page_number); }, [fact]);
  useEffect(() => {
    if (!fact) return;
    setError(null);
    api.page(fact.document_id, pageNo).then((p) => {
      setPage(p);
      setTimeout(() => document.getElementById('ev-mark')?.scrollIntoView({ block: 'center' }), 0);
    }).catch((e) => setError(e.message));
  }, [fact, pageNo]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  if (!fact) return null;
  const onSource = pageNo === fact.source_evidence.page_number;
  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label="Source page">
        <header>
          <div>
            <h2>{fact.document_name}</h2>
            <p className="note">{fact.metric}: {fact.raw_value}, {fact.period.raw || fact.period.canonical}. Found by {fact.extraction_method.replace('rule_', '').replace('_', ' ')}.</p>
            {fact.notes && <p className="note">{fact.notes}</p>}
          </div>
          <button type="button" className="close" aria-label="Close" onClick={onClose}>×</button>
        </header>
        <div className="body" ref={body}>
          <div className="pagenav">
            <button type="button" className="btn quiet" disabled={pageNo <= 1} onClick={() => setPageNo(pageNo - 1)}>Previous page</button>
            <span>PDF page {pageNo}{page ? ` of ${page.page_count}` : ''}{onSource ? ' (source of this fact)' : ''}</span>
            <button type="button" className="btn quiet" disabled={!page || pageNo >= page.page_count} onClick={() => setPageNo(pageNo + 1)}>Next page</button>
            {!onSource && <button type="button" className="btn quiet" onClick={() => setPageNo(fact.source_evidence.page_number)}>Back to source</button>}
          </div>
          <p className="note" style={{ marginBottom: 10 }}>Text as read from the PDF (columns reflowed). The highlighted span is the exact evidence for this fact.</p>
          {error && <div className="status error">{error}</div>}
          {page && <div className="pagetext">{onSource ? highlight(page.text, fact) : page.text}</div>}
        </div>
      </aside>
    </>
  );
};
