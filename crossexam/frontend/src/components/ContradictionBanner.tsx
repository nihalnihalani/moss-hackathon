import { AlertTriangle, CornerDownRight, ScanLine, Crosshair } from 'lucide-react';
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
  /**
   * The PRIMARY citation id (the claim/clause under examination). The primary
   * keeps the amber treatment; the other conflicting citation is the COUNTER,
   * given a distinct rose treatment so the two read apart across a room.
   */
  primaryId?: string | null;
  /**
   * The shared ANCHOR the conflict hangs off (e.g. "§4.2 Subcontracting"). When
   * present, an anchor banner + a one-line plain-English conflict note render.
   */
  anchor?: string | null;
  /** One-line plain-English statement of the conflict. */
  conflictNote?: string | null;
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
  primaryId,
  anchor,
  conflictNote,
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

      {/* The anchor banner (killer feature B): names the shared clause/term the
          two sources hang off, with a one-line plain-English breach note. */}
      {anchor ? (
        <div className="contradiction__anchor" data-testid="contradiction-anchor">
          <span className="contradiction__anchor-mark" aria-hidden="true">
            <Crosshair size={11} />
          </span>
          <span className="contradiction__anchor-label">Conflict — Anchor:</span>
          <span className="contradiction__anchor-value">{anchor}</span>
        </div>
      ) : null}

      {conflictNote ? (
        <p className="contradiction__note" data-testid="contradiction-note">
          {conflictNote}
        </p>
      ) : null}

      <div className="contradiction__chips" role="group" aria-label="Conflicting sources">
        {citations.map((c) => {
          const active = c.id === activeId;
          // The non-primary conflicting citation is the COUNTER (rose). With no
          // primaryId given, fall back to the prior amber-only treatment.
          const counter = primaryId != null && c.id !== primaryId;
          return (
            <button
              key={c.id}
              type="button"
              className={`contradiction__chip${active ? ' contradiction__chip--active' : ''}${
                counter ? ' contradiction__chip--counter' : ''
              }`}
              data-testid={`contradiction-chip-${c.id}`}
              data-role={counter ? 'counter' : 'primary'}
              aria-pressed={active}
              onClick={() => onSelect(c)}
            >
              <span className="contradiction__chip-role" aria-hidden="true">
                {counter ? 'Counter' : 'Primary'}
              </span>
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
