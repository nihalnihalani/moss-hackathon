import { AlertTriangle, CornerDownRight, ScanLine } from 'lucide-react';
import type { Citation, HopTrace } from '../types';

export interface ContradictionBannerProps {
  /** The conflicting citations (typically two, across docs/pages). */
  citations: Citation[];
  /** The "how I found this" decomposition trail. */
  hops: HopTrace[];
  /** Which conflicting citation is currently focused. */
  activeId: string | null;
  /** Flip to a conflicting citation (jumps its doc/page into view). */
  onSelect: (citation: Citation) => void;
}

/**
 * CONTRADICTION UI (feat 1). A clear "Contradiction across documents" banner with
 * a "how I found this" hops trail (the decomposition sub-queries), plus doc/page
 * chips to flip between the two conflicting citations. Only the conflicting
 * citations are shown as chips; clicking one jumps the canvas there.
 */
export function ContradictionBanner({
  citations,
  hops,
  activeId,
  onSelect,
}: ContradictionBannerProps): JSX.Element | null {
  if (citations.length < 2) return null;

  // The conflicting pair spans >1 document iff there are 2+ distinct documentIds.
  const docIds = new Set(citations.map((c) => c.documentId));
  const crossDoc = docIds.size > 1;

  return (
    <section
      className="contradiction"
      role="alert"
      aria-label="Contradiction detected"
      data-testid="contradiction-banner"
    >
      <header className="contradiction__head">
        <span className="contradiction__mark" aria-hidden="true">
          <AlertTriangle size={13} />
        </span>
        <span className="contradiction__title">
          Contradiction {crossDoc ? 'across documents' : 'in the record'}
        </span>
      </header>

      <div className="contradiction__chips" role="group" aria-label="Conflicting sources">
        {citations.map((c) => {
          const active = c.id === activeId;
          return (
            <button
              key={c.id}
              type="button"
              className={`contradiction__chip${active ? ' contradiction__chip--active' : ''}`}
              data-testid={`contradiction-chip-${c.id}`}
              aria-pressed={active}
              onClick={() => onSelect(c)}
            >
              <span className="contradiction__chip-doc">
                {c.documentTitle ?? c.documentId}
              </span>
              {c.scanned ? (
                <span className="contradiction__chip-scan" aria-label="Scanned source">
                  <ScanLine size={9} />
                </span>
              ) : null}
              <span className="contradiction__chip-page">p.{c.bbox.page}</span>
            </button>
          );
        })}
      </div>

      {hops.length > 0 ? (
        <ol className="hops" aria-label="How I found this" data-testid="hops-trail">
          <li className="hops__label" aria-hidden="true">
            How I found this
          </li>
          {hops.map((h, i) => (
            <li className="hops__step" key={`${i}-${h.subQuery}`}>
              <span className="hops__arrow" aria-hidden="true">
                <CornerDownRight size={11} />
              </span>
              <span className="hops__query">{h.subQuery}</span>
            </li>
          ))}
        </ol>
      ) : null}
    </section>
  );
}
