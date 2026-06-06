/**
 * memo.test.ts — KILLER FEATURE A: the deterministic legal-memo builder.
 *
 * Pins:
 *  - buildMemo produces every section, in order, from a sample session.
 *  - a contradiction yields a Contradictions entry with the anchor + BOTH
 *    conflicting verbatim quotes + a one-line nature.
 *  - memoToMarkdown contains the verbatim quotes + the anchor, byte-faithful.
 *  - buildMemo is pure/deterministic (same input -> identical output).
 */
import { describe, it, expect } from 'vitest';
import { buildMemo, memoToMarkdown, type MemoSession } from './memo';
import {
  CONTRACT_CLAUSE_CITATION,
  EMAIL_ADMISSION_CITATION,
  CONTRACT_EMAIL_ANCHOR,
  CONTRACT_EMAIL_HOPS,
  CONTRACT_EMAIL_QUESTION,
  CONTRACT_EMAIL_TRANSCRIPT,
} from './mockData';

const SESSION: MemoSession = {
  question: CONTRACT_EMAIL_QUESTION,
  caption: CONTRACT_EMAIL_TRANSCRIPT,
  citations: [CONTRACT_CLAUSE_CITATION, EMAIL_ADMISSION_CITATION],
  primaryId: CONTRACT_CLAUSE_CITATION.id,
  contradiction: true,
  anchor: CONTRACT_EMAIL_ANCHOR,
  hops: CONTRACT_EMAIL_HOPS,
};

describe('buildMemo', () => {
  it('produces all sections in order from a sample session', () => {
    const m = buildMemo(SESSION);
    expect(m.heading.title).toMatch(/MEMORANDUM/i);
    expect(m.heading.caption).toBe('CASE NO. 2026-CV-0914');
    expect(m.heading.re).toContain(CONTRACT_EMAIL_QUESTION.replace(/\?$/, ''));
    expect(m.questionPresented.length).toBeGreaterThan(0);
    expect(m.briefAnswer.length).toBeGreaterThan(0);
    expect(m.statementOfFacts.length).toBeGreaterThan(0);
    // One finding per surfaced citation.
    expect(m.findings).toHaveLength(2);
    expect(m.discussion.issue).toBe(m.questionPresented);
    expect(m.discussion.rule.length).toBeGreaterThan(0);
    expect(m.conclusion.length).toBeGreaterThan(0);
    // Appendix carries every cited passage, verbatim.
    expect(m.appendix).toHaveLength(2);
    expect(m.appendix[0]?.quote).toBe(CONTRACT_CLAUSE_CITATION.text);
    expect(m.appendix[1]?.quote).toBe(EMAIL_ADMISSION_CITATION.text);
  });

  it('emits a contradiction with the anchor + BOTH conflicting verbatim quotes', () => {
    const m = buildMemo(SESSION);
    expect(m.contradictions).toHaveLength(1);
    const c = m.contradictions[0]!;
    expect(c.anchor).toBe(CONTRACT_EMAIL_ANCHOR);
    expect(c.crossDocument).toBe(true);
    expect(c.primary.quote).toBe(CONTRACT_CLAUSE_CITATION.text);
    expect(c.counter.quote).toBe(EMAIL_ADMISSION_CITATION.text);
    expect(c.primary.page).toBe(7);
    expect(c.counter.page).toBe(1);
    expect(c.nature.length).toBeGreaterThan(0);
  });

  it('each finding maps to a cited passage with doc+page+verbatim quote', () => {
    const m = buildMemo(SESSION);
    expect(m.findings[0]?.citation.documentId).toBe(CONTRACT_CLAUSE_CITATION.documentId);
    expect(m.findings[0]?.citation.page).toBe(7);
    expect(m.findings[0]?.citation.quote).toBe(CONTRACT_CLAUSE_CITATION.text);
  });

  it('is deterministic — identical input yields identical output', () => {
    expect(buildMemo(SESSION)).toEqual(buildMemo(SESSION));
    expect(memoToMarkdown(buildMemo(SESSION))).toBe(memoToMarkdown(buildMemo(SESSION)));
  });

  it('handles an empty/no-source session gracefully', () => {
    const empty: MemoSession = {
      question: '',
      caption: '',
      citations: [],
      primaryId: null,
      contradiction: false,
      anchor: null,
      hops: [],
    };
    const m = buildMemo(empty);
    expect(m.findings).toHaveLength(0);
    expect(m.contradictions).toHaveLength(0);
    expect(m.appendix).toHaveLength(0);
    // Still renders without throwing.
    expect(() => memoToMarkdown(m)).not.toThrow();
  });
});

describe('memoToMarkdown', () => {
  it('contains the verbatim quotes and the anchor', () => {
    const md = memoToMarkdown(buildMemo(SESSION));
    expect(md).toContain(CONTRACT_EMAIL_ANCHOR);
    expect(md).toContain(CONTRACT_CLAUSE_CITATION.text);
    expect(md).toContain(EMAIL_ADMISSION_CITATION.text);
    // Section headings present, in order.
    const order = [
      '# ',
      '## Question Presented',
      '## Brief Answer',
      '## Statement of Facts',
      '## Findings',
      '## Contradictions / Discrepancies',
      '## Discussion',
      '## Conclusion',
      '## Appendix',
    ];
    let cursor = -1;
    for (const heading of order) {
      const idx = md.indexOf(heading, cursor + 1);
      expect(idx, `heading "${heading}" should appear in order`).toBeGreaterThan(cursor);
      cursor = idx;
    }
  });

  it('quotes the two conflicting passages under a Discrepancy with Primary/Counter labels', () => {
    const md = memoToMarkdown(buildMemo(SESSION));
    expect(md).toMatch(/Discrepancy 1 — Anchor: §4\.2 Subcontracting/);
    expect(md).toContain('**Primary — Master Services Agreement, p. 7:**');
    expect(md).toContain('**Counter — Email Thread — Contractor Correspondence, p. 1:**');
  });
});
