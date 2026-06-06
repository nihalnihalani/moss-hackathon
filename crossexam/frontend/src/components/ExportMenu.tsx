/**
 * ExportMenu — KILLER FEATURE A: the command-bar Export control.
 *
 * A Download-icon button that opens a small menu with two actions:
 *   - "Download Markdown": serializes the MemoModel via memoToMarkdown and
 *     triggers a Blob download (memo.md). Trivial, no deps.
 *   - "Save as PDF": invokes window.print(); the hidden MemoSheet + @media print
 *     CSS isolate the memo node and render it as crisp, selectable Letter-size
 *     text (no html2canvas raster).
 *
 * Keyboard- and screen-reader-friendly: a real <button> toggles an aria-expanded
 * menu; Escape and outside clicks close it; menu items are real buttons.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { Download, FileText, Printer } from 'lucide-react';
import type { MemoModel } from '../lib/memo';
import { memoToMarkdown } from '../lib/memo';
import { useToast } from './ToastContext';

export interface ExportMenuProps {
  /** The built memo model (deterministic, from the session). */
  memo: MemoModel;
}

export function ExportMenu({ memo }: ExportMenuProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const toast = useToast();

  // Close on outside click or Escape.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent): void => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const onDownloadMarkdown = useCallback((): void => {
    setOpen(false);
    try {
      const md = memoToMarkdown(memo);
      const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'crossexam-memo.md';
      document.body.appendChild(a);
      a.click();
      a.remove();
      // Revoke on the next tick so the click has consumed the URL.
      setTimeout(() => URL.revokeObjectURL(url), 0);
      toast.success('Memo downloaded (Markdown).');
    } catch (err) {
      toast.error(`Export failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  }, [memo, toast]);

  const onPrintPdf = useCallback((): void => {
    setOpen(false);
    // The MemoSheet is already in the DOM (print-only). window.print() lets the
    // user "Save as PDF" via the browser print dialog — crisp, selectable text.
    if (typeof window !== 'undefined' && typeof window.print === 'function') {
      window.print();
    }
  }, []);

  return (
    <div className="export-menu" ref={rootRef}>
      <button
        type="button"
        className="btn btn--ghost"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Export memo"
        data-testid="export-button"
      >
        <Download size={13} /> Export
      </button>

      {open ? (
        <div className="export-menu__list" role="menu" aria-label="Export options" data-testid="export-menu">
          <button
            type="button"
            role="menuitem"
            className="export-menu__item"
            onClick={onDownloadMarkdown}
            data-testid="export-markdown"
          >
            <FileText size={14} aria-hidden="true" /> Download Markdown
          </button>
          <button
            type="button"
            role="menuitem"
            className="export-menu__item"
            onClick={onPrintPdf}
            data-testid="export-pdf"
          >
            <Printer size={14} aria-hidden="true" /> Save as PDF
          </button>
        </div>
      ) : null}
    </div>
  );
}
