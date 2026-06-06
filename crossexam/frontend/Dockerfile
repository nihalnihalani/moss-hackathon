# syntax=docker/dockerfile:1
# =============================================================================
# CrossExam frontend — Vite/React build served as static files via nginx
# Multi-stage: node build -> nginx static serve. Build context = ./frontend
# =============================================================================

# ---- Stage 1: build the static bundle ---------------------------------------
FROM node:22-slim AS build

WORKDIR /app

# Install deps from the lockfile (reproducible). Cached unless lockfile changes.
COPY package.json package-lock.json ./
RUN npm ci

# Build the static site. VITE_* vars are baked in at build time; pass them with
# `--build-arg` if you need a non-mock production bundle.
ARG VITE_API_URL
ARG VITE_LIVEKIT_URL
ARG VITE_LIVEKIT_TOKEN
ARG VITE_PDF_URL
ARG VITE_MOCK_MODE
COPY . .
RUN npm run build

# ---- Stage 2: static serve --------------------------------------------------
FROM nginx:1.27-alpine AS runtime

# SPA routing: fall back to index.html for client-side routes.
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD wget -qO- http://localhost/ >/dev/null 2>&1 || exit 1

CMD ["nginx", "-g", "daemon off;"]
