import React, { useRef, useState } from 'react';
import { UploadIcon } from './Shared';

interface Props { busy: boolean; onUpload: (files: File[]) => void; }

/** The first thing a new user sees: one large target that both clicks and accepts a drop. */
export const EmptyDrop: React.FC<Props> = ({ busy, onUpload }) => {
  const [drag, setDrag] = useState(false);
  const [rejected, setRejected] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  const take = (list: FileList | null) => {
    if (!list) return;
    const all = Array.from(list);
    const pdfs = all.filter((f) => f.name.toLowerCase().endsWith('.pdf'));
    setRejected(all.length - pdfs.length);
    if (pdfs.length) onUpload(pdfs);
  };

  return (
    <div
      className={`bigdrop ${drag ? 'drag' : ''}`}
      role="button"
      tabIndex={0}
      aria-label="Add PDFs"
      onClick={() => !busy && input.current?.click()}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.current?.click(); } }}
      onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => { e.preventDefault(); setDrag(false); take(e.dataTransfer.files); }}
    >
      <input ref={input} type="file" accept="application/pdf,.pdf" multiple hidden onChange={(e) => { take(e.target.files); e.target.value = ''; }} />
      <span className="bigdrop-icon"><UploadIcon size={28} /></span>
      <h2>{drag ? 'Drop to add' : busy ? 'Reading…' : 'Add PDFs to begin'}</h2>
      <p>
        Drop files here, or click anywhere in this panel to choose them. Filings, annual reports, investor decks,
        institutional reports. Facts are read with the sentence they came from, then compared across every document you add.
      </p>
      {rejected > 0 && <p className="bigdrop-warn">{rejected} file{rejected > 1 ? 's were' : ' was'} skipped. Only PDFs can be read.</p>}
    </div>
  );
};
