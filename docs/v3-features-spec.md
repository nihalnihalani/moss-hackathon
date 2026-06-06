# CrossExam v3 — Quick Wins + Killer Features Spec

Research-grounded build spec (sources in the research briefs). Build to this.

## Quick Wins (frontend)
1. **Keyboard shortcuts** (`react-hotkeys-hook`, cross-platform `mod`):
   - **Space** = push-to-talk (hold). `keydown`+`keyup`; guard `e.repeat`; `preventDefault` (stop page scroll); default-disabled on form tags (don't talk while typing).
   - **Cmd/Ctrl+K** = open a "type a question" palette (`cmdk` combobox; `enableOnFormTags` so it opens even from a field). Enter = ask.
   - **Cmd/Ctrl+[ / ]** = switch document tabs (cycle).
   - **Shift+/ (`?`)** = shortcuts cheat-sheet overlay; render keys in `<kbd>`.
   - a11y: don't trap focus; `aria-live` announce mic state; mic button `aria-keyshortcuts="Space"` `aria-pressed`.
2. **Empty-state dropzone** (`react-dropzone`): the first-run canvas IS the drop target — frosted-glass panel, "Drag & drop a Deposition, Contract, or Brief to begin" + "browse files". States from `isDragActive/isDragAccept/isDragReject` via `data-drag`. accept pdf/docx/txt, single file. Keyboard-openable (`noKeyboard:false`), `aria-label`, announce success/failure. Posts to the existing `/documents` ingest (or loads locally in mock).
3. **Transcript auto-scroll** ("stick-to-bottom"): auto-scroll to bottom on new captions, but STOP if the user scrolled up; show a "↓ Jump to latest" pill when not at bottom. Threshold ~32px (never exact equality). `prefers-reduced-motion` → instant. `role="log" aria-live="polite" aria-atomic="false"`. Prefer `use-stick-to-bottom` or a clean hand-rolled hook.

## Killer Features
### Export to Legal Memo (frontend)
- **Deterministic `buildMemo(session) -> MemoModel`** (always runs, no key) from the transcript + cited passages (doc+page+verbatim quote) + detected contradictions. Sections (order): Heading/Caption → Question Presented → Brief Answer → Statement of Facts → **Findings** (each → cited passage) → **Contradictions/Discrepancies** (anchor + the two conflicting quotes) → Discussion (IRAC) → Conclusion → Appendix (cited passages). Quotes verbatim, never paraphrased.
- **Export**: a command-bar **Export** button (lucide `download` icon) → menu: **Markdown** (Blob download, trivial) and **PDF** via **`window.print()` + `@media print` CSS** (`@page{size:Letter;margin:1in}`, `break-inside:avoid` on quotes/citations/findings, `break-before:page` before Appendix; isolate the memo node). Zero new heavy deps; crisp selectable text. (Avoid html2canvas+jsPDF — blurry raster.)
- **Guarded LLM enhance** (only if a key present): feed the built MemoModel JSON, model returns the SAME JSON with only prose fields rewritten — quotes/pages/doc-names byte-identical; discard malformed → keep deterministic.

### Multi-document cross-examination (contract vs email)
- **Demo docs** (pipeline-generated PDFs + fixture): a Master Services Agreement (documentId `contract-msa`) and an Email Thread (`email-thread`) with REAL conflicting pairs sharing an anchor:
  - **§4.2 Subcontracting**: contract "Contractor shall not … subcontract … without prior written consent … material breach" (p.7) vs email "we already handed the integration work off to Acme Labs … never got the formal sign-off" (p.1).
  - **§6.1 Net-30**: contract "pay each undisputed invoice within thirty (30) days (Net-30) … not be modified except by a written amendment" (p.9) vs email "we're going to pay this one on a Net-60 basis … didn't sign anything to change the terms" (p.1).
- **Backend detector**: generalize cross-doc contradiction beyond location to TERM/OBLIGATION conflict — shared anchor (clause #, "Net-30"/"Net-60", subject) + opposing-obligation cues (consent vs "never got sign-off"; "30 days" vs "Net-60"; "shall not" vs admission). Keep the location path. Must detect the contract↔email pair cross-doc.
- **UI**: when the contradiction spans two docs, present them clearly — synced split / rapid tab-switch + linked color-coded highlights (contract one color, email the other) + an **anchor banner** ("CONFLICT — Anchor: §4.2 Subcontracting") and a one-line plain-English conflict note. Draw a box on the contract, then the email, to prove the breach.

## Constraints
Preserve all behavior + the depth-v2 contract; keep TS strict, ruff/mypy/tsc/eslint clean, all tests green, eval/bench pass. Multiple verifications + a devil's-advocate audit before "done".
