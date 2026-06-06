# 🎙️ Moss Hackathon — Multi-Agent Debate → Winning Idea

A Claude Code **multi-agent team** that generates, attacks, fact-checks, and converges on one
demo-able idea for the **Moss Conversational AI Hackathon** (June 6–7, 2026, YC SF).

The team includes a dedicated **Devil's Advocate** teammate whose only job is to kill weak ideas
during idea generation. Ideas were grounded in live trends via the `/last30days` research skill.

---

## 🏆 The committed idea: **CrossExam**

> **Interrogate a 900-page document out loud. Moss finds the one relevant line in <10ms;
> Unsiloed snaps a bounding box onto the exact line of the scanned page as the agent answers.**

CrossExam won the internal debate over 7 other candidates because it is the only idea where Moss's
differentiator is **visible and photographable** on stage — the Devil's Advocate's decisive reframe:
*"if the differentiator can't be photographed, it can't win."*

| | Score |
|---|---|
| Demo-ability (Demo-Designer) | **9 / 10** |
| Fundable narrative (BizDev) | **62** (vs 51 runner-up) |
| Feasibility (Researcher) | Buildable in 24h (pre-index the PDF) |
| Devil's Advocate verdict | **FIX → SURVIVES** |

Full pitch: [`docs/pitch-crossexam.md`](docs/pitch-crossexam.md)

---

## 🤖 The agent team (`.claude/agents/`)

| Agent | Role |
|---|---|
| `orchestrator-judge.md` | Conducts the debate, simulates the 4 judge archetypes, forces convergence |
| `ideator.md` | Divergent, prolific idea generator |
| **`devils-advocate.md`** | Attacks every idea for fatal flaws, demo risk, "feature-not-a-company" |
| `researcher.md` | Live fact-checks sponsor APIs; runs `/last30days` for trend grounding |
| `demo-designer.md` | Designs the killer demo moment + 1–2 hero features |
| `bizdev-pitch.md` | Market, customer acquisition, validation, Problem→Team pitch |

> Subagents can't talk to each other directly, so the **orchestrator relays** each agent's output
> into the next agent's prompt (Ideator → Devil's Advocate → Researcher → Demo-Designer → BizDev).

### Re-run the debate
```
Use the ideator and devils-advocate agents to generate and pressure-test ideas for <new constraint>.
```

---

## 📂 Repo contents

```
moss-hackathon/
├── README.md                      ← you are here
├── .claude/agents/                ← the 6-agent team (project scope)
└── docs/
    ├── research-brief.md          ← the full sponsor + hackathon research brief
    ├── last30days-findings.md     ← live trend grounding (voice agents, last 30 days)
    ├── debate-transcript.md       ← the full 3-round debate, verbatim
    ├── pitch-crossexam.md         ← the committed idea + Problem→Team pitch
    └── build-plan.md              ← 24h critical path, must-fix risks, sponsor API map
```

---

## ⚠️ Top build risks (from the team)
1. `on_user_turn_completed` is a **LiveKit** hook, not Moss's — attribute correctly when briefing judges.
2. **Pre-index the PDF offline** — Unsiloed parse is async, can't run live. Retrieval + voice live only.
3. **Pin the bbox→canvas transform** (scale × page-offset × devicePixelRatio); test at projector resolution.
4. **Pin Moss package names in the first 30 min** — inconsistent across npm/PyPI.

---

*Built with a Claude Code agent team. Sponsor capability claims are vendor-stated unless verified — see `docs/build-plan.md`.*
