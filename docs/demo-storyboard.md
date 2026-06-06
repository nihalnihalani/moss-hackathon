# CrossExam — 90-Second Demo Storyboard (run-of-show)

The exact on-stage sequence. Runs in **mock mode with zero keys** (`make dev`), so it cannot fail
on conference WiFi. The hero moment is the **bounding-box snap** onto the cited line.

## Co-Pilot framing (CO-PILOT track)
Frame CrossExam as an **ambient voice co-pilot that listens and displays live context** — not a
notetaker. The win condition on screen is **verifiable co-presence**: the spoken question, the
spoken answer, and the highlighted source line are all visible **in the same instant**. Say it out
loud once: "This isn't a summary after the call — the proof is on screen *while* I'm talking."
(See `co-pilot-positioning.md` for the full vs-incumbents brief.)

## Pre-flight (before you walk up)
- `make dev` running; browser on the app, full-screen, mock mode (default).
- Real `sample-deposition.pdf` loaded (912-page corpus metaphor; admission on p12, contradiction on p41).
- Backup: a screen-recording of this exact run (in case the room WiFi is hostile).
- Optional: `crossexam-doctor` shown once to prove the stack is wired.

## Run of show

| Time | What the audience sees | What you say | The "shot" |
|---|---|---|---|
| 0:00–0:10 | Split screen: voice orb (left) · PDF scrolling **"p.1 of 912"** (right) | "This is a 912-page deposition. The answer is one sentence, somewhere in here." | Establish the haystack |
| 0:10–0:25 | Orb pulses `LISTENING` | *(speak)* "Did the witness admit they were at the warehouse on the night of the 14th?" | State pill flips |
| 0:25–0:40 | Orb `THINKING`; page counter blurs **1 → 12** | *(beat)* | The "searching 900 pages" motion |
| **0:40–1:00** | **THE SNAP** — glowing box draws on the exact line of **p.12**; caption streams; chip reads **"found in 912 pages · 7ms"** | "Yes — page 12, at the Harbor Street warehouse until nearly midnight." | **The winning screenshot** |
| 1:00–1:18 | Follow-up: box snaps on **p.41** | "But did he contradict himself?" → "Yes — on page 41 he says he left before 8 p.m." | Contradiction across the document |
| 1:18–1:30 | Freeze on the citation; tagline | "Verifiable co-presence — every claim provable the moment it's made." | Deck cover frame |

### Optional ambient/proactive beat (CO-PILOT track)
If time allows, drop one **unprompted** moment: instead of being asked, let a *claim* be spoken in
the room — e.g. someone says "the witness was never at the warehouse" — and the co-pilot **snaps the
contradicting source line on its own** (confidence-gated), with the latency badge. Line to say:
"I didn't ask it anything — it heard the claim and put the proof on screen." This is the clearest
demonstration of an ambient agent listening in and displaying live context.

## The one line that wins the room
> "Every other voice agent bolts RAG on as an afterthought and you *hear* the dead air. We made
> retrieval the hero — sub-10ms, in-process, with the citation drawn on the page. Watch."

## If a judge wants to drive it
Hand them the mic in mock mode (scripted) **or**, if keys are loaded, switch to live mode and let
them ask. The live path publishes the citation over the LiveKit data channel and the box snaps the
same way (covered by an integration test).

## The API-evangelist hook
Call out the **Unsiloed bounding box + word-level citation** explicitly — it's the photographable,
grounded citation almost nobody demos. Visit the Unsiloed evangelist early.

See also: `../crossexam/README.md` (runbook), `../crossexam/STATUS.md` (what's verified),
`pitch-crossexam.md` (the full pitch), `build-plan.md` (24h critical path).
