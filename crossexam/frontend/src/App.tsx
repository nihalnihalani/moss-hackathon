import { useCallback, useState, useEffect, useRef } from 'react';
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
import { useToast } from './components/ToastContext';
import { Play, RotateCcw } from 'lucide-react';
import type { UploadResponse } from './lib/api';
import type { Citation } from './types';

const ENV = import.meta.env as Record<string, string | undefined>;

/**
 * The PDF the canvas renders. Defaults to the real sample deposition served from
 * /public so the bbox snap lands on actual text out of the box in dev/mock mode.
 * If the fetch fails (file missing, bad worker), PdfCanvas falls back to drawing
 * the placeholder page, so the demo never hard-fails.
 */
const PDF_URL = ENV.VITE_PDF_URL ?? '/sample-deposition.pdf';

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
  const [pdfUrl, setPdfUrl] = useState<string>(PDF_URL);

  const onUploaded = useCallback((result: UploadResponse, file: File): void => {
    setDocInfo(result);
    // Render the uploaded file locally so the canvas reflects the new document.
    setPdfUrl(URL.createObjectURL(file));
  }, []);

  const searching = cx.agentState === 'thinking';
  const hasResult = cx.activeCitation !== null;
  const resolving = session.status === 'resolving';

  const corpusPages = docInfo?.pages ?? cx.totalPages;

  // Re-fire the snap (re-center + focus the box) when a page-ref chip is clicked.
  const [refocus, setRefocus] = useState(0);
  const onJump = useCallback((_citation: Citation): void => {
    setRefocus((n) => n + 1);
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
          <VoiceOrb state={cx.agentState} />
          <StatePill state={cx.agentState} proactive={cx.proactive} />
          <Captions
            text={cx.caption}
            question={cx.question}
            citation={cx.activeCitation}
            onJump={onJump}
            proactive={cx.proactive}
            silenceReason={cx.silenceReason}
          />
        </section>

        <section className="docstage" aria-label="Document">
          <div className="docstage__hud">
            <PageJump active={searching} targetPage={cx.targetPage} totalPages={corpusPages} />
            <LatencyChip
              pages={cx.activeCitation?.pagesSearched ?? corpusPages}
              latencyMs={cx.activeCitation?.latencyMs ?? 7}
              visible={hasResult}
            />
          </div>
          <PdfCanvas
            page={cx.targetPage}
            citation={cx.activeCitation}
            pdfUrl={pdfUrl}
            onClearCitation={onClearCitation}
            refocusSignal={refocus}
            proactive={cx.proactive}
          />
        </section>
      </main>
    </div>
  );
}
