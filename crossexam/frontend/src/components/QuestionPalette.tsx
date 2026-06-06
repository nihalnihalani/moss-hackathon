/**
 * QuestionPalette — a Cmd/Ctrl+K "type a question" palette (Quick Win #1).
 *
 * A cmdk Command.Dialog: type a question about the active document and press
 * Enter to ask. In mock mode this runs a query beat (drives the demo); in live
 * mode the same string is handed to the existing ask path. The dialog traps
 * focus only while open (cmdk handles this), so global focus is never trapped.
 */

import { useEffect, useState } from 'react';
import { Command } from 'cmdk';
import { Search, CornerDownLeft } from 'lucide-react';

export interface QuestionPaletteProps {
  /** Whether the palette is shown. */
  open: boolean;
  /** Close the palette (Esc / overlay click / after submit). */
  onOpenChange: (open: boolean) => void;
  /** Submit the typed question (Enter). */
  onAsk: (question: string) => void;
  /** Optional suggested prompts surfaced as selectable rows. */
  suggestions?: string[];
}

export function QuestionPalette({
  open,
  onOpenChange,
  onAsk,
  suggestions = [],
}: QuestionPaletteProps): JSX.Element {
  const [value, setValue] = useState('');

  // Reset the field whenever the palette closes so it opens fresh next time.
  useEffect(() => {
    if (!open) setValue('');
  }, [open]);

  const submit = (q: string): void => {
    const trimmed = q.trim();
    if (!trimmed) return;
    onAsk(trimmed);
    onOpenChange(false);
  };

  return (
    <Command.Dialog
      open={open}
      onOpenChange={onOpenChange}
      label="Ask a question about this document"
      className="qpalette"
      data-testid="question-palette"
      shouldFilter={false}
    >
      <div className="qpalette__inputrow">
        <Search size={15} className="qpalette__icon" aria-hidden="true" />
        <Command.Input
          autoFocus
          value={value}
          onValueChange={setValue}
          placeholder="Type a question about this document…"
          className="qpalette__input"
          data-testid="question-palette-input"
          onKeyDown={(e) => {
            if (e.key === 'Enter' && suggestions.length === 0) {
              e.preventDefault();
              submit(value);
            }
          }}
        />
        <kbd className="kbd qpalette__enter" aria-hidden="true">
          <CornerDownLeft size={11} /> Enter
        </kbd>
      </div>

      <Command.List className="qpalette__list">
        {value.trim() ? (
          <Command.Item
            value={`ask:${value}`}
            onSelect={() => submit(value)}
            className="qpalette__item qpalette__item--ask"
          >
            Ask “{value.trim()}”
          </Command.Item>
        ) : (
          <Command.Empty className="qpalette__empty">
            Type a question, then press Enter to ask.
          </Command.Empty>
        )}

        {suggestions.length > 0 ? (
          <Command.Group heading="Suggested" className="qpalette__group">
            {suggestions.map((s) => (
              <Command.Item
                key={s}
                value={s}
                onSelect={() => submit(s)}
                className="qpalette__item"
              >
                {s}
              </Command.Item>
            ))}
          </Command.Group>
        ) : null}
      </Command.List>
    </Command.Dialog>
  );
}
