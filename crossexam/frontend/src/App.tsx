import { useState } from 'react';
import { useCrossExam } from './hooks/useCrossExam';
import { VoiceOrb } from './components/VoiceOrb';
import { StatePill } from './components/StatePill';
import { Captions } from './components/Captions';
import { PdfCanvas } from './components/PdfCanvas';
import { LatencyChip } from './components/LatencyChip';
import { PageJump } from './components/PageJump';

const ENV = import.meta.env as Record<string, string | undefined>;

export function App(): JSX.Element {
  const envForceMock = ENV.VITE_MOCK_MODE === 'true';
  const [forceMock, setForceMock] = useState<boolean>(envForceMock);

  const cx = useCrossExam({
    livekitUrl: ENV.VITE_LIVEKIT_URL,
    livekitToken: ENV.VITE_LIVEKIT_TOKEN,
    forceMock,
  });

  const searching = cx.agentState === 'thinking';
  const hasResult = cx.activeCitation !== null;

  return (
    <div className="app">
      <header className="app__header">
        <div className="app__brand">
          <span className="app__logo" aria-hidden="true" />
          <h1 className="app__title">CrossExam</h1>
          <span className="app__tagline">Ask the document. It points to the proof.</span>
        </div>
        <div className="app__controls">
          <span className={`app__mode ${cx.isMock ? 'app__mode--mock' : 'app__mode--live'}`}>
            {cx.isMock ? 'MOCK MODE' : cx.isConnected ? 'LIVE · CONNECTED' : 'LIVE · CONNECTING…'}
          </span>
          <label className="app__toggle">
            <input
              type="checkbox"
              checked={forceMock}
              onChange={(e) => setForceMock(e.target.checked)}
            />
            Force mock
          </label>
          <button className="app__btn" onClick={cx.runDemo} disabled={!cx.isMock}>
            ▶ Run demo
          </button>
          <button className="app__btn app__btn--ghost" onClick={cx.reset}>
            Reset
          </button>
        </div>
      </header>

      <main className="app__split">
        <section className="app__pane app__pane--voice" aria-label="Voice interface">
          <StatePill state={cx.agentState} />
          <VoiceOrb state={cx.agentState} />
          <Captions text={cx.caption} question={cx.question} />
        </section>

        <section className="app__pane app__pane--doc" aria-label="Document">
          <div className="doc__chrome">
            <PageJump active={searching} targetPage={cx.targetPage} totalPages={cx.totalPages} />
            <LatencyChip
              pages={cx.activeCitation?.pagesSearched ?? cx.totalPages}
              latencyMs={cx.activeCitation?.latencyMs ?? 7}
              visible={hasResult}
            />
          </div>
          <PdfCanvas
            page={cx.targetPage}
            citation={cx.activeCitation}
            pdfUrl={ENV.VITE_PDF_URL}
          />
        </section>
      </main>
    </div>
  );
}
