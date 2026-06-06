# CrossExam — Co-Pilot Positioning Brief

**Track:** CO-PILOT — ambient agents that listen in and display live context.

## The positioning statement (one paragraph)
CrossExam is a real-time voice **co-pilot** that listens to a live conversation and snaps a
**grounded, verifiable citation onto the exact line of a document in under 10ms**. The spoken
question, the spoken answer, and the highlighted source line are all on screen at the same instant.
It is **not** a notetaker: it doesn't summarize after the call — it **proves the answer during the
call**. Moss does the retrieval in-process (<10ms, inside the conversational budget); Unsiloed draws
a word-level bounding box on the rendered page; LiveKit Agents runs the live voice loop. The wedge is
**verifiable co-presence** — every claim in a live conversation is provable the moment it's made.

## Competitive whitespace
| Product | Category | When it surfaces value | What it surfaces | Verifiable source line? |
|---|---|---|---|---|
| **Granola** | Meeting copilot / notetaker | **After** the call | AI meeting notes & summary | **No** |
| **Otter / Fireflies** | Meeting transcription | After (+ live transcript) | Transcript & summary | **No** |
| **Gong** | Revenue intelligence | After the call (analytics) | Deal insights, generic battlecards | **No** |
| **Clari** | Revenue platform | After / async forecasting | Pipeline & forecast analytics | **No** |
| **Cresta** | Contact-center copilot | **Live** | Scripted next-best-action prompts | **No** |
| **Abridge / Nuance DAX** | Clinical scribe | After the encounter | A **generated** clinical note | **No** |
| **CrossExam** | **Voice co-pilot (grounded)** | **Live, same instant** | **The exact source line, snapped + cited** | **Yes** |

The whole column that matters — *live* **and** *verifiable source line* — is empty until CrossExam.
Meeting copilots are late; sales copilots are generic; clinical scribes generate rather than verify;
Cresta is live but surfaces scripts, not a provable source. CrossExam owns **verifiable
co-presence**.

## The 3 biggest judge objections + counters
1. **"Isn't this just Granola / a notetaker?"**
   No. Granola/Otter/Fireflies deliver value **after** the call as a summary — there's no source
   line and nothing to verify in the moment. CrossExam acts **during** the conversation and puts a
   **clickable, grounded source line on screen as the words are spoken**. Different time, different
   artifact, different job.

2. **"Is sub-10ms retrieval actually real?"**
   Yes — because it's **in-process**, not a network hop to a vector DB. Bolt-on vector stores add
   **50–300ms** and blow the ~200ms conversational budget; Moss runs the retrieval inside the
   process. We show a **live latency badge** ("found in 912 pages · 7ms") on stage, and it runs in
   mock mode offline so it can't fail on conference WiFi. This is the exact frontier the literature
   is racing toward (**VoiceAgentRAG**, arXiv:2603.02206).

3. **"Is this a feature, not a company?"**
   The *snap* is a feature; the **moat is verifiable co-presence as a platform primitive.** Trust is
   becoming regulated (EU AI Act Art. 50, Aug 2 2026) and litigated ($145K+ in 2026 sanctions for
   ungrounded citations). The same listen→ground→snap engine generalizes across legal, support,
   sales, meetings, and clinical with **no rewrite** — a wedge-to-platform arc, not a single trick.

## Co-Pilot feature set
- **Proactive, confidence-gated snap** — when a claim is spoken, the agent surfaces the citation
  **unprompted**, but only above a confidence threshold (no noisy false-positive highlights).
- **Live latency badge** — every snap shows its real retrieval time ("· 7ms"), making the
  in-budget claim visible and falsifiable on stage.
- **Multi-speaker meeting mode** — diarized listening so the co-pilot can attribute and ground
  claims across multiple speakers in a live meeting, not just a single questioner.
- **Click-through provenance trail** — every snapped citation is clickable back to the exact page,
  bounding box, and confidence; a session builds an auditable trail of every claim and its proof.
