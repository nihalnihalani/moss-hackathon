import { Files, ScanLine } from 'lucide-react';

export interface DocSwitcherDoc {
  /** Stable document id (matches Citation.documentId). */
  id: string;
  /** Human label for the tab. */
  title: string;
  /** How many surfaced citations live in this document. */
  count: number;
  /** Any citation in this doc is from a scanned/OCR source. */
  scanned: boolean;
}

export interface DocSwitcherProps {
  /** Documents present across the surfaced citations. */
  docs: DocSwitcherDoc[];
  /** Currently selected document id. */
  activeId: string | null;
  /** Select a document (jumps the canvas to it). */
  onSelect: (id: string) => void;
}

/**
 * The DOC SWITCHER (feat 1). Tabs for every document present in the surfaced
 * citations; selecting one maps `documentId -> PDF url` and jumps the canvas to
 * that doc. A scanned doc gets a small OCR glyph. Hidden until 2+ docs appear, so
 * a single-doc answer stays uncluttered.
 */
export function DocSwitcher({ docs, activeId, onSelect }: DocSwitcherProps): JSX.Element | null {
  if (docs.length < 2) return null;

  return (
    <div
      className="doc-switcher"
      role="tablist"
      aria-label="Documents"
      data-testid="doc-switcher"
    >
      <span className="doc-switcher__icon" aria-hidden="true">
        <Files size={12} />
      </span>
      {docs.map((d) => {
        const active = d.id === activeId;
        return (
          <button
            key={d.id}
            type="button"
            role="tab"
            aria-selected={active}
            className={`doc-switcher__tab${active ? ' doc-switcher__tab--active' : ''}`}
            data-testid={`doc-tab-${d.id}`}
            onClick={() => onSelect(d.id)}
          >
            <span className="doc-switcher__title">{d.title}</span>
            {d.scanned ? (
              <span className="doc-switcher__scan" aria-label="Scanned source" title="Scanned source">
                <ScanLine size={10} />
              </span>
            ) : null}
            <span className="doc-switcher__count" aria-hidden="true">
              {d.count}
            </span>
          </button>
        );
      })}
    </div>
  );
}
