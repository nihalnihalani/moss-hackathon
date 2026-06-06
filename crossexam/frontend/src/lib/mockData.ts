/**
 * mockData.ts — the scripted depth-v2 demo, runnable with zero backend.
 *
 * Showcases all five features without a backend:
 *   1. Multi-doc + multi-hop CONTRADICTION: a box in the deposition, then a
 *      conflicting box in the exhibit-visitor-log doc, with a hops trail.
 *   2. Quad highlights: citations carry per-line `quads[]` so the amber
 *      highlight hugs real text across wraps (union `bbox` drives jump + label).
 *   3. Scanned source: the exhibit is OCR'd -> `scanned: true` badge.
 *   4. Meeting mode: a proactive citation tagged with its `speaker`.
 *   5. Memory: a recall chip ("as we saw on page 12") instead of a re-snap.
 *
 * IMPORTANT — page numbers + point bboxes MUST match the actual layout of the
 * real PDFs served at /public/sample-deposition.pdf and
 * /public/exhibit-visitor-log.pdf, since `bbox.page` is both the page the canvas
 * navigates to and the page the box is drawn on. bbox.ts reads the TRUE page
 * dimensions from the rendered viewport, so only the per-line x/y need to track
 * the text; the US-Letter constants below are the placeholder fallback size.
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

export const DOC_TITLES: Readonly<Record<string, string>> = {
  [DOC_DEPOSITION]: 'Holloway Deposition',
  [DOC_EXHIBIT]: 'Exhibit C — Visitor Log',
};

/** documentId -> public PDF url. Selecting a tab jumps the canvas to this doc. */
export const DOC_URLS: Readonly<Record<string, string>> = {
  [DOC_DEPOSITION]: '/sample-deposition.pdf',
  [DOC_EXHIBIT]: '/exhibit-visitor-log.pdf',
};

/**
 * The warehouse-admission line (deposition). Two wrapped lines, so it carries
 * two quads that hug the real text; `bbox` is their union.
 */
const warehouseQuads: BBox[] = [
  {
    page: 12,
    x0: 72.0,
    y0: 123.94,
    x1: 522.0,
    y1: 141.94,
    page_width: DEMO_PAGE_WIDTH_PT,
    page_height: DEMO_PAGE_HEIGHT_PT,
  },
  {
    page: 12,
    x0: 72.0,
    y0: 143.94,
    x1: 408.0,
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
 * The contradicting visitor-log entry — in the SEPARATE exhibit document. Scanned
 * (OCR) source. Two quads hugging the wrapped entry.
 */
const visitorLogQuads: BBox[] = [
  {
    page: 1,
    x0: 64.0,
    y0: 188.0,
    x1: 520.0,
    y1: 206.0,
    page_width: DEMO_PAGE_WIDTH_PT,
    page_height: DEMO_PAGE_HEIGHT_PT,
  },
  {
    page: 1,
    x0: 64.0,
    y0: 208.0,
    x1: 360.0,
    y1: 226.0,
    page_width: DEMO_PAGE_WIDTH_PT,
    page_height: DEMO_PAGE_HEIGHT_PT,
  },
];

const visitorLogBBox: BBox = {
  page: 1,
  x0: 64.0,
  y0: 188.0,
  x1: 520.0,
  y1: 226.0,
  page_width: DEMO_PAGE_WIDTH_PT,
  page_height: DEMO_PAGE_HEIGHT_PT,
};

/** The primary answer the agent speaks first (deposition, page 12). */
export const ANSWER_CITATION: Citation = {
  id: 'pdf-p12-l1',
  text: 'Q. Where were you on the night of the 14th? A. I was at the Harbor Street warehouse from approximately 9:00 p.m. until nearly midnight.',
  bbox: warehouseBBox,
  quads: warehouseQuads,
  confidence: 0.94,
  score: 0.91,
  latencyMs: 7,
  pagesSearched: DEMO_TOTAL_PAGES,
  faithfulness: { supported: true, score: 0.99, method: 'nli' },
  documentId: DOC_DEPOSITION,
  documentTitle: DOC_TITLES[DOC_DEPOSITION],
};

/**
 * The follow-up contradiction — surfaced from the EXHIBIT (a different document),
 * a scanned visitor log. This is the cross-doc conflict.
 */
export const CONTRADICTION_CITATION: Citation = {
  id: 'exh-p1-l3',
  text: 'Visitor log, downtown office: H. Holloway — signature recorded at 9:40 p.m. on the 14th, two miles from the Harbor Street warehouse.',
  bbox: visitorLogBBox,
  quads: visitorLogQuads,
  confidence: 0.89,
  score: 0.88,
  latencyMs: 6,
  pagesSearched: DEMO_TOTAL_PAGES,
  faithfulness: { supported: true, score: 0.96, method: 'nli' },
  documentId: DOC_EXHIBIT,
  documentTitle: DOC_TITLES[DOC_EXHIBIT],
  scanned: true,
};

/**
 * The PROACTIVE / MEETING-mode beat. A speaker utters a CLAIM aloud and the
 * co-pilot snaps the contradicting scanned exhibit UNPROMPTED — tagged with the
 * speaker who triggered it.
 */
export const PROACTIVE_CITATION: Citation = {
  id: 'exh-p1-l3-proactive',
  text: 'Visitor log, downtown office: signature recorded at 9:40 p.m. on the 14th — two miles from the Harbor Street warehouse.',
  bbox: { ...visitorLogBBox },
  quads: visitorLogQuads.map((q) => ({ ...q })),
  confidence: 0.92,
  score: 0.9,
  latencyMs: 9,
  pagesSearched: DEMO_TOTAL_PAGES,
  faithfulness: { supported: true, score: 0.98, method: 'nli' },
  documentId: DOC_EXHIBIT,
  documentTitle: DOC_TITLES[DOC_EXHIBIT],
  scanned: true,
};

/** Speakers for meeting mode (feat 4). */
export const SPEAKER_COUNSEL: Speaker = { id: 'spk_1', label: 'Counsel' };
export const SPEAKER_WITNESS: Speaker = { id: 'spk_2', label: 'Witness' };

/**
 * The hops trail (feat 1): how the agent decomposed the question to find the
 * cross-document contradiction. Surfaced under the contradiction banner.
 */
export const CONTRADICTION_HOPS: HopTrace[] = [
  {
    subQuery: 'Where does the witness say he was on the night of the 14th?',
    citationIds: [ANSWER_CITATION.id],
  },
  {
    subQuery: 'Is there any record placing the witness elsewhere that night?',
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
  'the 14th, staying until nearly midnight.';

/** The user question that kicks off the demo sequence. */
export const DEMO_QUESTION =
  'Did the witness admit they were at the warehouse on the night of the 14th?';

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
  'Flagging this unprompted: the scanned visitor-log exhibit has the witness signing ' +
  'the downtown log at 9:40 p.m. — which contradicts the claim just made.';

/**
 * A query that has NO grounded source in the corpus. The co-pilot stays silent
 * rather than fabricate a box — surfaced as the honest empty state.
 */
export const NOT_FOUND_CLAIM =
  'Was there ever any mention of a second vehicle at the scene?';

/** Caption shown alongside the honest-silence (not_found) beat. */
export const NOT_FOUND_TRANSCRIPT =
  'I could not find that in the document, so I am staying silent rather than guess.';

/** Ordered list of demo citations for any UI that wants to step through them. */
export const DEMO_CITATIONS: readonly Citation[] = [
  ANSWER_CITATION,
  CONTRADICTION_CITATION,
  PROACTIVE_CITATION,
];
