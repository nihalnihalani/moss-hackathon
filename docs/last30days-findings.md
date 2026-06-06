# Live Trend Grounding — `/last30days conversational AI voice agents`

Run on 2026-06-04, last-30-day window (2026-05-05 → 2026-06-04).
Sources active: Reddit (with comments), X, YouTube, HN, Polymarket, TikTok, Instagram.
Returned: **9 X posts + 10 YouTube videos** (Reddit hit a 402/paywall this run; HN empty for the query).

## Headline signals (what judges will find novel)

1. **Latency is the universal obsession.** Every top tutorial brags about it — ElevenLabs Agents
   tout "187ms latency" and ultra-low-latency models; Deepgram's unified Voice Agent API pushes
   barge-in + turn-prediction and claims it beats OpenAI Realtime / ElevenLabs / Azure on their
   Voice Agent Quality Index.

2. **RAG / retrieval is the *named* failure mode.** Direct quote from Tech With Tim's developer
   tutorial: *"a lot of the AI voice agents that you see, they kind of fall apart when they're
   dealing with domain-specific information."* This is precisely Moss's wedge.

3. **Knowledge bases are bolted on as an afterthought.** The ElevenLabs walkthrough spends its
   back half on scraping a website into a knowledge base / RAG to stop hallucination — retrieval
   is treated as plumbing, never the hero of the demo. **Nobody makes retrieval-latency the show.**

4. **Active field (last 30 days):** ElevenLabs Agents (V3 expressive mode, audio tags),
   Deepgram unified Voice Agent API, Retell AI, PolyAI, Voiceflow (vs Avery), Twilio Conversational
   AI, Google Conversational Agents, Sarvam (India, opening public access to "Sarvam Samvaad"),
   and dialect-aware Arabic voice agents (cntxt acquiring Actualize).

## Why this validates CrossExam / the Moss thesis
- The market obsesses over voice latency but treats **retrieval latency** as invisible plumbing.
- The most-cited failure ("falls apart on domain-specific info") is exactly what sub-10ms
  in-process retrieval + grounded citations fixes.
- So the winning move is to make **retrieval the visible hero** of the demo — which is what
  CrossExam's bounding-box-on-the-page moment does.

## Stats
- 🔵 X: 9 posts · ~60 likes
- 🔴 YouTube: 10 videos · ~218k combined views · 3 with full transcripts
- 🟠 Reddit / 🟡 HN: no results this run (Reddit API paywall hit)
- Top voices: @YourStoryCo, @AverySoftware, @WamdaME; channels: Tech Tomlet, Tech With Tim, Retell AI

> Raw brief saved by the skill to `~/Documents/Last30Days/conversational-ai-voice-agents-raw.md`.
