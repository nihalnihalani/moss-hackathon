import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// CrossExam dev server. Runs standalone in mock mode (no backend keys required).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
  },
  // pdfjs-dist ships a worker we load via ?url; keep it un-optimized to avoid CJS interop noise.
  optimizeDeps: {
    exclude: ['pdfjs-dist'],
  },
});
