# syntax=docker/dockerfile:1
# =============================================================================
# CrossExam pipeline — offline index-builder job
# Multi-stage slim Python build. Build context = ./pipeline
#
# This is a ONE-SHOT job image, not a long-running service: it parses PDFs into
# citation-grade chunks and (with Moss creds) builds the real Moss index that the
# backend then queries live. Run it before bringing the live stack up. Example:
#
#   docker build -t crossexam-pipeline:local ./pipeline
#   docker run --rm --env-file .env -v "$PWD/build:/work" crossexam-pipeline:local \
#       build-index --input /work/chunks.json --index-name crossexam-documents
#
# With no Moss creds (or --dry-run) it writes backend-compatible chunk JSON to
# disk instead of calling Moss, so it runs fully offline in CI/dev.
# =============================================================================

# ---- Stage 1: builder — install deps into an isolated venv ------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Self-contained venv we can copy wholesale into the runtime stage.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install the [pdf,moss] extras so one image can BOTH parse born-digital PDFs
# (reportlab/pypdf/pdfplumber) AND build the real Moss index ([moss] ->
# inferedge-moss + its native core). The Moss native core (inferedge-moss-core)
# ships as a prebuilt manylinux/macos wheel, so pip resolves it on
# python:3.12-slim with NO extra apt build tooling.
# Override with --build-arg INSTALL_EXTRAS="[pdf]" for an offline-only image
# (the build-index path falls back to disk JSON when inferedge-moss is absent).
ARG INSTALL_EXTRAS="[pdf,moss]"
COPY pyproject.toml README.md ./
COPY crossexam_pipeline ./crossexam_pipeline
RUN pip install ".${INSTALL_EXTRAS}"

# ---- Stage 2: runtime — minimal image, non-root -----------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# Non-root user for the job.
RUN useradd --create-home --uid 10001 crossexam

WORKDIR /app

# Bring over the prebuilt venv and the runtime assets (package + sample fixtures
# used by the deterministic --dry-run parser).
COPY --from=builder /opt/venv /opt/venv
COPY crossexam_pipeline ./crossexam_pipeline
COPY fixtures ./fixtures

USER crossexam

# Entrypoint is the CLI module; pass a subcommand (parse / build-index / ...) as
# the container command. Defaults to --help so a bare `docker run` is self-documenting.
ENTRYPOINT ["python", "-m", "crossexam_pipeline.cli"]
CMD ["--help"]
