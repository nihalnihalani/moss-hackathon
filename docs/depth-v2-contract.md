# CrossExam Depth-v2 — Shared Data Contract

The single source of truth for the 5 new features. Backend + frontend MUST match this exactly.
Coordinates stay **PDF points, top-left origin, with page_width/page_height** (unchanged).

## Models

### BBox (unchanged)
`{ page:int, x0, y0, x1, y1, page_width, page_height }`  — all floats in PDF points.

### Citation (extended)
```
Citation = {
  id: string,
  text: string,
  bbox: BBox,                 // the UNION bounding rect (back-compat; used for page-jump + label)
  quads?: BBox[],             // NEW (feat 2): per-line boxes hugging the actual glyphs; render these
  confidence: number,
  score: number,
  faithfulness?: { supported: boolean, score: number, method: string },
  documentId: string,         // NEW (feat 1): which document this came from
  documentTitle?: string,     // NEW (feat 1): human label for the doc switcher
  scanned?: boolean,          // NEW (feat 3): source page was a scan (OCR) -> "scanned source" badge
}
```

### MemoryRef (NEW — feat 5)
```
MemoryRef = { kind: "recall", citationId: string, documentId: string, page: int, note: string }
// e.g. note = "as we saw on page 12"
```

### HopTrace (NEW — feat 1, the agentic decomposition trail)
```
HopTrace = { subQuery: string, citationIds: string[] }
```

### Speaker (NEW — feat 4, meeting mode)
```
Speaker = { id: string, label: string }   // e.g. {id:"spk_1", label:"Counsel"}
```

## Wire frame (data channel JSON) — REPLACES the single-citation frame
```
Frame = {
  citations: Citation[],          // 0..N — multi-hop can return several across docs/pages
  primaryId?: string,             // which citation to page-jump to first
  contradiction?: boolean,        // NEW (feat 1): the citations conflict (cross-page/cross-doc)
  hops?: HopTrace[],              // NEW (feat 1): decomposition trail ("how I found this")
  memory?: MemoryRef[],           // NEW (feat 5): recalls referenced this turn
  speaker?: Speaker,              // NEW (feat 4): who triggered it (meeting mode)
  proactive?: boolean,            // unprompted surfacing
  latencyMs?: number,             // retrieval latency (live badge)
  reason?: "not_found_in_document", // honest silence (citations empty)
  agentState?: string,
  caption?: string,
}
```
Back-compat: a single-citation answer is just `citations:[c]` with `primaryId=c.id`.
Not-found stays `{ citations:[], reason:"not_found_in_document", latencyMs }`.

## Feature → contract mapping
1. **Multi-doc + multi-hop:** `citations[]` across `documentId`s/pages, `hops[]` trail, `contradiction` flag, `primaryId`. Pipeline indexes multiple PDFs; retrieval decomposes the query, retrieves per sub-query, fuses, and flags conflicting citations.
2. **Quad highlights:** `Citation.quads[]` (per-line point boxes). Frontend renders each quad (hug text across wraps); `bbox` remains the union for jump/label.
3. **Scanned/OCR:** `Citation.scanned`. Pipeline uses the Unsiloed vision path for scans (region boxes), sets `scanned:true`; frontend shows a "scanned source" badge.
4. **Audio-reactive + meeting mode:** `speaker` on the frame; frontend orb driven by real WebAudio RMS (mic in listening, TTS out in speaking), state-anim fallback in mock; proactive surfacing per speaker.
5. **Memory / multi-turn:** `memory[]` recalls; backend dedupes (never re-surface the same `citationId` in a session) and emits a recall note instead.

## Invariants
- Every Citation carries `documentId` (single-doc demo = one id like `deposition-holloway`).
- `quads`, when present, are within the same page as `bbox.page`; each quad is a valid points rect.
- Frontend renders all citations whose `bbox.page === currentPage`; the doc switcher changes `documentId`.
- All new fields optional except `documentId` on Citation and `citations[]` on the frame.
