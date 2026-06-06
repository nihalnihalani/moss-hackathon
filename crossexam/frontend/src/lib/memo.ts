/**
 * memo.ts — KILLER FEATURE A: Export to Legal Memo.
 *
 * `buildMemo(session) -> MemoModel` is a PURE, DETERMINISTIC transform of the
 * CrossExam conversation state into a structured legal memorandum. No network,
 * no randomness, no Date.now in the model body — the same session always yields
 * the same memo. Quotes are carried VERBATIM from the cited citations; they are
 * never paraphrased.
 *
 * Sections, in order:
 *   Heading/Caption → Question Presented → Brief Answer → Statement of Facts →
 *   Findings (each → cited passage doc+page+quote) →
 *   Contradictions/Discrepancies (anchor + the two conflicting quotes + nature) →
 *   Discussion (IRAC) → Conclusion → Appendix (cited passages).
 *
 * `memoToMarkdown(model) -> string` renders the model to GitHub-flavored
 * Markdown for the "Download Markdown" path. The print-to-PDF path renders the
 * same model via the MemoSheet React component + @media print CSS.
 *
 * Guarded LLM enhance: `enhanceMemo` is a no-op stub by default (offline,
 * deterministic). It is structured so a future LLM pass could rewrite ONLY the
 * prose fields and return the SAME shape; quotes/pages/doc-names stay byte-
 * identical. With no key present it returns the input unchanged.
 */

import type { Citation, HopTrace } from '../types';

/** The slice of session state the memo is built from. All fields are read-only. */
export interface MemoSession {
  /** The question under examination (the user's last ask). */
  question: string;
  /** The agent's spoken answer caption for this turn. */
  caption: string;
  /** Every citation surfaced this session (across docs/pages). */
  citations: readonly Citation[];
  /** The primary citation id (the claim/clause under examination), or null. */
  primaryId: string | null;
  /** Whether the surfaced citations conflict. */
  contradiction: boolean;
  /** The shared anchor the conflict hangs off (e.g. "§4.2 Subcontracting"). */
  anchor: string | null;
  /** The agentic decomposition trail ("how I found this"). */
  hops: readonly HopTrace[];
}

/** A cited passage: document, page, and the VERBATIM quoted text. */
export interface MemoCitation {
  /** Citation id (stable, used as the appendix anchor). */
  id: string;
  /** Document id the passage came from. */
  documentId: string;
  /** Human document title (falls back to the id). */
  documentTitle: string;
  /** 1-based page number. */
  page: number;
  /** The VERBATIM quoted text — never paraphrased. */
  quote: string;
  /** Grounded-confidence in [0,1] when a faithfulness check ran, else null. */
  grounded: number | null;
}

/** A finding: a one-line assertion backed by exactly one cited passage. */
export interface MemoFinding {
  /** Plain-English statement of what the passage establishes. */
  statement: string;
  /** The cited passage backing the finding. */
  citation: MemoCitation;
}

/** A contradiction/discrepancy: an anchor + two conflicting quotes + its nature. */
export interface MemoContradiction {
  /** The shared anchor the conflict hangs off, or null for an unanchored conflict. */
  anchor: string | null;
  /** Whether the two sources are different documents. */
  crossDocument: boolean;
  /** The primary side (claim/clause under examination). */
  primary: MemoCitation;
  /** The counter side (the source that conflicts with the primary). */
  counter: MemoCitation;
  /** One-line plain-English statement of the nature of the conflict. */
  nature: string;
}

/** The structured memorandum. Prose fields are the only LLM-rewritable surface. */
export interface MemoModel {
  /** ── Heading / Caption ── */
  heading: {
    /** Fixed document title. */
    title: string;
    /** Case caption line. */
    caption: string;
    /** "RE:" subject line (derived from the question). */
    re: string;
  };
  /** ── Question Presented ── (prose) */
  questionPresented: string;
  /** ── Brief Answer ── (prose) */
  briefAnswer: string;
  /** ── Statement of Facts ── (prose paragraphs) */
  statementOfFacts: string[];
  /** ── Findings ── each backed by a cited passage. */
  findings: MemoFinding[];
  /** ── Contradictions / Discrepancies ── */
  contradictions: MemoContradiction[];
  /** ── Discussion (IRAC) ── */
  discussion: {
    issue: string;
    rule: string;
    application: string;
    conclusion: string;
  };
  /** ── Conclusion ── (prose) */
  conclusion: string;
  /** ── Appendix ── every cited passage, in order, verbatim. */
  appendix: MemoCitation[];
}

const MEMO_TITLE = 'CONFIDENTIAL MEMORANDUM — CrossExam Evidentiary Findings';
const CASE_CAPTION = 'CASE NO. 2026-CV-0914';

/** Map a Citation into a MemoCitation (verbatim quote, no transformation). */
function toMemoCitation(c: Citation): MemoCitation {
  return {
    id: c.id,
    documentId: c.documentId,
    documentTitle: c.documentTitle ?? c.documentId,
    page: c.bbox.page,
    quote: c.text,
    grounded: c.faithfulness ? c.faithfulness.score : null,
  };
}

/** Trim trailing punctuation/whitespace so a derived "RE:" line reads cleanly. */
function trimQuestion(q: string): string {
  return q.trim().replace(/\s+/g, ' ').replace(/[?.\s]+$/, '');
}

/**
 * Build the deterministic memo model from the session. Pure: no I/O, no clock,
 * no randomness. Verbatim quotes are carried through untouched.
 */
export function buildMemo(session: MemoSession): MemoModel {
  const citations = [...session.citations];
  const question = trimQuestion(session.question);

  const primary =
    citations.find((c) => c.id === session.primaryId) ?? citations[0] ?? null;

  // ── Findings: one per surfaced citation, in surfaced order. The statement is
  // a neutral, deterministic framing; the verbatim quote lives in the citation.
  const findings: MemoFinding[] = citations.map((c) => {
    const mc = toMemoCitation(c);
    return {
      statement: `The ${mc.documentTitle} (p. ${mc.page}) establishes the following passage of record.`,
      citation: mc,
    };
  });

  // ── Contradictions: pair the primary with each conflicting counter citation.
  const contradictions: MemoContradiction[] = [];
  if (session.contradiction && primary && citations.length >= 2) {
    const primaryMc = toMemoCitation(primary);
    for (const c of citations) {
      if (c.id === primary.id) continue;
      const counterMc = toMemoCitation(c);
      const crossDocument = counterMc.documentId !== primaryMc.documentId;
      const where = crossDocument
        ? `across two documents (${primaryMc.documentTitle} vs. ${counterMc.documentTitle})`
        : `within ${primaryMc.documentTitle}`;
      const anchorPhrase = session.anchor ? ` on ${session.anchor}` : '';
      contradictions.push({
        anchor: session.anchor,
        crossDocument,
        primary: primaryMc,
        counter: counterMc,
        nature: `The cited passages directly conflict${anchorPhrase}, ${where}.`,
      });
    }
  }

  // ── Question Presented (prose).
  const questionPresented = question
    ? `Whether, on the present record, ${lowerFirst(question)}.`
    : 'Whether the cited record supports the position advanced on examination.';

  // ── Brief Answer (prose). Leads with the contradiction posture when present.
  const briefAnswer = contradictions.length
    ? `The record is internally inconsistent. ${
        contradictions[0]?.crossDocument
          ? 'Two documents in evidence conflict'
          : 'The record conflicts with itself'
      }${session.anchor ? ` on ${session.anchor}` : ''}, as set out under Contradictions below.`
    : primary
      ? `Yes, in part. The cited passage from ${primary.documentTitle ?? primary.documentId} (p. ${primary.bbox.page}) supports the position, as set out below.`
      : 'No grounded source was surfaced for this question on the present record.';

  // ── Statement of Facts (prose paragraphs). Names the documents in evidence
  // and the agent's spoken summary, without paraphrasing any quoted text.
  const docTitles = uniqueDocTitles(citations);
  const statementOfFacts: string[] = [];
  if (docTitles.length) {
    statementOfFacts.push(
      `The following documents are in evidence: ${joinList(docTitles)}.`,
    );
  } else {
    statementOfFacts.push('No documents have been surfaced into evidence for this question.');
  }
  if (session.caption.trim()) {
    statementOfFacts.push(`On examination, the assistant summarized: "${session.caption.trim()}"`);
  }

  // ── Discussion (IRAC).
  const issue = questionPresented;
  const rule =
    'A finding of record must be grounded in a cited passage; where two grounded passages conflict, the discrepancy is material and must be resolved on the merits.';
  const application = contradictions.length
    ? `Here, the cited passages conflict${
        session.anchor ? ` on ${session.anchor}` : ''
      }. The primary passage and the counter passage cannot both be true as written; the conflict is set out verbatim under Contradictions.`
    : findings.length
      ? 'Here, each finding is grounded in a cited passage reproduced verbatim in the Appendix; no internal conflict was detected on the present record.'
      : 'Here, no grounded passage was surfaced, so no finding can be made.';
  const conclusionIrac = contradictions.length
    ? 'The discrepancy is material and warrants resolution before the record can be relied upon.'
    : findings.length
      ? 'The cited passages support the findings stated above.'
      : 'No finding can be supported on the present record.';

  const conclusion = conclusionIrac;

  const re = question ? `RE: ${question}` : 'RE: Evidentiary findings on the present record';

  // ── Appendix: every cited passage, in surfaced order, verbatim.
  const appendix = citations.map(toMemoCitation);

  return {
    heading: { title: MEMO_TITLE, caption: CASE_CAPTION, re },
    questionPresented,
    briefAnswer,
    statementOfFacts,
    findings,
    contradictions,
    discussion: { issue, rule, application, conclusion: conclusionIrac },
    conclusion,
    appendix,
  };
}

/** Render a MemoModel to GitHub-flavored Markdown. Deterministic. */
export function memoToMarkdown(model: MemoModel): string {
  const lines: string[] = [];
  const h = model.heading;

  lines.push(`# ${h.title}`);
  lines.push('');
  lines.push(`**${h.caption}**`);
  lines.push('');
  lines.push(`**${h.re}**`);
  lines.push('');

  lines.push('## Question Presented');
  lines.push('');
  lines.push(model.questionPresented);
  lines.push('');

  lines.push('## Brief Answer');
  lines.push('');
  lines.push(model.briefAnswer);
  lines.push('');

  lines.push('## Statement of Facts');
  lines.push('');
  for (const p of model.statementOfFacts) {
    lines.push(p);
    lines.push('');
  }

  lines.push('## Findings');
  lines.push('');
  if (model.findings.length === 0) {
    lines.push('_No grounded findings on the present record._');
    lines.push('');
  } else {
    model.findings.forEach((f, i) => {
      lines.push(`### Finding ${i + 1}`);
      lines.push('');
      lines.push(f.statement);
      lines.push('');
      lines.push(citationBlock(f.citation));
      lines.push('');
    });
  }

  lines.push('## Contradictions / Discrepancies');
  lines.push('');
  if (model.contradictions.length === 0) {
    lines.push('_No contradictions detected on the present record._');
    lines.push('');
  } else {
    model.contradictions.forEach((c, i) => {
      const header = c.anchor
        ? `### Discrepancy ${i + 1} — Anchor: ${c.anchor}`
        : `### Discrepancy ${i + 1}`;
      lines.push(header);
      lines.push('');
      lines.push(`_${c.nature}_`);
      lines.push('');
      lines.push(`**Primary — ${c.primary.documentTitle}, p. ${c.primary.page}:**`);
      lines.push('');
      lines.push(`> ${c.primary.quote}`);
      lines.push('');
      lines.push(`**Counter — ${c.counter.documentTitle}, p. ${c.counter.page}:**`);
      lines.push('');
      lines.push(`> ${c.counter.quote}`);
      lines.push('');
    });
  }

  lines.push('## Discussion');
  lines.push('');
  lines.push(`**Issue.** ${model.discussion.issue}`);
  lines.push('');
  lines.push(`**Rule.** ${model.discussion.rule}`);
  lines.push('');
  lines.push(`**Application.** ${model.discussion.application}`);
  lines.push('');
  lines.push(`**Conclusion.** ${model.discussion.conclusion}`);
  lines.push('');

  lines.push('## Conclusion');
  lines.push('');
  lines.push(model.conclusion);
  lines.push('');

  lines.push('## Appendix — Cited Passages');
  lines.push('');
  if (model.appendix.length === 0) {
    lines.push('_No cited passages._');
    lines.push('');
  } else {
    model.appendix.forEach((c, i) => {
      lines.push(`**[A${i + 1}] ${c.documentTitle}, p. ${c.page}** (\`${c.id}\`)`);
      lines.push('');
      lines.push(`> ${c.quote}`);
      lines.push('');
    });
  }

  return lines.join('\n').replace(/\n{3,}/g, '\n\n').trimEnd() + '\n';
}

function citationBlock(c: MemoCitation): string {
  const groundedSuffix = c.grounded != null ? ` _(grounded ${c.grounded.toFixed(2)})_` : '';
  return `**${c.documentTitle}, p. ${c.page}:**${groundedSuffix}\n\n> ${c.quote}`;
}

function lowerFirst(s: string): string {
  return s.length ? s[0]!.toLowerCase() + s.slice(1) : s;
}

function uniqueDocTitles(citations: readonly Citation[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const c of citations) {
    const title = c.documentTitle ?? c.documentId;
    if (!seen.has(title)) {
      seen.add(title);
      out.push(title);
    }
  }
  return out;
}

function joinList(items: string[]): string {
  if (items.length <= 1) return items[0] ?? '';
  if (items.length === 2) return `${items[0]} and ${items[1]}`;
  return `${items.slice(0, -1).join(', ')}, and ${items[items.length - 1]}`;
}

/**
 * Guarded LLM enhance — NO-OP STUB.
 *
 * Default path is fully deterministic + offline; this returns the model
 * unchanged. The signature is shaped so a future LLM pass could rewrite ONLY the
 * prose fields (questionPresented, briefAnswer, statementOfFacts, discussion,
 * conclusion) and return the SAME MemoModel shape, while quotes/pages/doc-names
 * (findings[].citation, contradictions[].primary/counter, appendix) stay
 * byte-identical. Malformed enhancer output should be discarded by callers in
 * favor of the deterministic model.
 */
export function enhanceMemo(model: MemoModel): MemoModel {
  return model;
}
