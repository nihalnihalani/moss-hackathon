# syntax=docker/dockerfile:1
# =============================================================================
# CrossExam backend — LiveKit voice-agent worker
# Multi-stage slim Python build. Build context = ./backend
# =============================================================================

# ---- Stage 1: builder — install deps into an isolated venv ------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Create a self-contained venv we can copy wholesale into the runtime stage.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install dependencies first (cached unless project metadata changes), then the
# package itself. Include the [voice] extra (Deepgram/OpenAI/Cartesia/Silero
# plugins) so the container can run a real STT->LLM->TTS session, not just mocks.
# Override with --build-arg INSTALL_EXTRAS="" for a smaller mock-only image.
ARG INSTALL_EXTRAS="[voice]"
COPY pyproject.toml README.md ./
COPY crossexam_backend ./crossexam_backend
RUN pip install ".${INSTALL_EXTRAS}"

# ---- Stage 2: runtime — minimal image, non-root -----------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# Non-root user for the worker.
RUN useradd --create-home --uid 10001 crossexam

WORKDIR /app

# Bring over the prebuilt venv and the runtime assets (package + mock fixtures).
COPY --from=builder /opt/venv /opt/venv
COPY crossexam_backend ./crossexam_backend
COPY fixtures ./fixtures

USER crossexam

# Runs the LiveKit worker; falls back to a helpful message + mock index when
# livekit-agents / credentials are absent (see crossexam_backend/server.py).
ENTRYPOINT ["python", "-m", "crossexam_backend.server"]
CMD ["dev"]
