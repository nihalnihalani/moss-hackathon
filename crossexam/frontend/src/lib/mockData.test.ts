/**
 * mockData.test.ts — guards the MOCK ↔ LIVE reconcile (BLOCKER).
 *
 * The mock citations must mirror the real backend fixture
 * (backend/fixtures/sample_chunks.json) exactly, so unchecking "Force mock" does
 * not break the demo. These assertions pin the load-bearing fields (id, page,
 * union bbox, quad geometry, scanned honesty) to the fixture's values.
 */
import { describe, it, expect } from 'vitest';
import {
  ANSWER_CITATION,
  CONTRADICTION_CITATION,
  PROACTIVE_CITATION,
  CONTRADICTION_HOPS,
  DOC_DEPOSITION,
  DOC_EXHIBIT,
  DOC_FIELD_NOTES,
  DOC_TITLES,
  DOC_URLS,
} from './mockData';

describe('mock citations mirror the real fixture', () => {
  it('ANSWER_CITATION mirrors fixture chunk pdf-p12-l1 (deposition p12)', () => {
    expect(ANSWER_CITATION.id).toBe('pdf-p12-l1');
    expect(ANSWER_CITATION.documentId).toBe(DOC_DEPOSITION);
    expect(ANSWER_CITATION.bbox.page).toBe(12);
    expect(ANSWER_CITATION.bbox).toMatchObject({ x0: 72, y0: 123.94, x1: 522, y1: 159.94 });
    expect(ANSWER_CITATION.text).toContain('Harbor Street');
    expect(ANSWER_CITATION.text).toContain('well past midnight'); // fixed the "nearly midnight" drift
    expect(ANSWER_CITATION.scanned).toBeUndefined();
    expect(ANSWER_CITATION.quads).toHaveLength(3);
  });

  it('CONTRADICTION_CITATION targets exhibit-visitor-log PAGE 2 with the real coords', () => {
    expect(CONTRADICTION_CITATION.id).toBe('exhibit-visitor-log-p2-l1');
    expect(CONTRADICTION_CITATION.documentId).toBe(DOC_EXHIBIT);
    expect(CONTRADICTION_CITATION.bbox.page).toBe(2);
    expect(CONTRADICTION_CITATION.bbox).toMatchObject({
      page: 2,
      x0: 72,
      y0: 123.94,
      x1: 534,
      y1: 172.94,
      page_width: 612,
      page_height: 792,
    });
    expect(CONTRADICTION_CITATION.text).toContain('9:40 p.m. on the 14th');
    expect(CONTRADICTION_CITATION.text).toContain('two miles from the Harbor Street warehouse');
    // Born-digital — NOT scanned.
    expect(CONTRADICTION_CITATION.scanned).toBeUndefined();
    // Four wrapped lines -> four quads, all on page 2.
    expect(CONTRADICTION_CITATION.quads).toHaveLength(4);
    for (const q of CONTRADICTION_CITATION.quads ?? []) {
      expect(q.page).toBe(2);
    }
  });

  it('the contradiction is cross-document (deposition vs exhibit)', () => {
    expect(ANSWER_CITATION.documentId).not.toBe(CONTRADICTION_CITATION.documentId);
  });

  it('PROACTIVE_CITATION is the real scanned field-notes chunk (honest scanned beat)', () => {
    expect(PROACTIVE_CITATION.id).toBe('exhibit-field-notes-p1-l0');
    expect(PROACTIVE_CITATION.documentId).toBe(DOC_FIELD_NOTES);
    expect(PROACTIVE_CITATION.scanned).toBe(true);
    expect(PROACTIVE_CITATION.bbox.page).toBe(1);
  });

  it('hops are the principled claim + contradicting-evidence pair, with resolvable ids', () => {
    expect(CONTRADICTION_HOPS).toHaveLength(2);
    expect(CONTRADICTION_HOPS[0]?.citationIds).toEqual([ANSWER_CITATION.id]);
    expect(CONTRADICTION_HOPS[1]?.citationIds).toEqual([CONTRADICTION_CITATION.id]);
  });

  it('doc titles + urls map the demo documents correctly', () => {
    expect(DOC_TITLES[DOC_DEPOSITION]).toBe('Deposition of Raymond T. Holloway');
    expect(DOC_TITLES[DOC_EXHIBIT]).toBe('Exhibit 14: Security Desk Visitor Log');
    expect(DOC_URLS[DOC_EXHIBIT]).toBe('/exhibit-visitor-log.pdf');
    expect(DOC_URLS[DOC_DEPOSITION]).toBe('/sample-deposition.pdf');
  });
});
