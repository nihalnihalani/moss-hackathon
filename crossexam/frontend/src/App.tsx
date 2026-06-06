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
import { EmptyDropzone } from './components/EmptyDropzone';
import { QuestionPalette } from './components/QuestionPalette';
import { ShortcutsCheatSheet } from './components/ShortcutsCheatSheet';
import { useToast } from './components/ToastContext';
import { ToggleSwitch } from './components/ToggleSwitch';
import { Logo } from './components/Logo';
import { useShortcuts } from './hooks/useShortcuts';
import { Play, RotateCcw, Mic } from 'lucide-react';
import {
  DOC_TITLES,
  DOC_URLS,
  DEMO_QUESTION,
  DEMO_CONTRADICTION_QUESTION,
  CONTRACT_EMAIL_QUESTION,
  CONTRACT_EMAIL_ANCHOR,
  CONTRACT_EMAIL_CONFLICT_NOTE,
} from './lib/mockData';
import { ExportMenu } from './components/ExportMenu';
import { MemoSheet } from './components/MemoSheet';
import { buildMemo } from './lib/memo';
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
  // Has the user loaded/surfaced ANY document yet? Drives the empty-state
  // dropzone vs. the rendered canvas (Quick Win #2). Flipped on first upload or
  // once a citation/doc is surfaced (e.g. the mock demo).
  const [docLoaded, setDocLoaded] = useState(false);

  const onUploaded = useCallback((result: UploadResponse, file: File): void => {
    setDocInfo(result);
    // Render the uploaded file locally so the canvas reflects the new document.
    // Revoke any prior object URL first so re-uploading never leaks (m3).
    setUploadedUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return URL.createObjectURL(file);
    });
    setDocLoaded(true);
  }, []);

  // Revoke the live object URL on unmount so a closed tab/session never leaks it.
  useEffect(() => {
    return () => {
      if (uploadedUrl) URL.revokeObjectURL(uploadedUrl);
    };
  }, [uploadedUrl]);

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

  // Surfacing any citation (e.g. the mock demo) counts as "a document is loaded",
  // so the empty-state dropzone steps aside for the rendered canvas.
  useEffect(() => {
    if (cx.citations.length > 0) setDocLoaded(true);
  }, [cx.citations.length]);

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

  // ---- KILLER FEATURE B: the cross-document breach. When the conflict spans two
  // docs, the PRIMARY (amber) is the claim/clause under examination and the
  // COUNTER (rose) is the other doc that proves the breach. We surface an anchor
  // banner + a one-line note, and auto-flip to the counter doc to draw its box.
  const conflictCounter: Citation | null = useMemo(() => {
    if (!cx.contradiction || cx.citations.length < 2) return null;
    const counter = cx.citations.find((c) => c.id !== cx.primaryId);
    if (!counter) return null;
    // Cross-doc only: a same-doc conflict needs no tab flip.
    const primary = cx.citations.find((c) => c.id === cx.primaryId) ?? cx.citations[0];
    if (primary && counter.documentId === primary.documentId) return null;
    return counter;
  }, [cx.contradiction, cx.citations, cx.primaryId]);

  // One-line plain-English conflict note. Special-cased for the contract↔email
  // anchor; otherwise a generic cross-source statement (verbatim quotes live in
  // the banner chips + the canvas boxes, never paraphrased there).
  const conflictNote = useMemo(() => {
    if (!cx.contradiction || cx.citations.length < 2) return null;
    if (cx.anchor === CONTRACT_EMAIL_ANCHOR) return CONTRACT_EMAIL_CONFLICT_NOTE;
    return null;
  }, [cx.contradiction, cx.citations.length, cx.anchor]);

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

  // Auto tab-switch: on a NEW anchored cross-doc breach, hold on the primary
  // briefly (let its box snap), then flip to the counter doc and draw its box —
  // visually proving the breach. Fires once per contradiction signal; the user
  // can still flip back via the doc tabs or the banner chips.
  const lastBreachKey = useRef<string | null>(null);
  useEffect(() => {
    if (!cx.anchor || !conflictCounter) {
      if (!cx.contradiction) lastBreachKey.current = null;
      return;
    }
    const key = `${cx.anchor}|${cx.primaryId ?? ''}|${conflictCounter.id}`;
    if (lastBreachKey.current === key) return;
    lastBreachKey.current = key;
    const t = setTimeout(() => {
      setManualDocId(conflictCounter.documentId);
      setRefocus((n) => n + 1);
    }, 1500);
    return () => clearTimeout(t);
  }, [cx.anchor, cx.primaryId, cx.contradiction, conflictCounter]);

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

  // Full session reset: return the stage to the empty dropzone. Resets the
  // CrossExam snapshot, clears the docLoaded flag (so the EmptyDropzone renders
  // again), revokes any uploaded object URL (fixes the m3 leak), and clears the
  // uploaded/corpus + manual doc/contradiction UI selection state.
  const onReset = useCallback((): void => {
    cx.reset();
    setDocLoaded(false);
    if (uploadedUrl) URL.revokeObjectURL(uploadedUrl);
    setUploadedUrl(null);
    setDocInfo(null);
    setManualDocId(null);
    lastPrimaryId.current = null;
    lastBreachKey.current = null;
    setRefocus(0);
  }, [cx, uploadedUrl]);

  // ---- QUICK WIN #1: keyboard shortcuts ------------------------------------
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  // Mic / push-to-talk pressed state, mirrored for aria-pressed + aria-live.
  const [micActive, setMicActive] = useState(false);

  // Cycle the document tabs by ±1 (wraps). Only meaningful with 2+ docs, which is
  // also the only time the DocSwitcher renders.
  const cycleDoc = useCallback(
    (delta: number): void => {
      if (docs.length < 2) return;
      const current = docs.findIndex((d) => d.id === activeDocId);
      const base = current === -1 ? 0 : current;
      const nextIndex = (base + delta + docs.length) % docs.length;
      const next = docs[nextIndex];
      if (next) setManualDocId(next.id);
    },
    [docs, activeDocId],
  );

  const onTalkStart = useCallback((): void => {
    setMicActive(true);
    cx.startListening();
  }, [cx]);
  const onTalkEnd = useCallback((): void => {
    setMicActive(false);
    cx.stopListening();
  }, [cx]);

  const onAsk = useCallback(
    (question: string): void => {
      cx.ask(question);
    },
    [cx],
  );

  const { isMac } = useShortcuts({
    onTalkStart,
    onTalkEnd,
    onOpenPalette: useCallback(() => setPaletteOpen(true), []),
    onPrevDoc: useCallback(() => cycleDoc(-1), [cycleDoc]),
    onNextDoc: useCallback(() => cycleDoc(1), [cycleDoc]),
    onToggleHelp: useCallback(() => setHelpOpen((v) => !v), []),
  });

  // The mic affordance is a real button (pointer push-to-talk mirrors Space).
  const onMicDown = useCallback((): void => onTalkStart(), [onTalkStart]);
  const onMicUp = useCallback((): void => onTalkEnd(), [onTalkEnd]);

  // Suggested prompts in the palette: the canonical demo questions in mock.
  const paletteSuggestions = useMemo(
    () => (cx.isMock ? [DEMO_QUESTION, DEMO_CONTRADICTION_QUESTION, CONTRACT_EMAIL_QUESTION] : []),
    [cx.isMock],
  );

  // ---- KILLER FEATURE A: build the legal memo from the live session state. The
  // model is deterministic + pure; the ExportMenu turns it into Markdown or a
  // print-to-PDF via the hidden MemoSheet below.
  const memo = useMemo(
    () =>
      buildMemo({
        question: cx.question,
        caption: cx.caption,
        citations: cx.citations,
        primaryId: cx.primaryId,
        contradiction: cx.contradiction,
        anchor: cx.anchor,
        hops: cx.hops,
      }),
    [cx.question, cx.caption, cx.citations, cx.primaryId, cx.contradiction, cx.anchor, cx.hops],
  );

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
          <Logo />
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
          <ExportMenu memo={memo} />
          <ToggleSwitch
            label="Force mock"
            checked={forceMock}
            onChange={(e) => setForceMock(e.target.checked)}
            aria-label="Force mock mode"
          />
          <button className="btn btn--ghost" onClick={onReset} aria-label="Reset session">
            <RotateCcw size={13} /> Reset
          </button>
          <button className="btn btn--primary" onClick={cx.runDemo} disabled={!cx.isMock} aria-label="Run demo">
            <Play size={13} fill="currentColor" /> Run demo
          </button>
        </div>
      </header>

      <main className="stage">
        <section className="rail" aria-label="Voice interface">
          <VoiceOrb
            state={cx.agentState}
            audioReactive={!cx.isMock}
            outputStream={cx.outputStream}
          />
          <StatePill state={cx.agentState} proactive={cx.proactive} />

          {/* Push-to-talk affordance: hold (Space or pointer) to talk. Announced
              politely so screen-reader users hear "Listening…" / "Mic off". */}
          <button
            type="button"
            className={`mic-control${micActive ? ' mic-control--active' : ''}`}
            data-testid="mic-control"
            aria-keyshortcuts="Space"
            aria-pressed={micActive}
            aria-label="Push to talk. Hold Space or this button to talk."
            onPointerDown={onMicDown}
            onPointerUp={onMicUp}
            onPointerLeave={() => micActive && onMicUp()}
          >
            <Mic size={13} aria-hidden="true" />
            {micActive ? 'Listening…' : 'Hold Space to talk'}
          </button>
          <span className="visually-hidden" role="status" aria-live="polite" data-testid="mic-announce">
            {micActive ? 'Listening…' : 'Mic off'}
          </span>

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
            primaryId={cx.primaryId}
            anchor={cx.anchor}
            conflictNote={conflictNote}
          />
        </section>

        <section className="docstage" aria-label="Document">
          {docLoaded ? (
            <>
              <div className="docstage__hud">
                <DocSwitcher docs={docs} activeId={activeDocId} onSelect={onSelectDoc} />
                <PageJump active={searching} targetPage={targetPage} totalPages={corpusPages} />
                <LatencyChip
                  pages={cx.activeCitation?.pagesSearched ?? corpusPages}
                  latencyMs={cx.activeCitation?.latencyMs ?? 7}
                  visible={hasResult}
                />
              </div>

              {/* + New document: reuses the picker without leaving the canvas. */}
              <DocumentUpload
                onUploaded={onUploaded}
                disabled={resolving}
                variant="compact"
              />

              <PdfCanvas
                page={targetPage}
                citation={activeForDoc}
                citations={docCitations}
                pdfUrl={pdfUrl}
                onClearCitation={onClearCitation}
                refocusSignal={refocus}
                proactive={cx.proactive && activeForDoc?.id === cx.activeCitation?.id}
                counterId={conflictCounter?.id ?? null}
              />
            </>
          ) : (
            <EmptyDropzone onUploaded={onUploaded} disabled={resolving} />
          )}
        </section>
      </main>

      {/* Quick Win #1 overlays: Cmd/Ctrl+K palette + the ? cheat-sheet. */}
      <QuestionPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        onAsk={onAsk}
        suggestions={paletteSuggestions}
      />
      <ShortcutsCheatSheet open={helpOpen} onClose={() => setHelpOpen(false)} isMac={isMac} />

      {/* Killer feature A: the print-only memo sheet. Hidden on screen; isolated
          and laid out for Letter paper under @media print (window.print()). */}
      <MemoSheet memo={memo} />
    </div>
  );
}
