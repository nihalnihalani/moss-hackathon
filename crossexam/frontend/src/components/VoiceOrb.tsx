import { useEffect, useRef } from 'react';
import type { AgentState } from '../types';

export interface VoiceOrbProps {
  state: AgentState;
  /** 0..1 amplitude to drive the pulse; mock mode synthesizes this from state. */
  level?: number;
}

const STATE_BASE_LEVEL: Record<AgentState, number> = {
  idle: 0.12,
  listening: 0.45,
  thinking: 0.3,
  speaking: 0.85,
};

/**
 * Audio-visualizer orb. Animates a soft pulsing glow whose intensity tracks the
 * agent state (or a real audio level if provided). Pure canvas, no deps.
 */
export function VoiceOrb({ state, level }: VoiceOrbProps): JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const stateRef = useRef<AgentState>(state);
  const levelRef = useRef<number | undefined>(level);

  stateRef.current = state;
  levelRef.current = level;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const size = 220;
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    ctx.scale(dpr, dpr);

    let raf = 0;
    let t = 0;

    const draw = (): void => {
      t += 0.04;
      const s = stateRef.current;
      const target = levelRef.current ?? STATE_BASE_LEVEL[s];
      const wobble = s === 'idle' ? 0.02 : 0.12;
      const amp = target + Math.sin(t) * wobble;
      const cx = size / 2;
      const cy = size / 2;
      const baseR = 52;
      const r = baseR + amp * 34;

      ctx.clearRect(0, 0, size, size);

      // Outer glow rings.
      for (let i = 3; i >= 1; i--) {
        const rr = r + i * (10 + amp * 18);
        const g = ctx.createRadialGradient(cx, cy, rr * 0.4, cx, cy, rr);
        g.addColorStop(0, `rgba(99,179,255,${0.06 * amp})`);
        g.addColorStop(1, 'rgba(99,179,255,0)');
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.arc(cx, cy, rr, 0, Math.PI * 2);
        ctx.fill();
      }

      // Core orb.
      const core = ctx.createRadialGradient(cx - 12, cy - 12, 6, cx, cy, r);
      core.addColorStop(0, '#cfe8ff');
      core.addColorStop(0.55, '#5aa9ff');
      core.addColorStop(1, '#2f6fd0');
      ctx.fillStyle = core;
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.fill();

      raf = requestAnimationFrame(draw);
    };
    draw();
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <canvas
      ref={canvasRef}
      className={`voice-orb voice-orb--${state}`}
      style={{ width: 220, height: 220 }}
      role="img"
      aria-label={`Voice activity orb, agent is ${state}`}
    />
  );
}
