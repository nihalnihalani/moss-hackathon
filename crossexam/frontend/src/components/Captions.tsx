import { useEffect, useState } from 'react';
import type { Citation } from '../types';

export interface CaptionsProps {
  /** The agent's spoken line; revealed word-by-word (Newsreader serif). */
  text: string;
  /** The user's question, shown as a mono right-aligned line above the answer. */
  question?: string;
  /** The citation backing the current answer; renders a clickable page-ref chip. */
  citation?: Citation | null;
  /** Re-fire the snap to a citation's page when its chip is activated. */
  onJump?: (citation: Citation) => void;
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
export function Captions({ text, question, citation, onJump }: CaptionsProps): JSX.Element {
  // Re-key the reveal so the stagger restarts whenever the spoken line changes.
  const [revealKey, setRevealKey] = useState(0);
  useEffect(() => setRevealKey((k) => k + 1), [text]);

  const words = text ? text.split(/(\s+)/) : [];

  return (
    <div className="captions" role="log" aria-live="polite" aria-atomic="false">
      {question ? (
        <div className="caption-line caption-line--user">
          <p className="caption-line__user">{question}</p>
        </div>
      ) : null}

      {text ? (
        <div className="caption-line caption-line--agent">
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
            <button
              type="button"
              className="pageref"
              onClick={() => onJump?.(citation)}
              aria-label={`Jump to citation on page ${citation.bbox.page}`}
            >
              p.{citation.bbox.page} · {Math.round(citation.confidence * 100)}%
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
