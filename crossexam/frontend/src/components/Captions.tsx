import { useEffect, useState } from 'react';
import type { Citation, SilenceReason } from '../types';
import { NotFoundState } from './NotFoundState';

export interface CaptionsProps {
  /** The agent's spoken line; revealed word-by-word (Newsreader serif). */
  text: string;
  /** The user's question, shown as a mono right-aligned line above the answer. */
  question?: string;
  /** The citation backing the current answer; renders a clickable page-ref chip. */
  citation?: Citation | null;
  /** Re-fire the snap to a citation's page when its chip is activated. */
  onJump?: (citation: Citation) => void;
  /** The active citation was surfaced UNPROMPTED — show the ambient tag. */
  proactive?: boolean;
  /** Honest-silence reason; renders the "staying silent" empty state when set. */
  silenceReason?: SilenceReason | null;
}

/** Reveal stagger per word (ms), matching the spec's 35ms cadence. */
const WORD_STEP_MS = 35;

/**
 * Streaming captions. The agent's answer is split into words that fade/de-blur
 * in on a staggered delay so the line feels *spoken* as the citation settles.
 * Each answer carries a clickable mono page-ref chip that re-fires the snap.
 *
 * The live region (`role=log`, polite, non-atomic) is pre-rendered empty in the
 * DOM so assistive tech is already observing it before the first word lands.
 */
export function Captions({
  text,
  question,
  citation,
  onJump,
  proactive,
  silenceReason,
}: CaptionsProps): JSX.Element {
  // Re-key the reveal so the stagger restarts whenever the spoken line changes.
  const [revealKey, setRevealKey] = useState(0);
  useEffect(() => setRevealKey((k) => k + 1), [text]);

  const words = text ? text.split(/(\s+)/) : [];
  const faith = citation?.faithfulness;

  return (
    <div className="captions" role="log" aria-live="polite" aria-atomic="false">
      {question ? (
        <div className="caption-line caption-line--user">
          <p className="caption-line__user">{question}</p>
        </div>
      ) : null}

      {text ? (
        <div className="caption-line caption-line--agent">
          {proactive && citation ? (
            <span className="ambient-tag" data-testid="ambient-tag">
              <span className="ambient-tag__dot" aria-hidden="true" />
              Surfaced automatically
            </span>
          ) : null}

          <p className="caption-line__agent" key={revealKey}>
            {words.map((w, i) =>
              /^\s+$/.test(w) ? (
                <span key={i}> </span>
              ) : (
                <span
                  className="caption-word"
                  key={i}
                  style={{ animationDelay: `${i * WORD_STEP_MS}ms` }}
                >
                  {w}
                </span>
              ),
            )}
          </p>

          {citation ? (
            <div className="caption-line__provenance">
              <button
                type="button"
                className="pageref"
                onClick={() => onJump?.(citation)}
                aria-label={`Jump to citation on page ${citation.bbox.page}`}
              >
                p.{citation.bbox.page} · {Math.round(citation.confidence * 100)}%
              </button>
              {faith ? (
                <span
                  className={`grounded${faith.supported ? ' grounded--supported' : ' grounded--unsupported'}`}
                  title={`Faithfulness check: ${faith.method}`}
                  data-testid="grounded-indicator"
                  aria-label={`Grounded confidence ${faith.score.toFixed(2)}${
                    faith.supported ? ', supported' : ', unsupported'
                  }`}
                >
                  <span className="grounded__mark" aria-hidden="true" />
                  grounded {faith.score.toFixed(2)}
                </span>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}

      <NotFoundState visible={silenceReason === 'not_found_in_document'} />
    </div>
  );
}
