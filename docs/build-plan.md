# CrossExam — 24h Build Plan & Sponsor API Map

## Architecture (the critical path)
```
 Mic → LiveKit Agents (STT → LLM → TTS, real-time loop)
          │
          ├─ on_user_turn_completed()  ← LiveKit hook (NOT a Moss hook)
          │     └─ Moss.query(index, user_text, top_k=5, alpha=0.8)  ← sub-10ms, in-process
          │            └─ inject results as role="system" into turn_ctx  ("no dead air")
          │
 Front-end (React + LiveKit components)
          ├─ audio visualizer orb + agent state pill (LISTENING/THINKING/SPEAKING)
          ├─ streaming captions
          └─ PDF canvas → draw Unsiloed bounding box on the cited line  ← THE WOW
```

## Pre-event prep (do before Saturday)
- Pin the exact working Moss package + import names — they're inconsistent across npm/PyPI.
- Run Unsiloed **Parse + Extract** on the demo PDF **offline** (async; can't run live). Store:
  text chunks, word-level citations, **bounding boxes**, page numbers.
- Build the Moss index from the parsed chunks (embed page + bbox as metadata).
- Scripted demo questions → pre-compute the bbox geometry so the live render is a lookup.

## Hour-by-hour (24h)
| Block | Goal |
|---|---|
| 0–2h | Pin Moss package names; LiveKit agent skeleton talks back; `on_user_turn_completed` injects a hardcoded string |
| 2–6h | Moss index live; real retrieval injected per turn; confirm <10ms locally |
| 6–12h | Front-end: PDF canvas renders; **bbox→canvas transform pinned** (scale × page-offset × devicePixelRatio); box snaps on a scripted question |
| 12–16h | Wire retrieval result → page-jump + bbox draw + latency chip; streaming captions synced |
| 16–20h | The contradiction follow-up beat; agent state pill; visualizer; polish the 90s storyboard |
| 20–24h | **Test at projector resolution**; rehearse the snap until bulletproof; record the backup video |

## Must-fix risks (ranked)
1. **`on_user_turn_completed` is a LiveKit hook, not Moss's.** Attribute correctly to judges.
2. **Pre-index the PDF** — Unsiloed parse is async; retrieval + voice only run live.
3. **Bbox→canvas drift** — a misaligned box is worse than none. Pin the transform; test at the
   *projector's* DPI/resolution, not just the laptop. Render at fixed zoom; disable responsive reflow.
4. **Moss package-name lottery** — resolve in the first 30 minutes.
5. **Have a recorded backup** of the 90s demo in case conference WiFi misbehaves.

## Sponsor capability map (verified vs. claim)
| Capability | Status | Notes |
|---|---|---|
| Moss client-side WASM (`@moss-dev/moss-web`) | REAL | sub-10ms is a vendor CLAIM |
| Moss package names | INCONSISTENT | on-site risk: `@moss-dev/moss` vs `@inferedge/moss`; py `moss` vs `inferedge-moss` |
| LiveKit `on_user_turn_completed` | REAL | LiveKit hook, the documented RAG-injection point |
| LiveKit agent state attributes / semantic turn detection | REAL | use the state pill in the demo |
| Unsiloed bounding boxes + word-level citations + confidence | REAL | the hero visual; parse is async (pre-index) |
| Nova 2 Sonic async tool calling | REAL but flaky | documented hangs on *chained* tools — avoid for the demo path |
| Nova 2 Sonic barge-in | REAL | optional |
| TrueFoundry Virtual MCP + gateway PII redaction | REAL | not needed for CrossExam core |
| MiniMax 10s clone + voice-design-from-text + emotions/40 langs | REAL | optional voice flavor |

> Treat all vendor latency / accuracy / count figures as CLAIMS — spot-check on-site.

## Demo-day shortcuts (Gary-Yau Chan)
- Use the codename **CrossExam**; don't waste time over-naming.
- Borrow a clean UI template/color scheme; skip any sign-in — land straight on the document.
- A "searching 912 pages" loading animation signals algorithmic work and buys recovery time.
- Get a judge to ask the document a question live — interactive beats a video.
- Visit the **Unsiloed** evangelist early (befriend, get tagged for the prize); don't reveal the
  full WOW — save the bounding-box snap for the presentation.
