import React, { useRef, useState } from 'react';
import { DocumentMetadata } from '../types';
import { UploadIcon } from './Shared';

interface Props {
  documents: DocumentMetadata[];
  busy: boolean;
  onUpload: (files: File[], incremental: boolean) => void;
  onDelete: (id: string) => void;
  onClear: () => void;
}

/** Add and remove documents without leaving the overview. */
export const DocumentStrip: React.FC<Props> = ({ documents, busy, onUpload, onDelete, onClear }) => {
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const take = (list: FileList | null) => {
    if (!list) return;
    const pdfs = Array.from(list).filter((f) => f.name.toLowerCase().endsWith('.pdf'));
    if (pdfs.length) onUpload(pdfs, documents.length > 0);
  };

  return (
    <section
      className={`strip ${drag ? 'drag' : ''}`}
      onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => { e.preventDefault(); setDrag(false); take(e.dataTransfer.files); }}
    >
      <input ref={input} type="file" accept=".pdf" multiple hidden onChange={(e) => { take(e.target.files); e.target.value = ''; }} />
      {documents.map((d) => (
        <div key={d.id} className="strip-doc" title={d.original_name}>
          <span className="strip-name">{d.original_name.replace(/\.pdf$/i, '').replace(/^\d+-/, '')}</span>
          <span className="strip-meta">{[d.primary_entity, d.document_period || d.document_date, `${d.extracted_facts_count} facts`].filter(Boolean).join(' · ')}</span>
          <button type="button" aria-label={`Remove ${d.original_name}`} disabled={busy} onClick={() => onDelete(d.id)}>×</button>
        </div>
      ))}
      <button type="button" className="strip-add" disabled={busy} onClick={() => input.current?.click()}>
        <span className="plus"><UploadIcon size={18} /></span>
        <span>
          <b>Add PDF</b>
          <i>{drag ? 'Drop to add' : 'or drop files here'}</i>
        </span>
      </button>
      {documents.length > 1 && <button type="button" className="btn quiet strip-clear" disabled={busy} onClick={onClear}>Remove all</button>}
    </section>
  );
};
