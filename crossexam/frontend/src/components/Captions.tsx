import { useEffect, useRef, useState } from 'react';

export interface CaptionsProps {
  /** The full caption text; rendered with a typewriter reveal for the "streaming" feel. */
  text: string;
  /** The user's question, shown above the streamed answer. */
  question?: string;
}

/** Streaming captions panel: shows the question, then types out the agent's answer. */
export function Captions({ text, question }: CaptionsProps): JSX.Element {
  const [shown, setShown] = useState('');
  const rafRef = useRef<number>(0);

  useEffect(() => {
    cancelAnimationFrame(rafRef.current);
    if (!text) {
      setShown('');
      return;
    }
    let i = 0;
    let last = performance.now();
    const tick = (now: number): void => {
      // ~45 chars/sec reveal.
      if (now - last > 22) {
        i = Math.min(text.length, i + 1);
        setShown(text.slice(0, i));
        last = now;
      }
      if (i < text.length) rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, [text]);

  return (
    <div className="captions" aria-live="polite">
      {question ? <p className="captions__question">“{question}”</p> : null}
      <p className="captions__answer">
        {shown}
        {shown.length < text.length ? <span className="captions__cursor" aria-hidden="true" /> : null}
      </p>
    </div>
  );
}
