import { motion } from 'framer-motion';

export function Logo() {
  return (
    <div className="brand-logo" aria-hidden="true" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <svg width="28" height="28" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <filter id="amber-glow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="2" result="blur" />
            <feComposite in="SourceGraphic" in2="blur" operator="over" />
          </filter>
          <filter id="ambient-blur" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="8" />
          </filter>
        </defs>

        {/* Ambient glow behind the logo */}
        <circle cx="16" cy="16" r="10" fill="var(--amber)" filter="url(#ambient-blur)" opacity="0.25" />

        {/* Outer targeting ring (slow rotate) */}
        <motion.g
          animate={{ rotate: 360 }}
          transition={{ duration: 25, repeat: Infinity, ease: "linear" }}
          style={{ transformOrigin: "16px 16px" }}
        >
          <circle cx="16" cy="16" r="14" stroke="var(--text-mute)" strokeWidth="1.2" strokeDasharray="2 6" strokeLinecap="round" />
          <circle cx="16" cy="2" r="1.5" fill="var(--text-dim)" />
          <circle cx="16" cy="30" r="1.5" fill="var(--text-dim)" />
          <circle cx="2" cy="16" r="1.5" fill="var(--text-dim)" />
          <circle cx="30" cy="16" r="1.5" fill="var(--text-dim)" />
        </motion.g>

        {/* Inner diamond / document bounds */}
        <motion.rect
          x="9" y="9" width="14" height="14" rx="2.5"
          transform="rotate(45 16 16)"
          stroke="var(--text)" strokeWidth="1.5"
          fill="rgba(255, 255, 255, 0.03)"
          initial={{ scale: 0.8, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
          style={{ transformOrigin: "16px 16px" }}
        />

        {/* Crosshairs (The "Cross") */}
        <g stroke="var(--amber)" strokeWidth="1.5" strokeLinecap="round">
          <path d="M16 5V11" />
          <path d="M16 21V27" />
          <path d="M5 16H11" />
          <path d="M21 16H27" />
        </g>

        {/* Core focus point */}
        <motion.circle
          cx="16" cy="16" r="2.5"
          fill="var(--amber)"
          filter="url(#amber-glow)"
          animate={{ scale: [1, 1.4, 1], opacity: [0.8, 1, 0.8] }}
          transition={{ duration: 2.5, repeat: Infinity, ease: "easeInOut" }}
          style={{ transformOrigin: "16px 16px" }}
        />
      </svg>
    </div>
  );
}
