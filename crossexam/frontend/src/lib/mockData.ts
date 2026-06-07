/**
 * mockData.ts — the scripted depth-v2 demo, runnable with zero backend.
 *
 * The mock citations MIRROR the real backend fixture
 * (crossexam/backend/fixtures/sample_chunks.json) EXACTLY — same ids, text,
 * page, bbox and quads — so unchecking "Force mock" and switching to the live
 * backend snaps the same boxes onto the same glyphs. The source of truth is the
 * fixture; this file must not drift from it.
 *
 * Showcases all five features without a backend:
 *   1. Multi-doc + multi-hop CONTRADICTION: a box in the deposition (p12), then a
 *      conflicting box in the exhibit-visitor-log doc (p2), with a hops trail.
 *   2. Quad highlights: citations carry per-line `quads[]` so the amber
 *      highlight hugs real text across wraps (union `bbox` drives jump + label).
 *   3. Scanned source: the handwritten field-notes exhibit is OCR'd -> the
 *      `scanned: true` badge. The visitor log is born-digital (NOT scanned).
 *   4. Meeting mode: a proactive citation tagged with its `speaker`.
 *   5. Memory: a recall chip ("as we saw on page 12") instead of a re-snap.
 *
 * IMPORTANT — page numbers + point bboxes match the real PDFs served at
 * /public/sample-deposition.pdf and /public/exhibit-visitor-log.pdf (and the
 * fixture chunks), since `bbox.page` is both the page the canvas navigates to and
 * the page the box is drawn on. bbox.ts reads the TRUE page dimensions from the
 * rendered viewport; the US-Letter constants below are the placeholder fallback.
 *
 * Coordinates are top-left origin, y downward, PDF points (see types.ts BBox).
 */

import type { BBox, Citation, HopTrace, MemoryRef, Speaker } from '../types';

/** Intrinsic size of the placeholder/demo page, in PDF points (US Letter). */
export const DEMO_PAGE_WIDTH_PT = 612;
export const DEMO_PAGE_HEIGHT_PT = 792;

/** Corpus size, drives the "found in 912 pages" chip and the page-jump animation. */
export const DEMO_TOTAL_PAGES = 912;

/** Document ids -> PDF urls. The doc switcher uses this mapping (feat 1). */
export const DOC_DEPOSITION = 'deposition-holloway';
export const DOC_EXHIBIT = 'exhibit-visitor-log';
export const DOC_FIELD_NOTES = 'exhibit-field-notes';
/** KILLER FEATURE B: the contract-vs-email cross-doc pair. */
export const DOC_CONTRACT = 'contract-msa';
export const DOC_EMAIL = 'email-thread';
/**
 * LIVE real-doc kit (crossexam-live Moss index). Real, public, born-digital NTSB
 * accident report. The slug is the single documentId used everywhere: the chunk
 * id_prefix (ntsb-asiana-214-pN-lM), the /public/<slug>.pdf served below, the
 * --document-id stamped at parse time, and this DOC_URLS/DOC_TITLES key.
 */
export const DOC_NTSB_ASIANA = 'ntsb-asiana-214';

/**
 * Gates deposition kit (crossexam-gates Moss index). Three documents built from
 * the 1998 US v. Microsoft deposition transcripts (Techrights born-digital
 * reproductions) and the 1995 Internet Tidal Wave memo (Gov. Exhibit 345).
 * Hero contradiction: Gates says "No" to being worried about Netscape in April
 * 1995 (gates-depo-aug27 p.47-48), while the memo (exhibit-internet-tidal-wave
 * p.2) shows "I too am very worried" and "70% usage share / commoditize the
 * underlying operating system".
 */
export const DOC_GATES_AUG27 = 'gates-depo-aug27';
export const DOC_GATES_AUG28 = 'gates-depo-aug28';
export const DOC_INTERNET_TIDAL_WAVE = 'exhibit-internet-tidal-wave';

/** Titles mirror the fixture's `documentTitle` for each chunk's documentId. */
export const DOC_TITLES: Readonly<Record<string, string>> = {
  [DOC_DEPOSITION]: 'Deposition of Raymond T. Holloway',
  [DOC_EXHIBIT]: 'Exhibit 14: Security Desk Visitor Log',
  [DOC_FIELD_NOTES]: 'Exhibit 22: Handwritten Field Notes (scanned)',
  [DOC_CONTRACT]: 'Master Services Agreement',
  [DOC_EMAIL]: 'Email Thread — Contractor Correspondence',
  [DOC_NTSB_ASIANA]: 'Asiana Airlines Flight 214 Accident Report (NTSB/AAR-14/01)',
  [DOC_GATES_AUG27]: 'Gates Deposition — Aug 27, 1998 (US v. Microsoft)',
  [DOC_GATES_AUG28]: 'Gates Deposition — Aug 28, 1998 (US v. Microsoft)',
  [DOC_INTERNET_TIDAL_WAVE]: 'Internet Tidal Wave — Bill Gates Memo, May 26, 1995',
};

/**
 * documentId -> public PDF url. Selecting a tab jumps the canvas to this doc.
 * The deposition + visitor-log PDFs ship in /public; the scanned field-notes
 * exhibit has no rendered PDF, so PdfCanvas draws its placeholder page (the
 * scanned badge + box still read correctly for the OCR beat).
 */
export const DOC_URLS: Readonly<Record<string, string>> = {
  [DOC_DEPOSITION]: '/sample-deposition.pdf',
  [DOC_EXHIBIT]: '/exhibit-visitor-log.pdf',
  [DOC_CONTRACT]: '/contract-msa.pdf',
  [DOC_EMAIL]: '/email-thread.pdf',
  [DOC_NTSB_ASIANA]: '/ntsb-asiana-214.pdf',
  [DOC_GATES_AUG27]: '/gates-depo-aug27.pdf',
  [DOC_GATES_AUG28]: '/gates-depo-aug28.pdf',
  [DOC_INTERNET_TIDAL_WAVE]: '/exhibit-internet-tidal-wave.pdf',
};

/**
 * The warehouse-admission line (deposition, page 12). Mirrors fixture chunk
 * `pdf-p12-l1` exactly: three wrapped lines -> three quads; `bbox` is their union.
 */
const warehouseQuads: BBox[] = [
  {
    page: 12,
    x0: 72.0,
    y0: 123.94,
    x1: 510.0,
    y1: 133.94,
    page_width: DEMO_PAGE_WIDTH_PT,
    page_height: DEMO_PAGE_HEIGHT_PT,
  },
  {
    page: 12,
    x0: 72.0,
    y0: 136.94,
    x1: 522.0,
    y1: 146.94,
    page_width: DEMO_PAGE_WIDTH_PT,
    page_height: DEMO_PAGE_HEIGHT_PT,
  },
  {
    page: 12,
    x0: 72.0,
    y0: 149.94,
    x1: 282.0,
    y1: 159.94,
    page_width: DEMO_PAGE_WIDTH_PT,
    page_height: DEMO_PAGE_HEIGHT_PT,
  },
];

const warehouseBBox: BBox = {
  page: 12,
  x0: 72.0,
  y0: 123.94,
  x1: 522.0,
  y1: 159.94,
  page_width: DEMO_PAGE_WIDTH_PT,
  page_height: DEMO_PAGE_HEIGHT_PT,
};

/**
 * The contradicting visitor-log entry — in the SEPARATE exhibit document, on
 * PAGE 2. Mirrors fixture chunk `exhibit-visitor-log-p2-l1` exactly: born-digital
 * (NOT scanned), four wrapped lines -> four quads; `bbox` is their union.
 */
const visitorLogQuads: BBox[] = [
  {
    page: 2,
    x0: 72.0,
    y0: 123.94,
    x1: 528.0,
    y1: 133.94,
    page_width: DEMO_PAGE_WIDTH_PT,
    page_height: DEMO_PAGE_HEIGHT_PT,
  },
  {
    page: 2,
    x0: 72.0,
    y0: 136.94,
    x1: 534.0,
    y1: 146.94,
    page_width: DEMO_PAGE_WIDTH_PT,
    page_height: DEMO_PAGE_HEIGHT_PT,
  },
  {
    page: 2,
    x0: 72.0,
    y0: 149.94,
    x1: 534.0,
    y1: 159.94,
    page_width: DEMO_PAGE_WIDTH_PT,
    page_height: DEMO_PAGE_HEIGHT_PT,
  },
  {
    page: 2,
    x0: 72.0,
    y0: 162.94,
    x1: 144.0,
    y1: 172.94,
    page_width: DEMO_PAGE_WIDTH_PT,
    page_height: DEMO_PAGE_HEIGHT_PT,
  },
];

const visitorLogBBox: BBox = {
  page: 2,
  x0: 72.0,
  y0: 123.94,
  x1: 534.0,
  y1: 172.94,
  page_width: DEMO_PAGE_WIDTH_PT,
  page_height: DEMO_PAGE_HEIGHT_PT,
};

/**
 * The scanned, handwritten field-notes exhibit. Mirrors fixture chunk
 * `exhibit-field-notes-p1-l0` exactly: a single OCR'd quad, `scanned: true`.
 * Used for the meeting-mode proactive beat so the scanned badge has a REAL source.
 */
const fieldNotesQuads: BBox[] = [
  {
    page: 1,
    x0: 72.0,
    y0: 120.0,
    x1: 540.0,
    y1: 148.0,
    page_width: DEMO_PAGE_WIDTH_PT,
    page_height: DEMO_PAGE_HEIGHT_PT,
  },
];

const fieldNotesBBox: BBox = {
  page: 1,
  x0: 72.0,
  y0: 120.0,
  x1: 540.0,
  y1: 148.0,
  page_width: DEMO_PAGE_WIDTH_PT,
  page_height: DEMO_PAGE_HEIGHT_PT,
};

/**
 * The primary answer the agent speaks first (deposition, page 12).
 * Mirrors fixture chunk `pdf-p12-l1` (id/text/page/bbox/quads).
 */
export const ANSWER_CITATION: Citation = {
  id: 'pdf-p12-l1',
  text:
    'Q. Where were you on the night of the 14th? A. I was at the Harbor Street ' +
    'warehouse from approximately 9:00 p.m. until well past midnight, conducting ' +
    'the inventory count with Mr. Reyes.',
  bbox: warehouseBBox,
  quads: warehouseQuads,
  confidence: 0.9445,
  score: 0.91,
  latencyMs: 7,
  pagesSearched: DEMO_TOTAL_PAGES,
  faithfulness: { supported: true, score: 0.99, method: 'nli' },
  documentId: DOC_DEPOSITION,
  documentTitle: DOC_TITLES[DOC_DEPOSITION],
};

/**
 * The follow-up contradiction — surfaced from the EXHIBIT (a different document),
 * the downtown visitor log, PAGE 2. This is the cross-doc conflict. Mirrors
 * fixture chunk `exhibit-visitor-log-p2-l1` (id/text/page/bbox/quads). The log is
 * born-digital, so it is NOT scanned.
 */
export const CONTRADICTION_CITATION: Citation = {
  id: 'exhibit-visitor-log-p2-l1',
  text:
    'Visitor log entry: R. Holloway signed the downtown security desk visitor log ' +
    'at 9:40 p.m. on the 14th — two miles from the Harbor Street warehouse — and ' +
    'signed out at 11:20 p.m., having attended the quarterly budget meeting on the ' +
    'ninth floor.',
  bbox: visitorLogBBox,
  quads: visitorLogQuads,
  confidence: 0.943,
  score: 0.88,
  latencyMs: 6,
  pagesSearched: DEMO_TOTAL_PAGES,
  faithfulness: { supported: true, score: 0.96, method: 'nli' },
  documentId: DOC_EXHIBIT,
  documentTitle: DOC_TITLES[DOC_EXHIBIT],
};

/**
 * The PROACTIVE / MEETING-mode beat. A speaker utters a CLAIM aloud and the
 * co-pilot snaps the SCANNED handwritten field-notes exhibit UNPROMPTED — tagged
 * with the speaker who triggered it. Mirrors fixture chunk
 * `exhibit-field-notes-p1-l0` (the scanned/OCR source for the `scanned: true` badge).
 */
export const PROACTIVE_CITATION: Citation = {
  id: 'exhibit-field-notes-p1-l0',
  text: 'FIELD NOTES (scanned, handwritten)',
  bbox: fieldNotesBBox,
  quads: fieldNotesQuads,
  confidence: 0.6544,
  score: 0.66,
  latencyMs: 9,
  pagesSearched: DEMO_TOTAL_PAGES,
  faithfulness: { supported: true, score: 0.82, method: 'nli' },
  documentId: DOC_FIELD_NOTES,
  documentTitle: DOC_TITLES[DOC_FIELD_NOTES],
  scanned: true,
};

/** Speakers for meeting mode (feat 4). */
export const SPEAKER_COUNSEL: Speaker = { id: 'spk_1', label: 'Counsel' };
export const SPEAKER_WITNESS: Speaker = { id: 'spk_2', label: 'Witness' };

/**
 * The hops trail (feat 1). These are the EXACT sub-queries the backend's
 * QueryDecomposer.decompose() emits for DEMO_CONTRADICTION_QUESTION — the
 * principled (core-claim, contradicting-evidence) pair derived from the
 * question's subject+predicate, NOT hand-written natural-language fiction. The
 * backend strips the contradiction framing down to the temporal-anchor topic
 * ("night 14th") and prefixes "evidence that contradicts " for the second hop;
 * the test `test_published_hops_equal_decompose_output` asserts the live trail
 * equals this. The citationIds show how each hop resolves: the core claim to the
 * deposition admission (primary), the contradicting hop to the cross-doc exhibit.
 */
export const CONTRADICTION_HOPS: HopTrace[] = [
  {
    subQuery: 'night 14th',
    citationIds: [ANSWER_CITATION.id],
  },
  {
    subQuery: 'evidence that contradicts night 14th',
    citationIds: [CONTRADICTION_CITATION.id],
  },
];

/**
 * The memory recall (feat 5). On a later turn the agent references the
 * already-surfaced warehouse admission with a recall note instead of re-snapping.
 */
export const MEMORY_RECALL: MemoryRef = {
  kind: 'recall',
  citationId: ANSWER_CITATION.id,
  documentId: ANSWER_CITATION.documentId,
  page: ANSWER_CITATION.bbox.page,
  note: `as we saw on page ${ANSWER_CITATION.bbox.page}`,
};

/** The streamed answer caption that plays alongside the first snap. */
export const ANSWER_TRANSCRIPT =
  `Yes — on page ${warehouseBBox.page} the witness admits being at the warehouse on the night of ` +
  'the 14th, staying until well past midnight.';

/**
 * The user question that kicks off the demo sequence. This FIRST ask is a plain
 * single-hop admission question — it snaps the deposition warehouse admission
 * (pdf-p12-l1) with no contradiction yet.
 */
export const DEMO_QUESTION =
  'Did the witness admit they were at the warehouse on the night of the 14th?';

/**
 * The CANONICAL contradiction follow-up — the EXACT string the backend test
 * (tests/test_multihop.py DEMO_CONTRADICTION_Q) and eval query
 * (q-contradiction-cross-doc) use, so mock == live. On the live backend this
 * string (a) routes multi-hop (is_multihop_question == True via the
 * "contradict" cue), (b) decomposes to CONTRADICTION_HOPS verbatim, and
 * (c) selects primary == pdf-p12-l1 (deposition) with the cross-document counter
 * exhibit-visitor-log-p2-l1 — contradiction:true, crossDocument:true. It does
 * NOT echo "Harbor Street": robustness comes from the principled detector, not a
 * string match. Asking this against the live agent reproduces the scripted beat.
 */
export const DEMO_CONTRADICTION_QUESTION =
  'Did the witness contradict himself about the night of the 14th?';

/** The contradiction follow-up caption. */
export const CONTRADICTION_TRANSCRIPT =
  'But there is a contradiction across documents: the downtown visitor log exhibit ' +
  'recorded the witness signing in at 9:40 p.m. that night — two miles away.';

/** The MEMORY-recall caption (feat 5). */
export const MEMORY_TRANSCRIPT =
  'That timeline matters: the warehouse admission still stands — ' +
  `${MEMORY_RECALL.note} — so the conflict holds.`;

/**
 * The CLAIM the witness speaks aloud that the co-pilot answers UNPROMPTED. Shown
 * as the heard line above the proactively-surfaced citation.
 */
export const PROACTIVE_CLAIM =
  'I was nowhere near downtown that whole night — I never left the warehouse.';

/** The caption the co-pilot speaks when it surfaces the proactive citation. */
export const PROACTIVE_TRANSCRIPT =
  'Flagging this unprompted: a scanned, handwritten field-notes exhibit also bears on ' +
  'that night — surfacing it so the record is complete.';

/**
 * A query that has NO grounded source in the corpus. The co-pilot stays silent
 * rather than fabricate a box — surfaced as the honest empty state.
 */
export const NOT_FOUND_CLAIM =
  'Was there ever any mention of a second vehicle at the scene?';

/** Caption shown alongside the honest-silence (not_found) beat. */
export const NOT_FOUND_TRANSCRIPT =
  'I could not find that in the document, so I am staying silent rather than guess.';

/* ============================================================================
 * KILLER FEATURE B — CONTRACT vs EMAIL cross-document breach.
 *
 * The §4.2 Subcontracting beat: the contract clause (PRIMARY, contract-msa p.7)
 * forbids subcontracting without prior written consent; the email (COUNTER,
 * email-thread p.1) admits handing the work to Acme Labs with no sign-off. Both
 * citations MIRROR fixture chunks `contract-msa-p7-l1` and `email-thread-p1-l2`
 * EXACTLY (id/text/page/bbox/quads), so mock == live snaps onto real glyphs.
 * ========================================================================== */

/** §4.2 clause, contract page 7. Mirrors fixture chunk `contract-msa-p7-l1`. */
const contractSubcontractQuads: BBox[] = [
  { page: 7, x0: 72.0, y0: 123.94, x1: 486.0, y1: 133.94, page_width: DEMO_PAGE_WIDTH_PT, page_height: DEMO_PAGE_HEIGHT_PT },
  { page: 7, x0: 72.0, y0: 136.94, x1: 504.0, y1: 146.94, page_width: DEMO_PAGE_WIDTH_PT, page_height: DEMO_PAGE_HEIGHT_PT },
  { page: 7, x0: 72.0, y0: 149.94, x1: 528.0, y1: 159.94, page_width: DEMO_PAGE_WIDTH_PT, page_height: DEMO_PAGE_HEIGHT_PT },
  { page: 7, x0: 72.0, y0: 162.94, x1: 504.0, y1: 172.94, page_width: DEMO_PAGE_WIDTH_PT, page_height: DEMO_PAGE_HEIGHT_PT },
  { page: 7, x0: 72.0, y0: 175.94, x1: 132.0, y1: 185.94, page_width: DEMO_PAGE_WIDTH_PT, page_height: DEMO_PAGE_HEIGHT_PT },
];

/**
 * The PRIMARY citation for the contract-vs-email beat — the §4.2 clause that the
 * email breaches. Mirrors fixture chunk `contract-msa-p7-l1`.
 */
export const CONTRACT_CLAUSE_CITATION: Citation = {
  id: 'contract-msa-p7-l1',
  text:
    'Section 4.2 Subcontracting. Contractor shall not delegate, assign, or ' +
    'subcontract any of the Services, in whole or in part, to any third party ' +
    'without the prior written consent of Client. Any purported subcontracting in ' +
    'violation of this Section 4.2 shall constitute a material breach of this Agreement.',
  bbox: { page: 7, x0: 72.0, y0: 123.94, x1: 528.0, y1: 185.94, page_width: DEMO_PAGE_WIDTH_PT, page_height: DEMO_PAGE_HEIGHT_PT },
  quads: contractSubcontractQuads,
  confidence: 0.9431,
  score: 0.92,
  latencyMs: 8,
  pagesSearched: DEMO_TOTAL_PAGES,
  faithfulness: { supported: true, score: 0.98, method: 'nli' },
  documentId: DOC_CONTRACT,
  documentTitle: DOC_TITLES[DOC_CONTRACT],
};

/** Acme admission, email page 1. Mirrors fixture chunk `email-thread-p1-l2`. */
const emailAdmissionQuads: BBox[] = [
  { page: 1, x0: 72.0, y0: 163.94, x1: 522.0, y1: 173.94, page_width: DEMO_PAGE_WIDTH_PT, page_height: DEMO_PAGE_HEIGHT_PT },
  { page: 1, x0: 72.0, y0: 176.94, x1: 534.0, y1: 186.94, page_width: DEMO_PAGE_WIDTH_PT, page_height: DEMO_PAGE_HEIGHT_PT },
  { page: 1, x0: 72.0, y0: 189.94, x1: 516.0, y1: 199.94, page_width: DEMO_PAGE_WIDTH_PT, page_height: DEMO_PAGE_HEIGHT_PT },
  { page: 1, x0: 72.0, y0: 202.94, x1: 528.0, y1: 212.94, page_width: DEMO_PAGE_WIDTH_PT, page_height: DEMO_PAGE_HEIGHT_PT },
  { page: 1, x0: 72.0, y0: 215.94, x1: 168.0, y1: 225.94, page_width: DEMO_PAGE_WIDTH_PT, page_height: DEMO_PAGE_HEIGHT_PT },
];

/**
 * The COUNTER citation — the email admission that breaches §4.2. This is the
 * cross-document conflict; the email is born-digital (NOT scanned). Mirrors
 * fixture chunk `email-thread-p1-l2`.
 */
export const EMAIL_ADMISSION_CITATION: Citation = {
  id: 'email-thread-p1-l2',
  text:
    'From: Dana Vance Subject: Re: Section 4.2 / Acme work order -- Just to keep ' +
    'you in the loop, we already handed the integration work off to Acme Labs last ' +
    'week so we could hit the deadline. I know we never got the formal sign-off from ' +
    "your side, but it was faster to just get them started. They're fully up and running now.",
  bbox: { page: 1, x0: 72.0, y0: 163.94, x1: 534.0, y1: 225.94, page_width: DEMO_PAGE_WIDTH_PT, page_height: DEMO_PAGE_HEIGHT_PT },
  quads: emailAdmissionQuads,
  confidence: 0.9465,
  score: 0.9,
  latencyMs: 6,
  pagesSearched: DEMO_TOTAL_PAGES,
  faithfulness: { supported: true, score: 0.97, method: 'nli' },
  documentId: DOC_EMAIL,
  documentTitle: DOC_TITLES[DOC_EMAIL],
};

/** The shared anchor naming the conflicting clause (drives the anchor banner). */
export const CONTRACT_EMAIL_ANCHOR = '§4.2 Subcontracting';

/** One-line plain-English statement of the breach (counsel-readable). */
export const CONTRACT_EMAIL_CONFLICT_NOTE =
  'The contract forbids subcontracting without prior written consent (a material ' +
  'breach), yet the email admits work was handed to Acme Labs with no sign-off.';

/** The user question that surfaces the contract-vs-email cross-doc breach. */
export const CONTRACT_EMAIL_QUESTION =
  'Does the email contradict the subcontracting clause in the contract?';

/** The caption the agent speaks when it surfaces the breach. */
export const CONTRACT_EMAIL_TRANSCRIPT =
  'Yes — Section 4.2 of the contract forbids subcontracting without prior written ' +
  'consent, but the email admits the integration work was handed to Acme Labs with ' +
  'no sign-off. That is a cross-document breach.';

/**
 * Hops for the contract-vs-email beat: the clause under examination, then the
 * contradicting admission. Anchored on the shared §4.2 subject.
 */
export const CONTRACT_EMAIL_HOPS: HopTrace[] = [
  { subQuery: 'subcontracting clause §4.2', citationIds: [CONTRACT_CLAUSE_CITATION.id] },
  {
    subQuery: 'evidence that contradicts subcontracting clause §4.2',
    citationIds: [EMAIL_ADMISSION_CITATION.id],
  },
];

/** Ordered list of demo citations for any UI that wants to step through them. */
export const DEMO_CITATIONS: readonly Citation[] = [
  ANSWER_CITATION,
  CONTRADICTION_CITATION,
  PROACTIVE_CITATION,
];

/**
 * Every citation the offline mock can ground a typed question against (the
 * deposition admission, the cross-doc visitor log, the scanned field notes, the
 * contract §4.2 clause, and the email admission). A Cmd+K query is scored
 * against these so the TYPED text drives the result, not a canned script.
 */
export const ALL_MOCK_CITATIONS: readonly Citation[] = [
  ANSWER_CITATION,
  CONTRADICTION_CITATION,
  PROACTIVE_CITATION,
  CONTRACT_CLAUSE_CITATION,
  EMAIL_ADMISSION_CITATION,
];

/** A spoken caption + an optional anchor to surface for a matched citation. */
export interface MockMatch {
  /** The best-matching citation for the query. */
  citation: Citation;
  /** A short grounded caption to speak alongside the snap. */
  caption: string;
}

/** English stop-words stripped before scoring so only content words count. */
const STOP_WORDS = new Set<string>([
  'a', 'an', 'and', 'any', 'are', 'as', 'at', 'be', 'been', 'but', 'by', 'did',
  'do', 'does', 'for', 'from', 'had', 'has', 'have', 'he', 'her', 'him', 'his',
  'i', 'if', 'in', 'into', 'is', 'it', 'its', 'me', 'my', 'no', 'not', 'of',
  'on', 'or', 'our', 'over', 'she', 'so', 'that', 'the', 'their', 'them',
  'then', 'there', 'they', 'this', 'to', 'up', 'us', 'was', 'we', 'were',
  'what', 'when', 'where', 'which', 'who', 'why', 'will', 'with', 'you', 'your',
  'about', 'ever', 'still', 'whole',
]);

/** Lowercase, split on non-word chars, drop stop-words + 1-char tokens. */
function tokenize(s: string): string[] {
  return s
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((t) => t.length > 1 && !STOP_WORDS.has(t));
}

/**
 * True when two content words refer to the same concept: an exact match, or one
 * is a stem/prefix of the other of length >= 4 (so "contract" matches
 * "contractor"/"contracting" and "subcontracting" matches "subcontract"). This
 * keeps the matcher robust to morphology without a real stemmer.
 */
function tokensRelated(a: string, b: string): boolean {
  if (a === b) return true;
  const [shorter, longer] = a.length <= b.length ? [a, b] : [b, a];
  return shorter.length >= 4 && longer.startsWith(shorter);
}

/**
 * Score a query against the mock corpus and return the BEST-matching citation
 * (with a grounded caption), or null when nothing clears the bar — the honest
 * "No grounded source" state. Scoring is a content-word overlap (with light
 * stemming) between the query and each citation's quoted text + title, so e.g. a
 * contract / subcontracting query surfaces the §4.2 clause rather than the
 * deposition, while a gibberish query matches nothing.
 *
 * Pure + deterministic so the mock `ask(q)` path is unit-testable.
 */
export function matchMockCitation(query: string): MockMatch | null {
  const qTokens = tokenize(query);
  if (qTokens.length === 0) return null;

  let best: { citation: Citation; score: number } | null = null;
  for (const citation of ALL_MOCK_CITATIONS) {
    const haystack = tokenize(`${citation.text} ${citation.documentTitle ?? ''}`);
    let score = 0;
    for (const qt of qTokens) {
      // Count a query word once if it relates to any word in the source.
      if (haystack.some((ht) => tokensRelated(qt, ht))) score += 1;
    }
    if (score === 0) continue;
    if (!best || score > best.score) best = { citation, score };
  }

  // Require at least two overlapping content words so a gibberish or off-corpus
  // query honestly yields not_found instead of a weak, misleading snap.
  if (!best || best.score < 2) return null;

  return { citation: best.citation, caption: captionFor(best.citation) };
}

/** A short grounded caption for a matched citation (verbatim-quote friendly). */
function captionFor(citation: Citation): string {
  switch (citation.id) {
    case ANSWER_CITATION.id:
      return ANSWER_TRANSCRIPT;
    case CONTRADICTION_CITATION.id:
      return CONTRADICTION_TRANSCRIPT;
    case CONTRACT_CLAUSE_CITATION.id:
      return CONTRACT_EMAIL_TRANSCRIPT;
    case EMAIL_ADMISSION_CITATION.id:
      return CONTRACT_EMAIL_TRANSCRIPT;
    case PROACTIVE_CITATION.id:
      return PROACTIVE_TRANSCRIPT;
    default:
      return `Found a grounded source on page ${citation.bbox.page}.`;
  }
}
