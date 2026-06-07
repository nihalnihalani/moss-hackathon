/**
 * ShortcutsCheatSheet — a "?" (Shift+/) overlay listing the keyboard shortcuts
 * (Quick Win #1). Keys render as <kbd> styled to the Deposition Room tokens.
 *
 * Rendered as a lightweight modal: a dimmed backdrop + a frosted card. Escape or
 * a backdrop click closes it; focus is not trapped globally — the close button
 * is auto-focused for keyboard users and Escape always exits.
 */

import { useEffect, useRef } from 'react';
import { X } from 'lucide-react';

export interface Shortcut {
  /** Key combo, e.g. ['Space'] or ['⌘', 'K']. Each token renders as a <kbd>. */
  keys: string[];
  /** What it does. */
  label: string;
}

export interface ShortcutsCheatSheetProps {
  open: boolean;
  onClose: () => void;
  /** Whether to show ⌘ (mac) vs Ctrl (others) for the mod key. */
  isMac: boolean;
}

export function ShortcutsCheatSheet({ open, onClose, isMac }: ShortcutsCheatSheetProps): JSX.Element | null {
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const mod = isMac ? '⌘' : 'Ctrl';

  useEffect(() => {
    if (open) closeRef.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  const shortcuts: Shortcut[] = [
    { keys: ['Space'], label: 'Push-to-talk (hold)' },
    { keys: [mod, 'K'], label: 'Ask a question' },
    { keys: [mod, '['], label: 'Previous document' },
    { keys: [mod, ']'], label: 'Next document' },
    { keys: ['?'], label: 'Toggle this cheat sheet' },
    { keys: ['M'], label: 'Toggle mock / live mode (on-stage bail-out)' },
  ];

  return (
    <div
      className="cheatsheet__backdrop"
      data-testid="shortcuts-cheatsheet"
      onClick={onClose}
    >
      <div
        className="cheatsheet"
        role="dialog"
        aria-modal="true"
        aria-label="Keyboard shortcuts"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="cheatsheet__head">
          <h2 className="cheatsheet__title">Keyboard Shortcuts</h2>
          <button
            ref={closeRef}
            type="button"
            className="cheatsheet__close"
            onClick={onClose}
            aria-label="Close keyboard shortcuts"
          >
            <X size={16} />
          </button>
        </div>
        <ul className="cheatsheet__list">
          {shortcuts.map((s) => (
            <li key={s.label} className="cheatsheet__row">
              <span className="cheatsheet__label">{s.label}</span>
              <span className="cheatsheet__keys">
                {s.keys.map((k, i) => (
                  <kbd key={`${s.label}-${i}`} className="kbd">
                    {k}
                  </kbd>
                ))}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
