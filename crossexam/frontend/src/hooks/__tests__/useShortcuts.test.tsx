/**
 * useShortcuts.test.tsx — keyboard-shortcut scoping (Quick Win #1).
 *
 * Verifies:
 *  - Space (push-to-talk) is IGNORED while typing in an input (form-tag scope),
 *    but fires start/stop when focus is on the page body.
 *  - Auto-repeat (e.repeat) does not re-fire onTalkStart.
 *  - The doc-cycle shortcuts fire prev/next, which App wires to tab switching.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { useShortcuts, type UseShortcutsHandlers } from '../useShortcuts';

function Harness({ handlers }: { handlers: UseShortcutsHandlers }): JSX.Element {
  useShortcuts(handlers);
  return (
    <div>
      <input data-testid="field" />
      <div data-testid="surface" tabIndex={-1} />
    </div>
  );
}

function makeHandlers(): UseShortcutsHandlers & { [K in keyof UseShortcutsHandlers]: ReturnType<typeof vi.fn> } {
  return {
    onTalkStart: vi.fn(),
    onTalkEnd: vi.fn(),
    onOpenPalette: vi.fn(),
    onPrevDoc: vi.fn(),
    onNextDoc: vi.fn(),
    onToggleHelp: vi.fn(),
    onToggleMock: vi.fn(),
  } as UseShortcutsHandlers & { [K in keyof UseShortcutsHandlers]: ReturnType<typeof vi.fn> };
}

describe('useShortcuts scoping', () => {
  beforeEach(() => vi.clearAllMocks());

  it('does NOT fire push-to-talk while typing a space in an input', () => {
    const handlers = makeHandlers();
    render(<Harness handlers={handlers} />);
    const field = screen.getByTestId('field');
    field.focus();

    fireEvent.keyDown(field, { key: ' ', code: 'Space' });
    fireEvent.keyUp(field, { key: ' ', code: 'Space' });

    expect(handlers.onTalkStart).not.toHaveBeenCalled();
    expect(handlers.onTalkEnd).not.toHaveBeenCalled();
  });

  it('fires push-to-talk start/stop on Space from the page body', () => {
    const handlers = makeHandlers();
    render(<Harness handlers={handlers} />);

    fireEvent.keyDown(document.body, { key: ' ', code: 'Space' });
    fireEvent.keyUp(document.body, { key: ' ', code: 'Space' });

    expect(handlers.onTalkStart).toHaveBeenCalledTimes(1);
    expect(handlers.onTalkEnd).toHaveBeenCalledTimes(1);
  });

  it('guards auto-repeat so a held Space starts talking only once', () => {
    const handlers = makeHandlers();
    render(<Harness handlers={handlers} />);

    fireEvent.keyDown(document.body, { key: ' ', code: 'Space' });
    fireEvent.keyDown(document.body, { key: ' ', code: 'Space', repeat: true });
    fireEvent.keyDown(document.body, { key: ' ', code: 'Space', repeat: true });

    expect(handlers.onTalkStart).toHaveBeenCalledTimes(1);
  });

  it('cycles document tabs with Cmd/Ctrl+[ and Cmd/Ctrl+]', () => {
    // `mod` resolves to Ctrl on non-Apple (the test platform); at runtime it also
    // matches ⌘ on macOS. We fire ctrlKey here for deterministic matching.
    const handlers = makeHandlers();
    render(<Harness handlers={handlers} />);

    fireEvent.keyDown(document.body, { key: ']', code: 'BracketRight', ctrlKey: true });
    expect(handlers.onNextDoc).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(document.body, { key: '[', code: 'BracketLeft', ctrlKey: true });
    expect(handlers.onPrevDoc).toHaveBeenCalledTimes(1);
  });

  it('opens the question palette on Cmd/Ctrl+K (even from a focused field)', () => {
    const handlers = makeHandlers();
    render(<Harness handlers={handlers} />);
    const field = screen.getByTestId('field');
    field.focus();

    fireEvent.keyDown(field, { key: 'k', code: 'KeyK', ctrlKey: true });
    expect(handlers.onOpenPalette).toHaveBeenCalledTimes(1);
  });

  it('toggles the cheat-sheet on Shift+/', () => {
    const handlers = makeHandlers();
    render(<Harness handlers={handlers} />);

    fireEvent.keyDown(document.body, { key: '/', code: 'Slash', shiftKey: true });
    expect(handlers.onToggleHelp).toHaveBeenCalledTimes(1);
  });

  it('toggles mock mode on M (body focus)', () => {
    const handlers = makeHandlers();
    render(<Harness handlers={handlers} />);

    fireEvent.keyDown(document.body, { key: 'm', code: 'KeyM' });
    expect(handlers.onToggleMock).toHaveBeenCalledTimes(1);
  });

  it('does NOT fire mock toggle while typing m in an input', () => {
    const handlers = makeHandlers();
    render(<Harness handlers={handlers} />);
    const field = screen.getByTestId('field');
    field.focus();

    fireEvent.keyDown(field, { key: 'm', code: 'KeyM' });
    expect(handlers.onToggleMock).not.toHaveBeenCalled();
  });
});
