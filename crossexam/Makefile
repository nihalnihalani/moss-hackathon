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

# Prefer python3, fall back to python.
PY := $(shell command -v python3 2>/dev/null || command -v python 2>/dev/null)
NPM := $(shell command -v npm 2>/dev/null)

# Where `make index` writes its intermediate chunk JSON and final index records.
INDEX_CHUNKS := $(PIPELINE)/build/chunks.json
INDEX_OUT    := $(PIPELINE)/build/index_records.json

.DEFAULT_GOAL := help

.PHONY: help setup setup-backend setup-pipeline setup-frontend \
        dev test test-backend test-pipeline test-frontend \
        lint lint-py lint-frontend fmt typecheck typecheck-py typecheck-frontend \
        build index clean

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
# dev
# =============================================================================
## dev: run backend worker + frontend dev server together (Ctrl-C stops both)
dev:
	$(call need_py)
	$(call need_npm)
	@echo ">> starting backend worker + frontend dev server (mock mode unless .env set)"
	@trap 'kill 0' EXIT INT TERM; \
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
	@echo ">> ruff (advisory: newer ruff may flag version-drift style on unchanged source)"
	-cd "$(BACKEND)"  && $(PY) -m ruff check crossexam_backend
	-cd "$(PIPELINE)" && $(PY) -m ruff check crossexam_pipeline

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
	@echo ">> mypy (advisory: newer livekit-agents/mypy may flag version-drift on unchanged source)"
	-cd "$(BACKEND)"  && $(PY) -m mypy crossexam_backend
	-cd "$(PIPELINE)" && $(PY) -m mypy crossexam_pipeline

typecheck-frontend:
	$(call need_npm)
	cd "$(FRONTEND)" && $(NPM) run typecheck

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
