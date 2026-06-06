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
  DEMO_QUESTION,
  DEMO_CONTRADICTION_QUESTION,
  DOC_DEPOSITION,
  DOC_EXHIBIT,
  DOC_FIELD_NOTES,
  DOC_CONTRACT,
  DOC_EMAIL,
  DOC_TITLES,
  DOC_URLS,
  CONTRACT_CLAUSE_CITATION,
  EMAIL_ADMISSION_CITATION,
  CONTRACT_EMAIL_ANCHOR,
  CONTRACT_EMAIL_HOPS,
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

  it('hops mirror the backend decompose() output verbatim (mock == live)', () => {
    // The backend QueryDecomposer strips the contradiction framing from
    // DEMO_CONTRADICTION_QUESTION down to the temporal-anchor topic and prefixes
    // "evidence that contradicts " for the second hop. These are the EXACT
    // strings test_published_hops_equal_decompose_output asserts the live trail
    // equals — no hand-written natural-language fiction.
    expect(CONTRADICTION_HOPS[0]?.subQuery).toBe('night 14th');
    expect(CONTRADICTION_HOPS[1]?.subQuery).toBe('evidence that contradicts night 14th');
  });

  it('the canonical contradiction question carries a contradiction cue (routes multi-hop)', () => {
    // The live backend routes this multi-hop because it contains "contradict";
    // it deliberately does NOT echo "Harbor Street" (robustness is principled).
    expect(DEMO_CONTRADICTION_QUESTION.toLowerCase()).toContain('contradict');
    expect(DEMO_CONTRADICTION_QUESTION).not.toContain('Harbor Street');
    expect(DEMO_CONTRADICTION_QUESTION).not.toBe(DEMO_QUESTION);
  });

  it('doc titles + urls map the demo documents correctly', () => {
    expect(DOC_TITLES[DOC_DEPOSITION]).toBe('Deposition of Raymond T. Holloway');
    expect(DOC_TITLES[DOC_EXHIBIT]).toBe('Exhibit 14: Security Desk Visitor Log');
    expect(DOC_URLS[DOC_EXHIBIT]).toBe('/exhibit-visitor-log.pdf');
    expect(DOC_URLS[DOC_DEPOSITION]).toBe('/sample-deposition.pdf');
  });
});

describe('KILLER FEATURE B — contract-vs-email cross-doc beat mirrors the fixture', () => {
  it('CONTRACT_CLAUSE_CITATION mirrors fixture chunk contract-msa-p7-l1 (clause, p7)', () => {
    expect(CONTRACT_CLAUSE_CITATION.id).toBe('contract-msa-p7-l1');
    expect(CONTRACT_CLAUSE_CITATION.documentId).toBe(DOC_CONTRACT);
    expect(CONTRACT_CLAUSE_CITATION.bbox.page).toBe(7);
    expect(CONTRACT_CLAUSE_CITATION.bbox).toMatchObject({
      page: 7, x0: 72, y0: 123.94, x1: 528, y1: 185.94, page_width: 612, page_height: 792,
    });
    expect(CONTRACT_CLAUSE_CITATION.text).toContain('Section 4.2 Subcontracting');
    expect(CONTRACT_CLAUSE_CITATION.text).toContain('prior written consent');
    expect(CONTRACT_CLAUSE_CITATION.text).toContain('material breach');
    expect(CONTRACT_CLAUSE_CITATION.quads).toHaveLength(5);
    expect(CONTRACT_CLAUSE_CITATION.scanned).toBeUndefined();
  });

  it('EMAIL_ADMISSION_CITATION mirrors fixture chunk email-thread-p1-l2 (admission, p1)', () => {
    expect(EMAIL_ADMISSION_CITATION.id).toBe('email-thread-p1-l2');
    expect(EMAIL_ADMISSION_CITATION.documentId).toBe(DOC_EMAIL);
    expect(EMAIL_ADMISSION_CITATION.bbox.page).toBe(1);
    expect(EMAIL_ADMISSION_CITATION.bbox).toMatchObject({
      page: 1, x0: 72, y0: 163.94, x1: 534, y1: 225.94, page_width: 612, page_height: 792,
    });
    expect(EMAIL_ADMISSION_CITATION.text).toContain('Acme Labs');
    expect(EMAIL_ADMISSION_CITATION.text).toContain('never got the formal sign-off');
    expect(EMAIL_ADMISSION_CITATION.quads).toHaveLength(5);
    expect(EMAIL_ADMISSION_CITATION.scanned).toBeUndefined();
  });

  it('the contract-vs-email conflict is cross-document with the §4.2 anchor', () => {
    expect(CONTRACT_CLAUSE_CITATION.documentId).not.toBe(EMAIL_ADMISSION_CITATION.documentId);
    expect(CONTRACT_EMAIL_ANCHOR).toBe('§4.2 Subcontracting');
  });

  it('hops anchor on the clause then the contradicting admission', () => {
    expect(CONTRACT_EMAIL_HOPS).toHaveLength(2);
    expect(CONTRACT_EMAIL_HOPS[0]?.citationIds).toEqual([CONTRACT_CLAUSE_CITATION.id]);
    expect(CONTRACT_EMAIL_HOPS[1]?.citationIds).toEqual([EMAIL_ADMISSION_CITATION.id]);
  });

  it('maps the contract + email document ids to the served PDFs', () => {
    expect(DOC_URLS[DOC_CONTRACT]).toBe('/contract-msa.pdf');
    expect(DOC_URLS[DOC_EMAIL]).toBe('/email-thread.pdf');
    expect(DOC_TITLES[DOC_CONTRACT]).toBe('Master Services Agreement');
  });
});
