# The Debate — Full Transcript

A 3-round structured debate run by the orchestrator, relaying outputs between teammates
(subagents can't talk directly). Idea generation included a dedicated **Devil's Advocate**.

Protocol: **Ideator proposes → Devil's Advocate attacks → Researcher fact-checks → Demo-Designer
scores demo-ability → BizDev scores story/market → Orchestrator tallies & commits.**

---

## Round 1 — Ideator: 8 candidates

| # | Codename | One-line pitch | Unpopular feature gamified |
|---|---|---|---|
| 1 | **REDLINE** | Live voice negotiation co-pilot; surfaces the exact contract clause mid-sentence over SIP | LiveKit agent state attributes ("notice it skipped *thinking*") |
| 2 | **SECONDOPINION** | Fully offline client-side triage agent; whole corpus in browser via Moss WASM | `@moss-dev/moss-web` WASM build |
| 3 | **GHOSTWRITER** | Clones your voice (10s) and answers domain Qs in your tone | MiniMax voice-design-from-text |
| 4 | **SWITCHBOARD** | One phone number, 200 tools via Virtual MCP; files a ticket while still talking | TrueFoundry Virtual MCP + Nova 2 Sonic async tool calling |
| 5 | **REDACT** | Compliance agent; PII scrubbed at gateway before the model ever sees it | TrueFoundry gateway PII redaction + Moss metadata filtering |
| 6 | **CROSSEXAM** | Interrogate a 900-page deposition out loud; bounding boxes snap onto the page | Unsiloed bounding boxes + word-level citations |
| 7 | **POLYGLOT** | Multilingual + 7-emotion field support over one shared Moss index | MiniMax 7 emotions × 40 languages |
| 8 | **BARGEIN** | "Interrupt me anytime" tutor; re-retrieves on each interruption | Nova 2 Sonic barge-in + LiveKit semantic turn detection |

**Ideator's bet:** REDLINE (top 3: REDLINE, SWITCHBOARD, REDACT). Flagged POLYGLOT/BARGEIN softest,
SECONDOPINION's offline claim riskiest.

---

## Round 2a — Devil's Advocate: teardown

| Idea | Verdict | Cause of death |
|---|---|---|
| REDLINE | **KILL** | Killer beat admires an *invisible* 10ms; latency is network-bound over SIP regardless of Moss; SIP eats the weekend |
| SECONDOPINION | **FIX** | Airplane mode kills your own slides — but offline WASM is the *only* truly Moss-native, undeniable demo |
| GHOSTWRITER | **KILL** | Great MiniMax demo, wrong hackathon; Moss is garnish |
| SWITCHBOARD | **FIX** | "200 tools" is a config file; judge-dials-in cedes network control; it's a TrueFoundry demo |
| REDACT | **KILL** | Stages the *absence* of a leak; Moss part is a `WHERE` clause; feature, not a company |
| CROSSEXAM | **FIX** | Bbox is Unsiloed's, but it photographs best and has the cleanest network path |
| POLYGLOT | **KILL** | Capabilities sampler, no product, no killer beat |
| BARGEIN | **KILL** | Killer beat deliberately triggers your worst bug 3× live |

**The decisive reframe:** *"The Ideator's entire top-3 (REDLINE, SWITCHBOARD, REDACT) shares one
fatal trait: Moss is invisible in all three. If the differentiator can't be photographed, it can't
win."* → Survivors where Moss is **visible**: SECONDOPINION & CROSSEXAM.

---

## Round 2b — Researcher: feasibility (parallel with the teardown)

Confirmed **real** as of June 2026: Moss client-side WASM (`@moss-dev/moss-web`); LiveKit agent
state attributes + semantic turn detection + `on_user_turn_completed` (a **LiveKit** hook, not
Moss's); Nova 2 Sonic async tool calling (⚠️ documented hangs on *chained* tools) + barge-in;
TrueFoundry Virtual MCP + gateway PII redaction; Unsiloed bounding boxes + confidence + word-level
citations; MiniMax 10s clone + voice-design-from-text + emotions/languages.

- **Lowest live-demo risk:** SECONDOPINION (if on-device LLM question is settled), GHOSTWRITER, REDACT.
- **Highest risk:** SWITCHBOARD (Nova async tools hang on chained calls — the exact pattern needed),
  BARGEIN (stacking Nova barge-in + LiveKit turn detection is unverified integration).
- **On-site risk for everyone:** Moss package names are inconsistent (`@moss-dev/moss` vs
  `@inferedge/moss`; Python `moss` vs `inferedge-moss`). Pin them in the first 30 minutes.
- Vendor-stated *numbers* (latency, redaction accuracy, emotion×language counts) = CLAIMS to spot-check.

---

## Round 3 — Demo-Designer + BizDev on the two survivors

**Demo-Designer:** SECONDOPINION **7.5/10** (proof is a number that *stays at zero* — absence is
hard to feel; honesty gap on offline generation). CROSSEXAM **9/10** — the bounding-box snap is a
*presence*, instantly legible across the room, Moss is legibly the hero ("found 1 line in 912
pages · 7ms"), all claims demonstrably true on stage. **CrossExam has the higher-ceiling moment.**

**BizDev fundable-narrative score:** SECONDOPINION **51** vs CROSSEXAM **62**. CrossExam's buyers
are reachable and expense four-figure seats; rides two tailwinds (verifiable-source demand + EU AI
Act transparency from Aug 2, 2026); same engine fans out law → insurance → due diligence →
compliance.

---

## Convergence (Orchestrator, disagree-and-commit)

Three teammates independently became true believers in **CrossExam**; the Devil's Advocate moved it
to FIX→SURVIVES with concrete mitigations. The Ideator's opening bet (REDLINE) was refuted on the
"invisible differentiator" axis. **Committed: CrossExam.** See `pitch-crossexam.md` and `build-plan.md`.
