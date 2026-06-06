# CrossExam — The Pitch

> **Track: CO-PILOT — ambient agents that listen in and display live context.**

> **"Ask 900 pages a question out loud — and watch the answer land on the exact line, in <10ms."**

**Positioning (one paragraph):** CrossExam is a real-time voice **co-pilot** that listens to a live
conversation and snaps a **grounded, verifiable citation onto the exact line of a document in
under 10ms** — the spoken question, the spoken answer, and the highlighted source line all on screen
at the same instant. It is **not** a notetaker. It does not summarize after the call; it proves the
answer *during* the call. **Moss** finds the one relevant line in **<10ms in-process**; **Unsiloed**
snaps a **bounding box onto the exact line of the scanned page** as the agent speaks. Built on
**LiveKit Agents** for the real-time voice loop. The wedge is **verifiable co-presence**: every claim
in a live conversation is provable the moment it's made.

Presented in the winning order: **Problem → Solution → Why Not a Notetaker → Market → Validation →
Demo → Business Model → Future → Team** (Gary-Yau Chan format).

---

## Problem
Voice agents "fall apart on domain-specific info" (verbatim, this week's top voice-AI tutorial).
Ungrounded LLM bots hallucinate citations **15–27%** of the time; grounded retrieval drops that to
**0.7–1.5%**. In a live, high-stakes conversation that gap is a liability event — 2026 has already
produced **$145K+ in court sanctions** for lawyers who filed ungrounded, fabricated citations.
The deeper issue isn't *search*, it's **trust**: you cannot act on an answer you can't see on the
page, and a 3-second pause to find a clause mid-deposition is the difference between catching a
witness and losing the room.

## Solution
CrossExam listens as you speak. Moss does sub-10ms **in-process** retrieval to find the one relevant
line out of 900 pages; Unsiloed's word-level bounding box draws itself onto the exact line of the
rendered page as the agent answers. **The citation is visible, grounded, and verifiable — live.**
The question, the answer, and the source line co-exist on screen in the same instant.

## Why Not a Notetaker / vs Incumbents
The "AI for conversations" space is crowded — but every incumbent surfaces value at the **wrong
time** or surfaces something **unverifiable**:

- **Granola / Otter / Fireflies (meeting copilots):** summarize **after** the call. The value lands
  in your inbox, not in the room. No source line, no live proof.
- **Gong / Clari (sales copilots):** surface **generic battlecards and analytics** — coaching cues,
  not a verifiable line from *this* document.
- **Cresta (contact-center copilot):** real-time agent assist, but **scripted next-best-action
  prompts**, not a grounded citation snapped to a source.
- **Abridge / Nuance DAX (clinical scribes):** **write the note** from the encounter — generation,
  not live verifiable retrieval against a source document.

**None of them visibly snap a verifiable source line in real time.** That is CrossExam's whitespace:
**verifiable co-presence.** (Full competitive table in `co-pilot-positioning.md`.)

## Market
1.3M+ US lawyers already expense four-figure software seats (Westlaw, Relativity). But the *same
engine* **generalizes beyond legal** — support docs, sales calls, meetings, and clinical encounters
all need a claim proved against a source line the instant it's spoken.
**Why now:** trust is becoming regulated and litigated. EU AI Act Article 50 transparency
obligations land **Aug 2, 2026** ("prove your source, exactly"), and 2026 sanctions for ungrounded
citations make verifiability a liability shield, not a nicety. The conversational latency budget is
~200ms; bolt-on vector DBs add **50–300ms** and blow it, so an answer arrives with dead air or no
proof. In-process retrieval is the research frontier the field is racing toward
(**VoiceAgentRAG**, arXiv:2603.02206) — CrossExam ships it today.

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
| 78–90s | Freeze on dual citations. Tagline: "Verifiable co-presence — every claim provable the moment it's made." | Deck cover frame |

**Hero features (cut everything else):** (1) the bounding-box snap onto the rendered PDF;
(2) Moss-as-hero "found the one line in 912 pages · 7ms" with auto page-jump + latency chip.

## Business Model
Per-seat legal SaaS at legal-software price points → usage tiers for discovery-heavy matters.
Moat = grounded-citation verifiability that plain-RAG competitors fake. High margin.

## Future Rollout
Law → insurance → due diligence → compliance, **same engine, no rewrite** — a credible
wedge-to-platform arc. The co-pilot pattern (listen → ground → snap) generalizes to support,
sales, meetings, and clinical with the same retrieval core.

## Team
Assign from hour zero: a **front-end/UX owner** (owns the bbox→canvas coordinate mapping — the whole
demo) and a **BizDev/pitch owner** (runs Day-2 customer validation, presents loud and confident).
These are the two roles Chan's research says decide hackathon wins.

---

## API-evangelist prize hook
**Unsiloed bounding boxes + word-level citations** — the photographable, grounded citation almost
nobody demos. Visit the Unsiloed evangelist early; this is the unique-API-use play.
