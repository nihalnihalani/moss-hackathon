# Research Brief — Moss Conversational AI Hackathon (June 6–7, 2026, YC SF)

The foundational research that seeded the agent team. Sponsor capabilities, judging dynamics, and
the Claude Code multi-agent design. **All sponsor latency / token-savings / win-rate figures are
vendor-stated CLAIMS unless independently verified** (see `build-plan.md`).

## TL;DR
- The hackathon rewards a **killer real-time voice demo grounded in Moss's sub-10ms retrieval**,
  layered with creative use of at least one sponsor's "unpopular" API feature. First place = a YC
  partner interview.
- The Claude Code prompt defines ~6 differently-tempered subagents in `.claude/agents/`, installs
  the `last30days` plugin for live trend grounding, and orchestrates a structured debate that
  converges on one demo-able idea (4 judge types, killer demo moment, customer validation,
  Problem→Team pitch format).

## Sponsors (capabilities)

### Moss (moss.dev / usemoss — YC F25, InferEdge Inc.)
Real-time semantic search **runtime** (not a database), runs **in-process** (browser/edge/device/
cloud) in Rust+WASM so retrieval never leaves the process. Founders ex-Grammarly/Microsoft.
- **Latency (Moss's own benchmark):** p50 3.1ms / p95 4.3ms / p99 5.4ms; claims 100–200× faster
  than typical cloud vector DBs. **70–90% token savings** (company-stated, from pilots).
- API: `MossClient(PROJECT_ID, PROJECT_KEY)` → `create_index`, `load_index`, `query` →
  `QueryOptions(top_k, alpha [default 0.8], filter, embedding)`; hybrid semantic+BM25; metadata
  filtering. Packages: Python `moss`; Node `@moss-dev/moss`; browser WASM `@moss-dev/moss-web`.
- **LiveKit integration (key for the demo):** override `on_user_turn_completed()` and inject Moss
  results as a `role="system"` message each turn — "no tool-calling overhead, no dead air." Also a
  `moss-agent` PyPI package (hot index cache shared across rooms).
- ⚠️ Package/import names inconsistent across sources — verify live.

### LiveKit (Apache 2.0) — `livekit-agents` 1.5.16 (June 1, 2026)
STT→LLM→TTS or native speech-to-speech (<600ms). **Unpopular features judges reward:** semantic
turn detection (open-weights transformer, not VAD); native MCP (one line); built-in LLM-judge test
framework; multi-agent handoff; **frontend tool-forwarding**; SIP telephony; LiveKit Inference;
WebGL audio visualizers; **agent state attributes** auto-published to frontend.

### AWS — Amazon Nova 2 Sonic (Bedrock, `amazon.nova-2-sonic-v1:0`)
Unified speech-to-speech, bidirectional streaming, function calling, agentic RAG with Bedrock KBs,
barge-in, 300K context, **asynchronous tool calling** (conversation continues while tools run),
multilingual. Win-rates over GPT-4o Realtime / Gemini are AWS-stated and vary by source.

### MiniMax — Speech 2.6
Real-time streaming, **voice cloning from ~10s of audio**, **voice-design-from-text-prompt**, 300+
voices, 40 languages, 7 emotions, voice mixing. LLM line: MiniMax M3/M2.x.

### Unsiloed (YC F25)
Vision-first PDF parsing. **Parse** (PDF→Markdown), **Extract** (schema→JSON with **word-level
citations, bounding boxes, confidence scores**), **Split**. Async job + poll. Outperforms
LlamaIndex/Gemini/Mistral/Unstructured on public benchmarks (vendor).

### TrueFoundry — AI / Agent Gateway (Agent Gateway launched June 2, 2026)
~3–4ms gateway latency. **Virtual MCP Servers** (aggregate many tools into one endpoint),
per-team budgets, model fallback / TrueFailover, **PII redaction at the gateway**, user-level OAuth
identity injection, request-level tracing.

## last30days skill (mvanhorn/last30days-skill)
Researches a topic across Reddit, X, YouTube, TikTok, Instagram, Bluesky, HN, Polymarket, GitHub,
web over the last 30 days; an "AI judge" synthesizes a cited brief.
- Install: `/plugin marketplace add mvanhorn/last30days-skill` then `/plugin install last30days`.
- Invoke: `/last30days <topic>` with flags `--days=N`, `--deep`/`--quick`, `--competitors=N`.

## Claude Code multi-agent features
- **Subagents** = isolated instances via the Task tool, own context/prompt/tools; only a summary
  returns to the parent. **Custom agents** = Markdown + YAML frontmatter in `.claude/agents/`
  (project) or `~/.claude/agents/` (user): `name`, `description` (lead with "MUST BE USED for X"),
  `tools`, `model`.
- **Key limit:** subagents can't talk to each other — the **orchestrator relays** each output into
  the next agent's prompt. (Cross-session agent *teams* exist but are a heavier research preview.)

## Recent trends (Apr–Jun 2026)
- **Retrieval is the new bottleneck, not the voice model** — voice AI needs sub-100ms retrieval to
  hit <800ms total; 3s end-to-end is a dealbreaker.
- Agent memory matured (Mem0); hybrid on-device + cloud; AI-native knowledge systems (Karpathy
  "llm-wiki"); native S2S models (Nova 2 Sonic, OpenAI Realtime, Gemini Live).
- **EU AI Act Article 50 transparency** applies from **Aug 2, 2026** (transitional period to
  Dec 2, 2026 for systems already on market). Fines up to €15M or 3% of global turnover.

## Hackathon-winning principles (Gary-Yau Chan — 55 hackathons / 26 wins)
- **4 judge types:** API evangelists (reward the *unpopular* API feature, used creatively);
  CTOs (must actually build it, expect scalability questions); investors (market size, comparables);
  BizDev ("can I picture myself as the user?" + customer-acquisition roadmap).
- **Team:** a **front-end engineer** carries UI/UX in 24h; a **BizDev** presenter does validation.
- **Killer demo:** a skit / interactive moment / audience involvement.
- **Pitch order:** Problem → Solution → Market → Validation → Demo → Business Model → Future → Team.

## The agent team this produced
`orchestrator-judge`, `ideator`, **`devils-advocate`**, `researcher`, `demo-designer`,
`bizdev-pitch` — see `../.claude/agents/`. Debate outcome in `debate-transcript.md`.
