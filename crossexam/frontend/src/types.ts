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
  /** Where on the page it lives. */
  bbox: BBox;
  /** Retrieval confidence in [0,1]. */
  confidence: number;
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
}

/** Reason the agent declined to surface a box. The honest-silence signal. */
export type SilenceReason = 'not_found_in_document';

/**
 * The wire frame published on the LiveKit data channel. Backend matches this
 * EXACTLY; the live handler in useCrossExam parses it field-by-field.
 *
 *   { citation, proactive?, latencyMs?, reason?, agentState?, caption? }
 *
 * - `citation: null` + `reason: "not_found_in_document"` => honest silence: the
 *   agent found no grounded source and stayed quiet rather than show a wrong box.
 * - `proactive: true` => the agent surfaced this WITHOUT being asked (ambient).
 * - `latencyMs` => retrieval latency for the persistent live latency badge.
 */
export interface LiveFrame {
  /** The retrieval result, or null when nothing grounded was found. */
  citation: Citation | null;
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
