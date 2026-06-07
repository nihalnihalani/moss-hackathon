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
# package itself. Include the [api,voice,moss] extras so ONE image can run BOTH
# the LiveKit voice worker (Deepgram/OpenAI/Cartesia/Silero plugins for a real
# STT->LLM->TTS session) AND the FastAPI HTTP service (`crossexam-api`:
# token minting via livekit-api + PDF upload via pdfplumber), AND the real Moss
# in-process retrieval path ([moss] -> inferedge-moss + its native core).
#
# The Moss native core (inferedge-moss-core) ships as a prebuilt manylinux/macos
# wheel, so pip resolves it on python:3.12-slim with NO extra apt build tooling
# (no gcc/rust toolchain needed). If a future release ever lacks a slim-compatible
# wheel, prefer pinning to a version that has one over adding a build toolchain.
#
# Override with --build-arg INSTALL_EXTRAS="[voice]" (worker-only, no Moss) or
# INSTALL_EXTRAS="" for a smaller mock-only image (the app falls back to MockIndex
# when inferedge-moss is absent — see retrieval/factory.py).
ARG INSTALL_EXTRAS="[api,voice,moss]"
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

# Pre-download the LiveKit voice model weights (turn-detector multilingual model +
# Silero VAD) into the image so a live voice worker starts immediately with no
# first-run network fetch. Run as the runtime user so the cache lands in this
# user's HOME and persists in the image. Tolerant: a mock-only image (built with
# INSTALL_EXTRAS="" so the voice plugins are absent) has nothing to fetch, and a
# missing turn-detector model degrades to VAD turn detection at runtime
# (see crossexam_backend/server.py _build_turn_detection).
RUN python -m livekit.agents download-files || true

# Runs the LiveKit worker; falls back to a helpful message + mock index when
# livekit-agents / credentials are absent (see crossexam_backend/server.py).
ENTRYPOINT ["python", "-m", "crossexam_backend.server"]
CMD ["dev"]
