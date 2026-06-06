export interface NotFoundStateProps {
  /** Render only when the agent declined to surface a box (honest silence). */
  visible: boolean;
}

/**
 * The honest empty state. When a `not_found_in_document` frame arrives, the agent
 * surfaced NO box — it stayed silent rather than guess. This is a TRUST feature,
 * not an error: a calm, muted note near the transcript that says so plainly.
 *
 * Announced politely (assistive tech hears the silence too).
 */
export function NotFoundState({ visible }: NotFoundStateProps): JSX.Element | null {
  if (!visible) return null;
  return (
    <div className="not-found" role="status" aria-live="polite" data-testid="not-found-state">
      <span className="not-found__mark" aria-hidden="true" />
      <div className="not-found__body">
        <p className="not-found__title">No grounded source — staying silent</p>
        <p className="not-found__sub">
          Nothing in the document supports this. The co-pilot declined to show a box
          rather than guess.
        </p>
      </div>
    </div>
  );
}
