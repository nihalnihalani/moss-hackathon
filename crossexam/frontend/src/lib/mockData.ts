/**
 * mockData.ts — the scripted demo, runnable with zero backend.
 *
 * Two citations matching the 90-second kill shot:
 *   1. The answer on p.687 ("warehouse on the night of the 14th").
 *   2. The contradiction surfaced on p.203 hundreds of pages apart.
 *
 * Geometry assumes a US-Letter scanned page (612 x 792 pt). Boxes wrap a single
 * line; coordinates are top-left origin, y downward (see types.ts BBox).
 */

import type { BBox, Citation } from '../types';

/** Intrinsic size of the placeholder/demo page, in PDF points (US Letter). */
export const DEMO_PAGE_WIDTH_PT = 612;
export const DEMO_PAGE_HEIGHT_PT = 792;

/** Corpus size, drives the "found in 912 pages" chip and the page-jump animation. */
export const DEMO_TOTAL_PAGES = 912;

const warehouseBBox: BBox = { page: 687, x0: 84, y0: 372, x1: 540, y1: 396 };
const contradictionBBox: BBox = { page: 203, x0: 84, y0: 188, x1: 528, y1: 212 };

/** The primary answer the agent speaks first. */
export const ANSWER_CITATION: Citation = {
  id: 'cite-warehouse-687',
  text: 'Q: Were you at the warehouse on the night of the 14th? A: Yes. I was there until nearly midnight.',
  bbox: warehouseBBox,
  confidence: 0.94,
  latencyMs: 7,
  pagesSearched: DEMO_TOTAL_PAGES,
};

/** The follow-up contradiction hundreds of pages earlier. */
export const CONTRADICTION_CITATION: Citation = {
  id: 'cite-contradiction-203',
  text: 'On the 14th the witness signed the visitor log at the downtown office at 9:40 p.m., two miles from the warehouse.',
  bbox: contradictionBBox,
  confidence: 0.89,
  latencyMs: 6,
  pagesSearched: DEMO_TOTAL_PAGES,
};

/** The streamed answer caption that plays alongside the first snap. */
export const ANSWER_TRANSCRIPT =
  'Yes — on page 687 the witness admits being at the warehouse on the night of the 14th, ' +
  'staying until nearly midnight.';

/** The user question that kicks off the demo sequence. */
export const DEMO_QUESTION =
  'Did the witness admit they were at the warehouse on the night of the 14th?';

/** The contradiction follow-up caption. */
export const CONTRADICTION_TRANSCRIPT =
  'But there is a contradiction: on page 203, the same witness signed the downtown visitor log ' +
  'at 9:40 p.m. that night — two miles away.';

/** Ordered list of demo citations for any UI that wants to step through them. */
export const DEMO_CITATIONS: readonly Citation[] = [ANSWER_CITATION, CONTRADICTION_CITATION];
