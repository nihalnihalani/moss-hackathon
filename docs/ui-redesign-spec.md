# CrossExam — UI/UX Redesign Spec ("The Deposition Room")

Synthesized from three research streams (21st.dev component mining · best-in-class product UIs ·
motion/a11y/perceived-latency). This is the buildable spec the UI engineer implements.

## 1. Aesthetic thesis
**The Deposition Room.** Quiet, institutional, expensive — a dim wood-panelled conference room at
midnight where the *only* color is the marker on the page. Near-black surfaces, editorial serif
voice, monospaced forensic metadata, and a single sacred **evidence-amber** accent that means
exactly one thing: *this is the cited fact*. The citation-snap onto the PDF is the hero; everything
else recedes so the eye goes there. Anti-AI-slop: no Inter/Roboto/system, no purple gradients, no
flat cards — real grain, hairline glass borders, OKLCH-tuned color.

## 2. Design tokens (CSS custom properties)
```css
:root {
  /* Surface ladder — near-black void → charcoal strata (Raycast/Vercel discipline) */
  --void:        #06070A;
  --surface-1:   #0C0E13;
  --surface-2:   #13161D;
  --surface-3:   #1B1F28;
  --hairline:    rgba(255,255,255,0.07);
  --hairline-st: rgba(255,255,255,0.14);

  /* Text */
  --text:        #E8EAF0;   /* ~13:1 on void — AA ✓ */
  --text-dim:    #9AA0AE;
  --text-mute:   #5C6270;

  /* THE accent — Evidence Amber. Rationed to: citation highlight, audio aura, focus, primary CTA */
  --amber:       #FFB020;
  --amber-soft:  rgba(255,176,32,0.16);
  --amber-glow:  rgba(255,176,32,0.55);

  /* Audio-state hues (state legibility, NOT decoration) */
  --state-listen: #63B3ED;  /* cool blue  */
  --state-think:  #FFB020;  /* amber      */
  --state-speak:  #6EE7B7;  /* mint       */

  /* Typography */
  --font-display: "Fraunces", Georgia, serif;       /* wordmark + headings (optical, characterful) */
  --font-serif:   "Newsreader", Georgia, serif;     /* deposition prose / agent voice */
  --font-mono:    "IBM Plex Mono", ui-monospace, monospace; /* metrics, page refs, state, IDs */

  /* Type scale (px): 12 13 14 16 18 24 32 48 ; tight tracking on display */
  --r-sm: 6px;  --r-md: 10px;  --r-lg: 16px;  --r-full: 999px;

  /* Motion (Material 3 + spring research) */
  --ease-emph:        cubic-bezier(0.2, 0, 0, 1);
  --ease-decel:       cubic-bezier(0.05, 0.7, 0.1, 1);
  --ease-snap:        cubic-bezier(0.34, 1.56, 0.64, 1); /* overshoot — the SNAP */
  --dur-fast: 180ms; --dur-med: 320ms; --dur-snap: 420ms; --dur-long: 600ms;

  /* Spacing — 8px grid */
  --s1:4px; --s2:8px; --s3:12px; --s4:16px; --s5:24px; --s6:32px; --s7:48px; --s8:64px;
}
```
Fonts via `@fontsource/fraunces`, `@fontsource/newsreader`, `@fontsource/ibm-plex-mono` (self-hosted,
no FOUT, offline-safe for the demo).

## 3. Layout
- **Background**: `--void` + an animated **canvas grain** (per-pixel noise, alpha ~16, `pixelated`,
  `pointer-events:none`) + a fine **dot-grid** (`background-size: 22px 24px`, opacity ~0.05) + one cold
  radial spotlight top-center. Three layers = depth, not noise.
- **Top command bar**: glassy (`backdrop-blur`, `--surface-1/60`, hairline bottom border). Left:
  Fraunces wordmark "CrossExam" + a mono case-number tag. Center: the **HUD agent-state readout**
  (mono, letter-scramble decode on change). Right: latency chip · Upload · Run demo (amber primary).
- **Left rail** (~320px): the **voice orb** (centered, large), the **state pill** below it, then the
  **streaming transcript** (agent = serif left-aligned with a page-ref chip; user = mono right). Fills
  the empty column the old design wasted.
- **Right stage**: the PDF on a faint "paper under lamp" surface (`--surface-1`, soft inset shadow,
  subtle vignette), page nav + zoom as a floating glass control. The citation overlay lives here.

## 4. Component redesign (before → after)
| Component | Now | After |
|---|---|---|
| **Voice orb** | plain grey blob | **Siri-style conic-gradient orb** (layered `conic-gradient` + `radial`, blurred/saturated, rotating via `--angle`). State = change `--animation-duration` + `--blur` + hue: listen slow/blue, think mid/amber + scale pulse, speak fast/mint sharp. Audio-reactive scale from RMS when live. |
| **State pill** | grey "READY" | mono **HUD readout** `● LISTENING / ANALYZING / CITING` with a ping-dot (`animate-ping` behind solid dot), hue per state, `role=status aria-live=polite`. |
| **Captions** | plain text block | agent lines in **Newsreader serif**, word-staggered reveal (35ms/word), each agent line carries a clickable mono **page-ref chip** that re-fires the snap; `role=log aria-live=polite aria-atomic=false`. |
| **PDF chrome** | bare canvas + 3 icons | "paper under lamp" surface, floating glass **page nav + zoom**, page indicator `p.12 / 912` in mono tabular-nums. |
| **Citation box (HERO)** | flat cyan rect | **amber** box that *draws/snaps* in with spring overshoot, glow via pseudo-element opacity, corner crosshair tick, label `p.12 · 94%` mono. Multi-line aware. |
| **Latency chip** | plain pill | mono `tabular-nums`, count-up to `found in 912 pages · 7ms`, leading status dot, glass pill — pops on the snap with its own lighter spring. |
| **Upload** | text button | amber-glow **dropzone** ("Submit document into evidence"), masked dot-grid, pdf-only, progress + success states. |
| **Empty/loading** | none | PDF **skeleton shimmer** with reserved box dims (optimistic box draws on skeleton, real page crossfades under). |

## 5. The signature moment (≈900ms, 3 beats)
1. **0–350ms — page jump**: fast decelerate translate to the cited page + a thin sweeping scan-line ("searching 912 pages").
2. **350–650ms — THE SNAP**: amber box springs in — `spring(stiffness 520, damping 30, mass 0.9)` (or CSS `--ease-snap`), one tight overshoot; glow trails by 60ms then decays `0.55→0.18`; latency chip pops with a lighter spring; numbers count up.
3. **600ms+ — caption stream**: words reveal staggered (35ms), starting as the snap settles so it feels *caused* by the box.

## 6. Accessibility (WCAG 2.2 AA — legal-tech requires it)
- Text ≥ 4.5:1; box/chip/orb/focus ≥ 3:1 non-text.
- `:focus-visible { outline: 2px solid var(--amber); outline-offset: 2px }`; no ring on mouse.
- Citation boxes are focusable buttons, arrow-key navigable, Enter jumps, Esc clears.
- Live regions: state pill `polite`, captions `polite`+`aria-atomic=false` (pre-rendered empty in DOM).
- **prefers-reduced-motion**: motion (scale/translate/rotate) → **opacity/color only**; orb becomes a
  static state-colored dot + text label; box crossfades (no spring); chip shows final number directly.
  Never strip *feedback*, only *movement*.
- Performance: animate only `transform`/`opacity`; glow on a `::after` pseudo (animate its opacity);
  `will-change` only while animating.

## 7. Constraints
Preserve ALL behavior: `lib/bbox.ts` transform, mock/live decision, data-channel citation path, every
existing test. Keep TS strict, eslint/tsc clean, `vite build` green. Build to the spec's complexity —
meticulous spacing, type, and the one orchestrated moment over scattered jitter.
