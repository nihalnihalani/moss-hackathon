/**
 * Shared CrossExam types. These mirror the backend (Unsiloed) shapes so the
 * frontend can consume a citation payload over the LiveKit data channel verbatim.
 */

/** Agent lifecycle state, surfaced on the state pill. */
export type AgentState = 'idle' | 'listening' | 'thinking' | 'speaking';

/**
 * A bounding box in PDF point-space (the native coordinate system Unsiloed emits).
 *
 * PDF points: 72 points = 1 inch. Origin is the PDF page origin. CrossExam treats
 * (x0,y0) as the TOP-LEFT corner and (x1,y1) as the BOTTOM-RIGHT corner, with y
 * increasing DOWNWARD — i.e. a top-left image-style coordinate system, which is what
 * Unsiloed's parser returns. (If a source uses PDF's native bottom-left origin, flip y
 * before constructing this BBox.)
 */
export interface BBox {
  /** 1-based page number the box lives on. */
  page: number;
  /** Left edge, PDF points. */
  x0: number;
  /** Top edge, PDF points (y grows downward). */
  y0: number;
  /** Right edge, PDF points. */
  x1: number;
  /** Bottom edge, PDF points (y grows downward). */
  y1: number;
  /**
   * Page width in PDF points (snake_case to match the backend/pipeline wire
   * shape). Optional: the live citation payload carries it, but bbox.ts takes
   * page dimensions from the render geometry, not the box.
   */
  page_width?: number;
  /** Page height in PDF points (snake_case to match the wire shape). */
  page_height?: number;
}

/**
 * Faithfulness / grounded-confidence signal attached to a citation: did the
 * agent's claim stay faithful to the cited source text, and how strongly?
 *
 * `supported` is the binary trust gate (drives the amber-vs-muted indicator);
 * `score` is the grounded-confidence in [0,1] (rendered as "grounded 0.99");
 * `method` names the check that produced it (e.g. "nli", "embedding"), surfaced
 * as a tooltip for provenance.
 */
export interface Faithfulness {
  /** Whether the claim is judged supported by the cited text. */
  supported: boolean;
  /** Grounded-confidence in [0,1]. */
  score: number;
  /** The verifier that produced the judgement (e.g. "nli"). */
  method: string;
}

/** A retrieval result: the line the agent is citing, with its box and provenance. */
export interface Citation {
  /** Stable id for keying/animation. */
  id: string;
  /** The exact quoted text the box wraps. */
  text: string;
  /** The UNION bounding rect (back-compat; used for page-jump + label). */
  bbox: BBox;
  /**
   * NEW (feat 2): per-line boxes hugging the actual glyphs across line wraps.
   * When present, the canvas renders ONE amber rect per quad (hugging real
   * text); `bbox` remains the union used for the page-jump + the p.N·% label.
   * Each quad is a valid points rect on the same page as `bbox.page`.
   */
  quads?: BBox[];
  /** Retrieval confidence in [0,1]. */
  confidence: number;
  /**
   * Retrieval score (relevance), distinct from confidence. Optional on the wire.
   */
  score?: number;
  /** Retrieval latency in milliseconds (drives the "· 7ms" chip). */
  latencyMs: number;
  /** Total corpus size searched (drives "found in 912 pages"). */
  pagesSearched: number;
  /**
   * Optional grounded-confidence check. Present when the backend ran a
   * faithfulness verifier over the answer + cited text. Drives the small
   * "grounded 0.99" indicator on the citation card.
   */
  faithfulness?: Faithfulness;
  /**
   * NEW (feat 1): which document this citation came from. Maps to a PDF url via
   * the doc switcher. Required by contract (single-doc demo = one id).
   */
  documentId: string;
  /** NEW (feat 1): human label for the doc switcher tabs. */
  documentTitle?: string;
  /**
   * NEW (feat 3): the source page was a scan (OCR). Drives the "SCANNED SOURCE"
   * badge on the citation card + near the page.
   */
  scanned?: boolean;
}

/**
 * NEW (feat 5): a memory recall. When the agent has already surfaced a citation
 * this session, it emits a recall note ("as we saw on page 12") INSTEAD of
 * re-snapping the box. Rendered as a clickable chip in the transcript.
 */
export interface MemoryRef {
  /** Discriminant. */
  kind: 'recall';
  /** The citation being recalled (clicking the chip jumps to it). */
  citationId: string;
  /** Which document the recalled citation lives in. */
  documentId: string;
  /** Page of the recalled citation. */
  page: number;
  /** Human recall note, e.g. "as we saw on page 12". */
  note: string;
}

/**
 * NEW (feat 1): the agentic decomposition trail. Each hop is a sub-query the
 * agent decomposed the question into, plus the citations it retrieved for it —
 * surfaced as the "how I found this" trail under a contradiction.
 */
export interface HopTrace {
  /** The sub-query the agent posed. */
  subQuery: string;
  /** Citation ids retrieved for this sub-query. */
  citationIds: string[];
}

/**
 * NEW (feat 4, meeting mode): who triggered a frame. When a speaker is present
 * the transcript labels lines and a small "MEETING" indicator appears.
 */
export interface Speaker {
  /** Stable speaker id (e.g. "spk_1"). */
  id: string;
  /** Human label, e.g. "Counsel", "Witness". */
  label: string;
}

/** Reason the agent declined to surface a box. The honest-silence signal. */
export type SilenceReason = 'not_found_in_document';

/**
 * The depth-v2 wire frame published on the LiveKit data channel. Backend matches
 * this EXACTLY; the live handler in useCrossExam parses it field-by-field per
 * /docs/depth-v2-contract.md.
 *
 *   { citations: Citation[], primaryId?, contradiction?, hops?, memory?,
 *     speaker?, proactive?, latencyMs?, reason?, agentState?, caption? }
 *
 * - `citations: []` + `reason: "not_found_in_document"` => honest silence: the
 *   agent found no grounded source and stayed quiet rather than show a wrong box.
 * - `primaryId` => which citation to page-jump to first.
 * - `contradiction: true` => the citations conflict (cross-page/cross-doc).
 * - `hops` => the decomposition trail ("how I found this").
 * - `memory` => recalls referenced this turn (render recall chips, no re-snap).
 * - `speaker` => who triggered it (meeting mode).
 * - `proactive: true` => the agent surfaced this WITHOUT being asked (ambient).
 * - `latencyMs` => retrieval latency for the persistent live latency badge.
 *
 * Back-compat: a single-citation answer is `citations:[c]` with `primaryId=c.id`.
 */
export interface Frame {
  /** 0..N citations — multi-hop can return several across docs/pages. */
  citations: Citation[];
  /** Which citation to page-jump to first. */
  primaryId?: string;
  /** The citations conflict (cross-page/cross-doc). */
  contradiction?: boolean;
  /**
   * NEW (killer feature B): the shared ANCHOR the conflicting citations hang off
   * — a clause number, term, or subject (e.g. "§4.2 Subcontracting"). Drives the
   * "CONFLICT — Anchor: …" banner. Optional; absent for a plain contradiction.
   */
  anchor?: string;
  /** The agentic decomposition trail. */
  hops?: HopTrace[];
  /** Recalls referenced this turn. */
  memory?: MemoryRef[];
  /** Who triggered the frame (meeting mode). */
  speaker?: Speaker;
  /** The agent surfaced this unprompted (ambient co-pilot behavior). */
  proactive?: boolean;
  /** Retrieval latency in milliseconds for this frame. */
  latencyMs?: number;
  /** Why no citation was surfaced (honest-silence states). */
  reason?: SilenceReason;
  /** Agent lifecycle state for the orb/pill. */
  agentState?: AgentState;
  /** Spoken caption text. */
  caption?: string;
}

/** A rectangle in canvas/CSS pixel space, ready to position an overlay div. */
export interface CanvasRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/**
 * Geometry needed to map a PDF-point bbox onto the rendered canvas.
 * All fields describe the page exactly as it was rendered on screen.
 */
export interface PageRenderGeometry {
  /** Intrinsic page width in PDF points (pdf.js: viewport at scale 1). */
  pageWidthPt: number;
  /** Intrinsic page height in PDF points. */
  pageHeightPt: number;
  /**
   * Scale applied when rendering the page to the canvas backing store
   * (pdf.js viewport scale). A renderScale of 2 means each PDF point became
   * 2 device pixels in the canvas bitmap.
   */
  renderScale: number;
  /**
   * Offset of the rendered page's top-left corner from the overlay container's
   * top-left, in CSS pixels. Lets the page sit anywhere inside a scroll/letterbox.
   */
  canvasOffset: { x: number; y: number };
  /**
   * Device pixel ratio the canvas backing store was sized for. The backing
   * store has renderScale*dpr pixels per point, but CSS lays it out at
   * renderScale CSS-px per point — so the overlay (positioned in CSS px) must
   * NOT multiply by dpr. We accept it to validate/normalize and to support
   * callers that pass a canvas measured in device pixels.
   */
  devicePixelRatio: number;
}
