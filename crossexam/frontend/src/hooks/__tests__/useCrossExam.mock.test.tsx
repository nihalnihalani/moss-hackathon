/**
 * useCrossExam.mock.test.tsx — guards mock/live parity for the scripted demo.
 *
 * The live backend publishes the cross-document contradiction with primaryId
 * set to the deposition claim under examination (`pdf-p12-l1`), while still
 * carrying the exhibit counter-evidence in `citations[]`. The mock script must
 * mirror that exactly so switching Force mock off does not change the story.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, act, cleanup } from '@testing-library/react';
import { useCrossExam } from '../useCrossExam';
import {
  ANSWER_CITATION,
  CONTRADICTION_CITATION,
  CONTRACT_CLAUSE_CITATION,
  EMAIL_ADMISSION_CITATION,
  CONTRACT_EMAIL_ANCHOR,
  DOC_EMAIL,
} from '../../lib/mockData';

function MockHarness(): JSX.Element {
  const cx = useCrossExam({ forceMock: true });
  const counter = cx.citations.find((c) => c.id !== cx.primaryId);
  return (
    <div>
      <button type="button" onClick={cx.runDemo}>
        run
      </button>
      <span data-testid="primary-id">{cx.primaryId ?? 'none'}</span>
      <span data-testid="active-id">{cx.activeCitation?.id ?? 'none'}</span>
      <span data-testid="contradiction">{String(cx.contradiction)}</span>
      <span data-testid="anchor">{cx.anchor ?? 'none'}</span>
      <span data-testid="citation-ids">{cx.citations.map((c) => c.id).join(',')}</span>
      <span data-testid="target-page">{cx.targetPage}</span>
      <span data-testid="counter-id">{counter?.id ?? 'none'}</span>
      <span data-testid="counter-doc">{counter?.documentId ?? 'none'}</span>
    </div>
  );
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('mock scripted demo', () => {
  it('matches the live contradiction primaryId while retaining the cross-doc counter', () => {
    vi.useFakeTimers();
    render(<MockHarness />);

    fireEvent.click(screen.getByText('run'));
    act(() => {
      vi.advanceTimersByTime(7600);
    });

    expect(screen.getByTestId('contradiction').textContent).toBe('true');
    expect(screen.getByTestId('primary-id').textContent).toBe(ANSWER_CITATION.id);
    expect(screen.getByTestId('active-id').textContent).toBe(ANSWER_CITATION.id);
    expect(screen.getByTestId('target-page').textContent).toBe(String(ANSWER_CITATION.bbox.page));
    expect(screen.getByTestId('citation-ids').textContent).toContain(ANSWER_CITATION.id);
    expect(screen.getByTestId('citation-ids').textContent).toContain(CONTRADICTION_CITATION.id);
  });

  it('surfaces the contract-vs-email breach: 2 cross-doc citations, counter on the email doc', () => {
    vi.useFakeTimers();
    render(<MockHarness />);

    fireEvent.click(screen.getByText('run'));
    act(() => {
      vi.advanceTimersByTime(32800);
    });

    // The contract clause is the primary; the email admission is the cross-doc counter.
    expect(screen.getByTestId('contradiction').textContent).toBe('true');
    expect(screen.getByTestId('anchor').textContent).toBe(CONTRACT_EMAIL_ANCHOR);
    expect(screen.getByTestId('primary-id').textContent).toBe(CONTRACT_CLAUSE_CITATION.id);
    const ids = screen.getByTestId('citation-ids').textContent ?? '';
    expect(ids).toContain(CONTRACT_CLAUSE_CITATION.id);
    expect(ids).toContain(EMAIL_ADMISSION_CITATION.id);
    // The counter is the email admission, on the email document.
    expect(screen.getByTestId('counter-id').textContent).toBe(EMAIL_ADMISSION_CITATION.id);
    expect(screen.getByTestId('counter-doc').textContent).toBe(DOC_EMAIL);
  });
});
