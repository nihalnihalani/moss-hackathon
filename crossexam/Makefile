# =============================================================================
# CrossExam — developer runbook (make targets)
# =============================================================================
# Quickstart:
#   make setup   # install backend + pipeline + frontend deps
#   make test    # run all test suites (mock mode, no keys needed)
#   make dev     # run the backend worker + frontend dev server
#
# Targets are tolerant of missing tools: if a tool is absent the target prints
# install guidance and skips rather than failing the whole run.
# =============================================================================

SHELL := /bin/bash
ROOT  := $(CURDIR)
BACKEND   := $(ROOT)/backend
PIPELINE  := $(ROOT)/pipeline
FRONTEND  := $(ROOT)/frontend

# Load local developer settings when present so Make variables such as API_PORT
# stay in sync with Docker Compose. Do not export the whole file globally:
# hermetic test targets intentionally exercise "no keys" branches.
ifneq (,$(wildcard $(ROOT)/.env))
include $(ROOT)/.env
endif

# Prefer the project venv when present, then fall back to python3/python.
VENV_PY := $(ROOT)/.venv/bin/python
PY := $(shell if [ -x "$(VENV_PY)" ]; then printf '%s\n' "$(VENV_PY)"; else command -v python3 2>/dev/null || command -v python 2>/dev/null; fi)
NPM := $(shell command -v npm 2>/dev/null)

# Where `make index` writes its intermediate chunk JSON and final index records.
INDEX_CHUNKS := $(PIPELINE)/build/chunks.json
INDEX_OUT    := $(PIPELINE)/build/index_records.json

.DEFAULT_GOAL := help

.PHONY: help setup setup-backend setup-pipeline setup-frontend \
        dev dev-live api verify-live test test-backend test-pipeline test-frontend \
        install-moss test-moss \
        lint lint-py lint-frontend fmt typecheck typecheck-py typecheck-frontend \
        eval bench build index clean

# API host/port for local `make api` and the `verify-live` curls. Override on
# the command line, e.g. `make verify-live API_PORT=9000`.
API_HOST ?= 127.0.0.1
API_PORT ?= 8000
API_BASE := http://$(API_HOST):$(API_PORT)

## help: show this help
help:
	@echo "CrossExam make targets:"
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/## /  /'

# ---- guards -----------------------------------------------------------------
define need_py
	@if [ -z "$(PY)" ]; then \
		echo ">> python3 not found. Install Python 3.10+ from https://python.org and re-run."; \
		exit 1; \
	fi
endef

define need_npm
	@if [ -z "$(NPM)" ]; then \
		echo ">> npm not found. Install Node.js 20+ from https://nodejs.org and re-run."; \
		exit 1; \
	fi
endef

# =============================================================================
# setup
# =============================================================================
## setup: install all deps (backend + pipeline + frontend, dev extras)
setup: setup-backend setup-pipeline setup-frontend

## setup-backend: pip install -e backend[dev]
setup-backend:
	$(call need_py)
	$(PY) -m pip install -e "$(BACKEND)[dev]"

## setup-pipeline: pip install -e pipeline[dev]
setup-pipeline:
	$(call need_py)
	$(PY) -m pip install -e "$(PIPELINE)[dev]"

## setup-frontend: npm ci in frontend
setup-frontend:
	$(call need_npm)
	cd "$(FRONTEND)" && $(NPM) ci

# =============================================================================
# moss (real in-process retrieval SDK — OPTIONAL; mock path needs none of this)
# =============================================================================
## install-moss: install backend + pipeline WITH the real Moss SDK ([dev,moss]).
## Resolves inferedge-moss + its prebuilt native core. The app still runs without
## it (MockIndex fallback); install only for the LIVE retrieval path.
install-moss:
	$(call need_py)
	@echo ">> installing crossexam-backend[dev,moss] + crossexam-pipeline[dev,moss]"
	$(PY) -m pip install -e "$(BACKEND)[dev,moss]" -e "$(PIPELINE)[dev,moss]"

## test-moss: run BOTH pytest suites with the real Moss SDK present.
## Exercises the real-SDK tests gated by pytest.importorskip("inferedge_moss").
## Needs NO API keys (those tests make no network calls). Run `make install-moss`
## first (or use the project venv at .venv where moss is already installed).
test-moss:
	$(call need_py)
	@if ! $(PY) -c "import inferedge_moss" >/dev/null 2>&1; then \
		echo ">> inferedge_moss not importable. Run 'make install-moss' first"; \
		echo "   (or activate the project venv: source .venv/bin/activate)."; \
		exit 1; \
	fi
	@echo ">> backend + pipeline pytest WITH real Moss SDK (no keys needed)"
	cd "$(BACKEND)"  && $(PY) -m pytest
	cd "$(PIPELINE)" && $(PY) -m pytest

# =============================================================================
# dev
# =============================================================================
## dev: run backend worker + frontend dev server together (Ctrl-C stops both)
## (for the full live stack incl. the HTTP API, use `make dev-live`)
dev:
	$(call need_py)
	$(call need_npm)
	@echo ">> starting backend worker + frontend dev server (mock mode unless .env set)"
	@echo ">> (no HTTP API here — token/upload need it; use 'make dev-live' or 'make api')"
	@trap 'kill 0' EXIT INT TERM; \
	( $(PY) -m crossexam_backend.server dev & ) ; \
	cd "$(FRONTEND)" && $(NPM) run dev

## api: run the FastAPI HTTP service (crossexam-api) locally on $(API_BASE)
## Needs the [api] extra:  pip install -e backend[api,voice]  (see KEYS.md).
api:
	$(call need_py)
	@echo ">> starting crossexam-api on $(API_BASE) (mock mode unless .env set)"
	cd "$(BACKEND)" && API_HOST=$(API_HOST) API_PORT=$(API_PORT) $(PY) -m crossexam_backend.api

## dev-live: run HTTP API + voice worker + frontend dev server together (Ctrl-C stops all)
## This is the full "drop in keys -> it's real" loop (token + upload + voice + UI).
dev-live:
	$(call need_py)
	$(call need_npm)
	@echo ">> starting HTTP API + voice worker + frontend dev server"
	@echo ">> (mock mode unless .env provides Moss/LiveKit/provider keys)"
	@trap 'kill 0' EXIT INT TERM; \
	( cd "$(BACKEND)" && API_HOST=$(API_HOST) API_PORT=$(API_PORT) $(PY) -m crossexam_backend.api & ) ; \
	( $(PY) -m crossexam_backend.server dev & ) ; \
	cd "$(FRONTEND)" && $(NPM) run dev

# =============================================================================
# test
# =============================================================================
## test: run all test suites (pytest backend + pipeline, vitest frontend)
test: test-backend test-pipeline test-frontend

## test-backend: pytest the backend package
test-backend:
	$(call need_py)
	cd "$(BACKEND)" && $(PY) -m pytest

## test-pipeline: pytest the pipeline package
test-pipeline:
	$(call need_py)
	cd "$(PIPELINE)" && $(PY) -m pytest

## test-frontend: vitest run
test-frontend:
	$(call need_npm)
	cd "$(FRONTEND)" && $(NPM) test

# =============================================================================
# lint / fmt / typecheck
# =============================================================================
## lint: ruff check (python) + eslint (frontend)
lint: lint-py lint-frontend

lint-py:
	$(call need_py)
	@echo ">> ruff (strict; pinned ruff==0.15.0)"
	cd "$(BACKEND)"  && $(PY) -m ruff check crossexam_backend
	cd "$(PIPELINE)" && $(PY) -m ruff check crossexam_pipeline

lint-frontend:
	$(call need_npm)
	cd "$(FRONTEND)" && $(NPM) run lint

## fmt: ruff format (python) + prettier (frontend)
fmt:
	$(call need_py)
	cd "$(BACKEND)"  && $(PY) -m ruff format .
	cd "$(PIPELINE)" && $(PY) -m ruff format .
	$(call need_npm)
	cd "$(FRONTEND)" && npx prettier --write "src/**/*.{ts,tsx,css}"

## typecheck: mypy (python) + tsc --noEmit (frontend)
typecheck: typecheck-py typecheck-frontend

typecheck-py:
	$(call need_py)
	@echo ">> mypy (strict; pinned mypy==1.19.1)"
	cd "$(BACKEND)"  && $(PY) -m mypy crossexam_backend
	cd "$(PIPELINE)" && $(PY) -m mypy crossexam_pipeline

typecheck-frontend:
	$(call need_npm)
	cd "$(FRONTEND)" && $(NPM) run typecheck

# =============================================================================
# verify-live
# =============================================================================
## verify-live: preflight (doctor) + a real Moss smoke when creds are present.
## Prints the READY/MISSING/MOCK table; if MOSS_PROJECT_ID and MOSS_PROJECT_KEY
## are in the env it adds `doctor --probe` (a REAL load_index/get_index round-trip,
## needs the [moss] extra). With no creds it prints a clear note and still
## exits 0 — this target is a read-out, not a hard gate. Then it probes
## /healthz and /config at $(API_BASE). If an API is reachable but does not look
## like CrossExam, it exits nonzero so a port conflict cannot masquerade as live.
verify-live:
	$(call need_py)
	@echo ">> crossexam-backend preflight (doctor — no network calls):"
	@cd "$(BACKEND)" && $(PY) -m crossexam_backend.doctor || true
	@echo ""
	@if [ -n "$(MOSS_PROJECT_ID)" ] && [ -n "$(MOSS_PROJECT_KEY)" ]; then \
		echo ">> Moss creds present — running a REAL load_index probe (doctor --probe):"; \
		cd "$(BACKEND)" && $(PY) -m crossexam_backend.doctor --probe || true; \
	else \
		echo ">> MOSS_PROJECT_ID / MOSS_PROJECT_KEY not set — skipping the live Moss probe."; \
		echo "   Set them (and 'make install-moss', USE_MOCKS=false) to smoke the real index."; \
	fi
	@echo ""
	@echo ">> probing HTTP API at $(API_BASE) (best-effort):"
	@if ! command -v curl >/dev/null 2>&1; then \
		echo "   curl not found — skipping endpoint probe (install curl to enable)."; \
	elif health="$$(curl -fsS --max-time 3 "$(API_BASE)/healthz" 2>/dev/null)"; then \
		if ! config="$$(curl -fsS --max-time 3 "$(API_BASE)/config" 2>/dev/null)"; then \
			echo "   GET /healthz -> $$health"; \
			echo "   GET /config  -> FAILED"; \
			echo "   A service is reachable at $(API_BASE), but it is not serving CrossExam /config."; \
			echo "   Stop the conflicting service, run 'make api', or pass API_PORT=..."; \
			exit 1; \
		fi; \
		if [[ "$$health" != *'"mode"'* || "$$health" != *'"livekit_configured"'* ]]; then \
			echo "   GET /healthz -> $$health"; \
			echo "   Unexpected /healthz payload. This does not look like crossexam-api."; \
			echo "   Stop the conflicting service, run 'make api', or pass API_PORT=..."; \
			exit 1; \
		fi; \
		if [[ "$$config" != *'"live"'* || "$$config" != *'"livekit_url"'* ]]; then \
			echo "   GET /config  -> $$config"; \
			echo "   Unexpected /config payload. This does not look like crossexam-api."; \
			echo "   Stop the conflicting service, run 'make api', or pass API_PORT=..."; \
			exit 1; \
		fi; \
		echo "   GET /healthz -> $$health"; \
		echo "   GET /config  -> $$config"; \
	else \
		echo "   API not reachable at $(API_BASE)."; \
		echo "   Start it with 'make api' (or 'make dev-live' / 'docker compose up'),"; \
		echo "   then re-run 'make verify-live'. See KEYS.md for the key list."; \
	fi

# =============================================================================
# eval / bench  (offline quality + latency gates — no API keys needed)
# =============================================================================
## eval: run the retrieval + faithfulness SCORECARD (mock index). HARD gate:
## exits nonzero if MRR / P@1 / faithfulness fall below threshold.
eval:
	$(call need_py)
	@echo ">> crossexam retrieval + faithfulness scorecard (mock mode, no keys)"
	cd "$(BACKEND)" && $(PY) -m crossexam_backend.eval.run_eval

## bench: micro-benchmark index.query latency (p50/p95/p99). HARD gate:
## asserts retrieval p99 < 10ms on the mock index (the headline Moss claim).
bench:
	$(call need_py)
	@echo ">> crossexam retrieval latency benchmark (mock mode, sub-10ms p99 gate)"
	cd "$(BACKEND)" && $(PY) -m crossexam_backend.eval.bench

# =============================================================================
# build / index
# =============================================================================
## build: docker build backend + frontend images
build:
	@if ! command -v docker >/dev/null 2>&1; then \
		echo ">> docker not found. Install Docker Desktop from https://docker.com and re-run."; \
		exit 1; \
	fi
	docker build -t crossexam-backend:local  "$(BACKEND)"
	docker build -t crossexam-frontend:local "$(FRONTEND)"

## fixture: regenerate the REAL demo fixture (sample PDF -> text parse -> backend/fixtures)
## This is the canonical regen command. `make index` is a separate dry-run smoke test that
## writes only to pipeline/build/ and must NOT be pointed at backend/fixtures/sample_chunks.json.
fixture:
	$(call need_py)
	@echo ">> generating sample-deposition.pdf + regenerating backend/fixtures/sample_chunks.json"
	cd "$(PIPELINE)" && $(PY) -m crossexam_pipeline.make_sample_pdf

## index: offline pipeline smoke test in --dry-run mode (no keys); writes ONLY to pipeline/build/.
## Does NOT regenerate the committed demo fixture — use `make fixture` for that.
index:
	$(call need_py)
	@echo ">> parse (deterministic fallback) -> chunks -> build-index (dry-run)"
	cd "$(PIPELINE)" && $(PY) -m crossexam_pipeline.cli parse \
		--dry-run \
		--out "$(INDEX_CHUNKS)"
	cd "$(PIPELINE)" && $(PY) -m crossexam_pipeline.cli build-index \
		--input "$(INDEX_CHUNKS)" \
		--index-name crossexam-documents \
		--out "$(INDEX_OUT)" \
		--dry-run

## clean: remove caches and build artifacts
clean:
	rm -rf "$(FRONTEND)/dist" "$(FRONTEND)/.vite" "$(PIPELINE)/build"
	find "$(BACKEND)" "$(PIPELINE)" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	find "$(BACKEND)" "$(PIPELINE)" -type d -name .pytest_cache -prune -exec rm -rf {} + 2>/dev/null || true
	find "$(BACKEND)" "$(PIPELINE)" -type d -name .ruff_cache -prune -exec rm -rf {} + 2>/dev/null || true
