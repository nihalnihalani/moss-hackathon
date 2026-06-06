# CrossExam — The Pitch

> **"Ask 900 pages a question out loud — and watch the answer land on the exact line."**

A voice agent that interrogates huge documents. **Moss** finds the one relevant line in **<10ms
in-process**; **Unsiloed** snaps a **bounding box onto the exact line of the scanned page** as the
agent speaks the answer. Built on **LiveKit Agents** for the real-time voice loop.

Presented in the winning order: **Problem → Solution → Market → Validation → Demo → Business Model
→ Future → Team** (Gary-Yau Chan format).

---

## Problem
Voice agents "fall apart on domain-specific info" (verbatim, this week's top voice-AI tutorial).
In law that's not a UX bug — an LLM citing a paragraph that doesn't exist is a **malpractice
event**. Lawyers don't have a *search* problem, they have a **trust** problem: they cannot act on
an answer they can't see on the page. And a 3-second pause to find a clause mid-deposition is the
difference between catching a witness and losing the room.

## Solution
Interrogate the document by voice. Moss does sub-10ms in-process retrieval to find the one
relevant line out of 900 pages; Unsiloed's word-level bounding box draws itself onto the exact line
of the rendered page as the agent answers. **The citation is visible, grounded, and verifiable.**

## Market
1.3M+ US lawyers already expense four-figure software seats (Westlaw, Relativity). The *same
engine* fans out into insurance claims, M&A due diligence, and compliance audits.
**Why now:** EU AI Act Article 50 transparency obligations land **Aug 2, 2026** — "prove your
source, exactly" becomes a compliance requirement, not a nicety.

## Validation (Day 2)
Interview a solo litigator, a discovery paralegal, and an insurance claims adjuster (all same-day
reachable via state-bar listservs / r/legaltech / LinkedIn). Capture:
- A screen-recording of the box snapping to the cited line in <10ms — **that clip IS the pitch.**
- A quote on hours/week burned manually locating clauses across discovery.
- Confirmation that "I can't use an answer I can't verify on the page" is the real blocker.

## Demo — the 90-second kill shot
| Time | On screen | The shot |
|---|---|---|
| 0–10s | Split screen: LiveKit visualizer orb (left) \| PDF scrolling "**p.1 of 912**" (right) | Establish the haystack |
| 10–25s | Orb `LISTENING`. "Did the witness admit they were at the warehouse on the night of the 14th?" | State badge flips |
| 25–40s | Orb `THINKING`; right pane blurs 1 → 687 (searching 900 pages, *visible*) | Moss-is-working shot |
| **40–60s** | **THE SNAP:** glowing bounding box draws onto the exact line; caption streams answer; chip reads **"found in 912 pages · 7ms"** | **The winning screenshot** |
| 60–78s | Follow-up surfaces a *second* box on p.203 → "it found a contradiction hundreds of pages apart" | Climax |
| 78–90s | Freeze on dual citations. Tagline: "Ask the document. It points to the proof." | Deck cover frame |

**Hero features (cut everything else):** (1) the bounding-box snap onto the rendered PDF;
(2) Moss-as-hero "found the one line in 912 pages · 7ms" with auto page-jump + latency chip.

## Business Model
Per-seat legal SaaS at legal-software price points → usage tiers for discovery-heavy matters.
Moat = grounded-citation verifiability that plain-RAG competitors fake. High margin.

## Future Rollout
Law → insurance → due diligence → compliance, **same engine, no rewrite** — a credible
wedge-to-platform arc.

## Team
Assign from hour zero: a **front-end/UX owner** (owns the bbox→canvas coordinate mapping — the whole
demo) and a **BizDev/pitch owner** (runs Day-2 customer validation, presents loud and confident).
These are the two roles Chan's research says decide hackathon wins.

---

## API-evangelist prize hook
**Unsiloed bounding boxes + word-level citations** — the photographable, grounded citation almost
nobody demos. Visit the Unsiloed evangelist early; this is the unique-API-use play.
