/**
 * MemoSheet — KILLER FEATURE A: the print-only memorandum.
 *
 * Renders a MemoModel as a paginated legal memo. Hidden on screen (the
 * `.memo-sheet` node is display:none until @media print), it becomes the ONLY
 * visible content when the browser prints, laid out for US-Letter paper with
 * 1in margins. Findings, quotes, and citations use break-inside:avoid; the
 * Appendix starts on a fresh page via break-before:page. Text stays crisp and
 * selectable — no html2canvas raster.
 */

import type { MemoCitation, MemoModel } from '../lib/memo';

export interface MemoSheetProps {
  memo: MemoModel;
}

function CitationQuote({ c }: { c: MemoCitation }): JSX.Element {
  return (
    <div className="memo-cite">
      <div className="memo-cite__head">
        {c.documentTitle}, p. {c.page}
        {c.grounded != null ? (
          <span className="memo-cite__grounded"> (grounded {c.grounded.toFixed(2)})</span>
        ) : null}
      </div>
      <blockquote className="memo-cite__quote">{c.quote}</blockquote>
    </div>
  );
}

export function MemoSheet({ memo }: MemoSheetProps): JSX.Element {
  return (
    <article className="memo-sheet" data-testid="memo-sheet" aria-hidden="true">
      <header className="memo-sheet__heading">
        <h1 className="memo-sheet__title">{memo.heading.title}</h1>
        <p className="memo-sheet__caption">{memo.heading.caption}</p>
        <p className="memo-sheet__re">{memo.heading.re}</p>
      </header>

      <section className="memo-section">
        <h2 className="memo-section__title">Question Presented</h2>
        <p>{memo.questionPresented}</p>
      </section>

      <section className="memo-section">
        <h2 className="memo-section__title">Brief Answer</h2>
        <p>{memo.briefAnswer}</p>
      </section>

      <section className="memo-section">
        <h2 className="memo-section__title">Statement of Facts</h2>
        {memo.statementOfFacts.map((p, i) => (
          <p key={i}>{p}</p>
        ))}
      </section>

      <section className="memo-section">
        <h2 className="memo-section__title">Findings</h2>
        {memo.findings.length === 0 ? (
          <p className="memo-empty">No grounded findings on the present record.</p>
        ) : (
          memo.findings.map((f, i) => (
            <div className="memo-finding" key={i}>
              <h3 className="memo-finding__title">Finding {i + 1}</h3>
              <p>{f.statement}</p>
              <CitationQuote c={f.citation} />
            </div>
          ))
        )}
      </section>

      <section className="memo-section">
        <h2 className="memo-section__title">Contradictions / Discrepancies</h2>
        {memo.contradictions.length === 0 ? (
          <p className="memo-empty">No contradictions detected on the present record.</p>
        ) : (
          memo.contradictions.map((c, i) => (
            <div className="memo-conflict" key={i}>
              <h3 className="memo-conflict__title">
                Discrepancy {i + 1}
                {c.anchor ? ` — Anchor: ${c.anchor}` : ''}
              </h3>
              <p className="memo-conflict__nature">{c.nature}</p>
              <div className="memo-conflict__side">
                <div className="memo-conflict__label">
                  Primary — {c.primary.documentTitle}, p. {c.primary.page}
                </div>
                <blockquote className="memo-cite__quote">{c.primary.quote}</blockquote>
              </div>
              <div className="memo-conflict__side">
                <div className="memo-conflict__label">
                  Counter — {c.counter.documentTitle}, p. {c.counter.page}
                </div>
                <blockquote className="memo-cite__quote">{c.counter.quote}</blockquote>
              </div>
            </div>
          ))
        )}
      </section>

      <section className="memo-section">
        <h2 className="memo-section__title">Discussion</h2>
        <p>
          <strong>Issue.</strong> {memo.discussion.issue}
        </p>
        <p>
          <strong>Rule.</strong> {memo.discussion.rule}
        </p>
        <p>
          <strong>Application.</strong> {memo.discussion.application}
        </p>
        <p>
          <strong>Conclusion.</strong> {memo.discussion.conclusion}
        </p>
      </section>

      <section className="memo-section">
        <h2 className="memo-section__title">Conclusion</h2>
        <p>{memo.conclusion}</p>
      </section>

      <section className="memo-section memo-section--appendix">
        <h2 className="memo-section__title">Appendix — Cited Passages</h2>
        {memo.appendix.length === 0 ? (
          <p className="memo-empty">No cited passages.</p>
        ) : (
          memo.appendix.map((c, i) => (
            <div className="memo-appendix-item" key={c.id}>
              <div className="memo-appendix-item__head">
                [A{i + 1}] {c.documentTitle}, p. {c.page}
              </div>
              <blockquote className="memo-cite__quote">{c.quote}</blockquote>
            </div>
          ))
        )}
      </section>
    </article>
  );
}
