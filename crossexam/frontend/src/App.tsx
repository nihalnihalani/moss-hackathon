import { useCallback, useState, useEffect, useMemo, useRef } from 'react';
import { useCrossExam } from './hooks/useCrossExam';
import { useBackendSession } from './hooks/useBackendSession';
import { Atmosphere } from './components/Atmosphere';
import { VoiceOrb } from './components/VoiceOrb';
import { StatePill } from './components/StatePill';
import { Captions } from './components/Captions';
import { PdfCanvas } from './components/PdfCanvas';
import { LatencyChip } from './components/LatencyChip';
import { LiveLatencyBadge } from './components/LiveLatencyBadge';
import { PageJump } from './components/PageJump';
import { DocumentUpload } from './components/DocumentUpload';
import { DocSwitcher } from './components/DocSwitcher';
import type { DocSwitcherDoc } from './components/DocSwitcher';
import { ContradictionBanner } from './components/ContradictionBanner';
import { useToast } from './components/ToastContext';
import { Play, RotateCcw } from 'lucide-react';
import { DOC_TITLES, DOC_URLS } from './lib/mockData';
import type { UploadResponse } from './lib/api';
import type { Citation, MemoryRef } from './types';

const ENV = import.meta.env as Record<string, string | undefined>;

/**
 * The PDF the canvas renders. Defaults to the real sample deposition served from
 * /public so the bbox snap lands on actual text out of the box in dev/mock mode.
 * If the fetch fails (file missing, bad worker), PdfCanvas falls back to drawing
 * the placeholder page, so the demo never hard-fails.
 */
const PDF_URL = ENV.VITE_PDF_URL ?? '/sample-deposition.pdf';

/** documentId -> human title, merged from the contract mapping + any citation titles. */
function titleFor(documentId: string, citations: Citation[]): string {
  const fromCite = citations.find((c) => c.documentId === documentId)?.documentTitle;
  return fromCite ?? DOC_TITLES[documentId] ?? documentId;
}

export function App(): JSX.Element {
  const envForceMock = ENV.VITE_MOCK_MODE === 'true';
  const [forceMock, setForceMock] = useState<boolean>(envForceMock);
  const toast = useToast();
  const prevReason = useRef<string | undefined>(undefined);

  // Resolve live-vs-mock against the backend on startup (skipped if forceMock).
  const session = useBackendSession({ forceMock });

  useEffect(() => {
    if (session.reason && session.reason !== prevReason.current) {
      const isOffline = session.reason.startsWith('offline:');
      const isForcedMock = session.reason === 'Forced mock mode';
      const isLiveFalse = session.reason === 'Backend reports live:false';
      const isMisconfigured = session.reason.includes('Backend live but no LiveKit URL');

      if (isMisconfigured) {
        // A REACHABLE backend that's misconfigured is a genuine problem.
        toast.error(`Connection Error: ${session.reason}`, 7000);
      } else if (!isOffline && !isForcedMock && !isLiveFalse) {
        // Other non-mock reasons are informational. The expected offline path,
        // forced-mock, and live:false stay silent — the MOCK/OFFLINE badge says it.
        toast.info(session.reason);
      }
      prevReason.current = session.reason;
    }
  }, [session.reason, toast]);

  // The hook only enters its LIVE branch when both URL + token are present.
  const cx = useCrossExam({
    livekitUrl: session.livekitUrl,
    livekitToken: session.livekitToken,
    forceMock,
  });

  // After an upload, render the freshly-ingested PDF and update corpus info.
  const [docInfo, setDocInfo] = useState<UploadResponse | null>(null);
  const [uploadedUrl, setUploadedUrl] = useState<string | null>(null);

  const onUploaded = useCallback((result: UploadResponse, file: File): void => {
    setDocInfo(result);
    // Render the uploaded file locally so the canvas reflects the new document.
    setUploadedUrl(URL.createObjectURL(file));
  }, []);

  // ---- FEATURE 1: multi-doc. Derive the set of documents from the citations.
  const docs: DocSwitcherDoc[] = useMemo(() => {
    const byId = new Map<string, DocSwitcherDoc>();
    for (const c of cx.citations) {
      const existing = byId.get(c.documentId);
      if (existing) {
        existing.count += 1;
        existing.scanned = existing.scanned || c.scanned === true;
      } else {
        byId.set(c.documentId, {
          id: c.documentId,
          title: titleFor(c.documentId, cx.citations),
          count: 1,
          scanned: c.scanned === true,
        });
      }
    }
    return [...byId.values()];
  }, [cx.citations]);

  // Selected document: follows the primary citation's doc by default, but a
  // manual tab/chip selection sticks until the agent surfaces a new primary.
  const primaryDocId = cx.activeCitation?.documentId ?? null;
  const [manualDocId, setManualDocId] = useState<string | null>(null);
  const lastPrimaryId = useRef<string | null>(null);

  useEffect(() => {
    // A NEW primary citation (new snap) overrides any manual doc selection.
    if (cx.primaryId && cx.primaryId !== lastPrimaryId.current) {
      lastPrimaryId.current = cx.primaryId;
      setManualDocId(null);
    }
  }, [cx.primaryId]);

  const activeDocId = manualDocId ?? primaryDocId ?? docs[0]?.id ?? null;

  // Resolve the PDF url for the active doc: an uploaded file wins, else the
  // contract mapping, else the default sample.
  const pdfUrl = useMemo(() => {
    if (uploadedUrl) return uploadedUrl;
    if (activeDocId && DOC_URLS[activeDocId]) return DOC_URLS[activeDocId];
    return PDF_URL;
  }, [uploadedUrl, activeDocId]);

  // Citations to render on the canvas: only those in the active document.
  const docCitations = useMemo(
    () => cx.citations.filter((c) => c.documentId === activeDocId),
    [cx.citations, activeDocId],
  );

  // The box to focus/label: the primary if it's in the active doc, else the
  // first citation of the active doc (so switching docs keeps a visible box).
  const activeForDoc: Citation | null = useMemo(() => {
    if (cx.activeCitation && cx.activeCitation.documentId === activeDocId) {
      return cx.activeCitation;
    }
    return docCitations[0] ?? null;
  }, [cx.activeCitation, activeDocId, docCitations]);

  const targetPage = activeForDoc?.bbox.page ?? cx.targetPage;

  const searching = cx.agentState === 'thinking';
  const hasResult = cx.activeCitation !== null;
  const resolving = session.status === 'resolving';

  const corpusPages = docInfo?.pages ?? cx.totalPages;

  // Re-fire the snap (re-center + focus the box) when a page-ref chip is clicked.
  const [refocus, setRefocus] = useState(0);
  const onJump = useCallback((citation: Citation): void => {
    setManualDocId(citation.documentId);
    setRefocus((n) => n + 1);
  }, []);

  // Flip to a conflicting citation from the contradiction banner.
  const onSelectConflict = useCallback((citation: Citation): void => {
    setManualDocId(citation.documentId);
    setRefocus((n) => n + 1);
  }, []);

  // FEATURE 5: a memory recall chip jumps back to the recalled citation.
  const onRecall = useCallback(
    (ref: MemoryRef): void => {
      const target = cx.citations.find((c) => c.id === ref.citationId);
      if (target) {
        setManualDocId(target.documentId);
        setRefocus((n) => n + 1);
      }
    },
    [cx.citations],
  );

  const onSelectDoc = useCallback((id: string): void => {
    setManualDocId(id);
  }, []);

  const onClearCitation = useCallback((): void => {
    cx.reset();
  }, [cx]);

  const modeLabel = resolving
    ? 'CONNECTING…'
    : cx.isMock
      ? 'MOCK / OFFLINE'
      : cx.isConnected
        ? 'LIVE · CONNECTED'
        : 'LIVE · CONNECTING…';

  const hudVariant = resolving || (cx.isConnected === false && !cx.isMock)
    ? 'resolving'
    : cx.isMock
      ? 'mock'
      : 'live';

  return (
    <div className="app">
      <Atmosphere />

      <header className="cmdbar">
        <div className="cmdbar__brand">
          <h1 className="cmdbar__wordmark">CrossExam</h1>
          <span className="cmdbar__case">CASE NO. 2026-CV-0914</span>
        </div>

        <div className="cmdbar__center">
          <div className={`hud hud--${hudVariant}`} title={session.reason ?? undefined} data-testid="mode-badge">
            <span className="hud__dot" aria-hidden="true" />
            {modeLabel}
          </div>
          <LiveLatencyBadge latencyMs={cx.lastLatencyMs} />
        </div>

        <div className="cmdbar__controls">
          <DocumentUpload onUploaded={onUploaded} disabled={resolving} />
          <label className="cmdbar__toggle">
            <input
              type="checkbox"
              checked={forceMock}
              onChange={(e) => setForceMock(e.target.checked)}
              aria-label="Force mock mode"
            />
            Force mock
          </label>
          <button className="btn btn--ghost" onClick={cx.reset} aria-label="Reset session">
            <RotateCcw size={13} /> Reset
          </button>
          <button className="btn btn--primary" onClick={cx.runDemo} disabled={!cx.isMock} aria-label="Run demo">
            <Play size={13} fill="currentColor" /> Run demo
          </button>
        </div>
      </header>

      <main className="stage">
        <section className="rail" aria-label="Voice interface">
          <VoiceOrb state={cx.agentState} audioReactive={!cx.isMock} />
          <StatePill state={cx.agentState} proactive={cx.proactive} />
          <Captions
            text={cx.caption}
            question={cx.question}
            citation={cx.activeCitation}
            onJump={onJump}
            proactive={cx.proactive}
            silenceReason={cx.silenceReason}
            speaker={cx.speaker}
            memory={cx.memory}
            onRecall={onRecall}
          />
          <ContradictionBanner
            citations={cx.contradiction ? cx.citations : []}
            hops={cx.hops}
            activeId={activeForDoc?.id ?? null}
            onSelect={onSelectConflict}
          />
        </section>

        <section className="docstage" aria-label="Document">
          <div className="docstage__hud">
            <DocSwitcher docs={docs} activeId={activeDocId} onSelect={onSelectDoc} />
            <PageJump active={searching} targetPage={targetPage} totalPages={corpusPages} />
            <LatencyChip
              pages={cx.activeCitation?.pagesSearched ?? corpusPages}
              latencyMs={cx.activeCitation?.latencyMs ?? 7}
              visible={hasResult}
            />
          </div>
          <PdfCanvas
            page={targetPage}
            citation={activeForDoc}
            citations={docCitations}
            pdfUrl={pdfUrl}
            onClearCitation={onClearCitation}
            refocusSignal={refocus}
            proactive={cx.proactive && activeForDoc?.id === cx.activeCitation?.id}
          />
        </section>
      </main>
    </div>
  );
}
