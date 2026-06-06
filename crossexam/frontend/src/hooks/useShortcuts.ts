/**
 * useShortcuts — global keyboard shortcuts for CrossExam (Quick Win #1).
 *
 * Bindings (react-hotkeys-hook, cross-platform `mod` = ⌘ on mac, Ctrl elsewhere):
 *   - Space (hold)   push-to-talk. keydown starts, keyup stops; `e.repeat` is
 *                    guarded so an auto-repeating held key fires start once.
 *                    preventDefault stops the page from scrolling. Disabled on
 *                    form tags so you never talk while typing.
 *   - mod+K          open the "type a question" palette (enabled on form tags so
 *                    it opens from anywhere, including an input).
 *   - mod+[ / mod+]  cycle the document tabs (prev / next).
 *   - Shift+/ (?)    toggle the shortcuts cheat-sheet.
 *
 * The hook owns no UI; it calls back into the app. `isMac` is returned so the
 * cheat-sheet can render the right mod glyph.
 */

import { useHotkeys } from 'react-hotkeys-hook';

export interface UseShortcutsHandlers {
  /** Space pressed (held) — begin push-to-talk. Guarded against auto-repeat. */
  onTalkStart: () => void;
  /** Space released — end push-to-talk. */
  onTalkEnd: () => void;
  /** Open the Cmd/Ctrl+K question palette. */
  onOpenPalette: () => void;
  /** Cycle to the previous document tab (Cmd/Ctrl+[). */
  onPrevDoc: () => void;
  /** Cycle to the next document tab (Cmd/Ctrl+]). */
  onNextDoc: () => void;
  /** Toggle the shortcuts cheat-sheet (Shift+/). */
  onToggleHelp: () => void;
}

export interface UseShortcutsResult {
  /** True on a mac (drives ⌘ vs Ctrl in the cheat-sheet). */
  isMac: boolean;
}

/** True when the running platform is macOS (for the mod-key glyph). */
export function detectMac(): boolean {
  if (typeof navigator === 'undefined') return false;
  const platform =
    (navigator as Navigator & { userAgentData?: { platform?: string } }).userAgentData?.platform ??
    navigator.platform ??
    '';
  return /mac|iphone|ipad|ipod/i.test(platform);
}

export function useShortcuts(handlers: UseShortcutsHandlers): UseShortcutsResult {
  const { onTalkStart, onTalkEnd, onOpenPalette, onPrevDoc, onNextDoc, onToggleHelp } = handlers;

  // Space = push-to-talk (hold). keydown + keyup, guard auto-repeat, stop scroll.
  // Default-disabled on form tags so typing a space never triggers the mic.
  useHotkeys(
    'space',
    (e) => {
      e.preventDefault();
      if ((e as KeyboardEvent).repeat) return;
      onTalkStart();
    },
    { keydown: true, keyup: false, enableOnFormTags: false },
    [onTalkStart],
  );
  useHotkeys(
    'space',
    (e) => {
      e.preventDefault();
      onTalkEnd();
    },
    { keydown: false, keyup: true, enableOnFormTags: false },
    [onTalkEnd],
  );

  // Cmd/Ctrl+K — open the question palette. enableOnFormTags so it opens from
  // anywhere (including a focused field).
  useHotkeys(
    'mod+k',
    (e) => {
      e.preventDefault();
      onOpenPalette();
    },
    { enableOnFormTags: true },
    [onOpenPalette],
  );

  // Cmd/Ctrl+[ and Cmd/Ctrl+] — cycle document tabs.
  useHotkeys(
    'mod+bracketleft',
    (e) => {
      e.preventDefault();
      onPrevDoc();
    },
    { enableOnFormTags: false },
    [onPrevDoc],
  );
  useHotkeys(
    'mod+bracketright',
    (e) => {
      e.preventDefault();
      onNextDoc();
    },
    { enableOnFormTags: false },
    [onNextDoc],
  );

  // Shift+/ (?) — toggle the cheat-sheet.
  useHotkeys(
    'shift+slash',
    (e) => {
      e.preventDefault();
      onToggleHelp();
    },
    { enableOnFormTags: false },
    [onToggleHelp],
  );

  return { isMac: detectMac() };
}
