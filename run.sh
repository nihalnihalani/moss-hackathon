#!/usr/bin/env bash
# =============================================================================
# run.sh — start the full CrossExam stack with one command.
#
#   ./run.sh
#
# Starts three processes and streams their logs (colored + prefixed) to this
# terminal AND to ./logs/<service>.log:
#   - api      : FastAPI HTTP service (token mint + PDF upload + /healthz /config)
#   - worker   : LiveKit voice agent worker (STT -> LLM -> Moss -> TTS)
#   - frontend : Vite dev server (the React UI)
#
# Mock vs live is decided by crossexam/.env. With keys present it runs live;
# with none it runs in mock mode. Either way the UI comes up.
#
# Ctrl-C stops all three. Pass --no-worker to skip the voice worker (UI + API
# only), or DEBUG=0 ./run.sh to quiet the extra verbosity.
# =============================================================================
set -uo pipefail

# ---- paths (resolved from this script's location, so cwd doesn't matter) -----
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$ROOT/crossexam"
BACKEND="$APP/backend"
FRONTEND="$APP/frontend"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

# ---- config (override via env, e.g. API_PORT=9000 ./run.sh) ------------------
API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8000}"
FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"
DEBUG="${DEBUG:-1}"
RUN_WORKER=1
[ "${1:-}" = "--no-worker" ] && RUN_WORKER=0

# ---- colors (disabled when not a tty) ---------------------------------------
if [ -t 1 ]; then
  C_API=$'\033[36m'; C_WRK=$'\033[35m'; C_FE=$'\033[32m'
  C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_ERR=$'\033[31m'; C_DIM=$'\033[2m'; C_RST=$'\033[0m'
else
  C_API=""; C_WRK=""; C_FE=""; C_OK=""; C_WARN=""; C_ERR=""; C_DIM=""; C_RST=""
fi
say()  { printf '%s>>%s %s\n' "$C_DIM" "$C_RST" "$*"; }
ok()   { printf '%s✓%s  %s\n' "$C_OK" "$C_RST" "$*"; }
warn() { printf '%s!%s  %s\n' "$C_WARN" "$C_RST" "$*"; }
die()  { printf '%s✗  %s%s\n' "$C_ERR" "$*" "$C_RST" >&2; exit 1; }

# ---- pick python: prefer the project venv, fall back to system --------------
if [ -x "$APP/.venv/bin/python" ]; then
  PY="$APP/.venv/bin/python"
else
  PY="$(command -v python3 || command -v python || true)"
fi
[ -n "$PY" ] || die "python not found. Install Python 3.10+ (or create crossexam/.venv) and re-run."
NPM="$(command -v npm || true)"
[ -n "$NPM" ] || die "npm not found. Install Node.js 20+ from https://nodejs.org and re-run."

# =============================================================================
# Pre-flight checks (fail early with a clear message, not a mid-run crash)
# =============================================================================
say "CrossExam launcher"
say "repo:     $ROOT"
say "python:   $PY ($("$PY" --version 2>&1))"
say "npm:      $NPM ($($NPM --version 2>&1))"
say "logs:     $LOG_DIR/{api,worker,frontend}.log"
echo

# .env present? Export it into the environment using the SAME parser pydantic
# uses (python-dotenv), so values match exactly. This matters because the
# LiveKit worker framework reads LIVEKIT_URL / API_KEY / SECRET straight from
# os.environ — pydantic parsing the .env into Settings is not enough, the vars
# must actually be exported or the worker dies with "ws_url is required".
ENV_LOADED=0
if [ -f "$APP/.env" ]; then
  # Parse .env with the SAME parser pydantic uses (python-dotenv) and capture the
  # KEY=VALUE pairs, then export them in THIS shell via a here-string. Using
  # command-substitution + here-string (not process substitution) on purpose: a
  # heredoc nested inside `< <(...)` breaks macOS's bash 3.2 parser, and a piped
  # `... | while` would export into a subshell and lose the vars.
  # python -c one-liner (NOT a heredoc): macOS ships bash 3.2, which mis-parses
  # heredocs nested inside $()/<(). The one-liner sidesteps that entirely.
  _env_kv="$("$PY" -c 'import sys
try:
    from dotenv import dotenv_values
except Exception:
    sys.exit(0)
[print(f"{k}={v}") for k, v in dotenv_values(sys.argv[1]).items() if v is not None and "\n" not in v]' "$APP/.env" 2>/dev/null)"
  if [ -n "$_env_kv" ]; then
    while IFS= read -r kv; do [ -n "$kv" ] && export "$kv"; done <<< "$_env_kv"
  fi
  ENV_LOADED=1
  if [ -n "${LIVEKIT_URL:-}" ] && [ -n "${MOSS_PROJECT_KEY:-}" ]; then
    ok ".env loaded (LiveKit + Moss keys present) — LIVE mode"
  else
    warn ".env loaded but some keys are blank — parts may run in mock"
  fi
else
  warn "no crossexam/.env — running in MOCK mode (UI works, no real voice/retrieval)"
fi

# Decide which extras to install. With Moss creds present we add the [moss] extra
# so the LIVE retrieval path actually has the in-process SDK (moss); with
# no creds we stay on [api,voice] and the app falls back to the mock index.
BACKEND_EXTRAS="api,voice"
MOSS_READY=0
if [ -n "${MOSS_PROJECT_ID:-}" ] && [ -n "${MOSS_PROJECT_KEY:-}" ]; then
  BACKEND_EXTRAS="api,voice,moss"
  MOSS_READY=1
fi

# backend importable?
if "$PY" -c "import crossexam_backend.api, crossexam_backend.server" >/dev/null 2>&1; then
  ok "backend package imports"
else
  warn "backend not importable — attempting: pip install -e backend[$BACKEND_EXTRAS]"
  "$PY" -m pip install -e "$BACKEND[$BACKEND_EXTRAS]" || die "backend install failed. See KEYS.md / 'make setup'."
fi

# Moss creds present but the SDK isn't installed? Pull in the [moss] extra so the
# live retrieval path is real (not silently mock). Skipped entirely in mock mode.
if [ "$MOSS_READY" = "1" ]; then
  if "$PY" -c "import moss" >/dev/null 2>&1; then
    ok "Moss SDK present — LIVE retrieval path available"
  else
    warn "Moss creds set but moss SDK missing — installing backend[moss]"
    "$PY" -m pip install -e "$BACKEND[moss]" \
      && ok "Moss SDK installed" \
      || warn "Moss SDK install failed — backend will fall back to the mock index"
  fi
fi

# LiveKit voice models: the turn-detector multilingual model + Silero VAD weights
# are fetched separately from the pip install. Pre-download them (idempotent —
# download-files skips already-cached files) so the worker's first session starts
# without a cold-download stall. Tolerant: if the turn-detector plugin isn't
# installed (mock-only) we skip; if the download fails the worker degrades to
# VAD turn detection (see crossexam_backend/server.py _build_turn_detection).
if "$PY" -c "import livekit.plugins.turn_detector" >/dev/null 2>&1; then
  "$PY" -m livekit.agents download-files >/dev/null 2>&1 \
    && ok "LiveKit voice models present" \
    || warn "LiveKit model download skipped/failed — worker falls back to VAD turn detection"
fi

# frontend deps?
if [ -d "$FRONTEND/node_modules" ]; then
  ok "frontend deps present"
else
  warn "frontend node_modules missing — running npm install (one-time)"
  ( cd "$FRONTEND" && "$NPM" install ) || die "npm install failed."
fi

# API port free?
if lsof -nP -iTCP:"$API_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  die "port $API_PORT already in use. Stop the other process or run: API_PORT=8001 ./run.sh"
fi
echo

# =============================================================================
# Log streaming: prefix + color to terminal, plain + timestamp to logfile
# =============================================================================
stream() {
  local label="$1" color="$2" logfile="$LOG_DIR/$1.log"
  : > "$logfile"
  while IFS= read -r line; do
    printf '%s[%s]%s %s\n' "$color" "$label" "$C_RST" "$line"
    printf '%s [%s] %s\n' "$(date '+%H:%M:%S')" "$label" "$line" >> "$logfile"
  done
}

# Verbose env for the python services (best-effort; unknown vars are harmless).
DEBUG_ENV=(PYTHONUNBUFFERED=1)
if [ "$DEBUG" = "1" ]; then
  DEBUG_ENV+=(LOG_LEVEL=DEBUG CROSSEXAM_LOG_LEVEL=DEBUG UVICORN_LOG_LEVEL=debug)
  say "debug logging ON (set DEBUG=0 ./run.sh to quiet it)"
fi

# Clean shutdown. `kill 0` (process group) handles the normal foreground Ctrl-C
# path, but to be robust however the script is stopped we also walk and kill each
# launched service's full process tree (subshell -> python/node children).
PIDS=()
kill_tree() {
  local pid=$1 child
  for child in $(pgrep -P "$pid" 2>/dev/null); do kill_tree "$child"; done
  kill "$pid" 2>/dev/null
}
cleanup() {
  trap '' EXIT INT TERM   # don't re-enter while we tear down
  echo; say "shutting down…"
  local pid
  for pid in "${PIDS[@]}"; do kill_tree "$pid"; done
  kill 0 2>/dev/null
}
trap cleanup EXIT INT TERM

# =============================================================================
# Launch
# =============================================================================
say "starting services… (Ctrl-C stops everything)"
echo

# 1) HTTP API. Run from the app root (not backend/) so pydantic's env_file=".env"
# resolves to crossexam/.env — otherwise the API silently falls back to mock.
( cd "$APP" && env "${DEBUG_ENV[@]}" API_HOST="$API_HOST" API_PORT="$API_PORT" \
    "$PY" -m crossexam_backend.api 2>&1 | stream api "$C_API" ) &
PIDS+=($!)

# 2) LiveKit voice worker (cwd = app root, matches the Makefile)
if [ "$RUN_WORKER" = "1" ]; then
  ( cd "$APP" && env "${DEBUG_ENV[@]}" \
      "$PY" -m crossexam_backend.server dev 2>&1 | stream worker "$C_WRK" ) &
  PIDS+=($!)
else
  warn "voice worker skipped (--no-worker)"
fi

# 3) Frontend dev server (cwd = frontend)
( cd "$FRONTEND" && "$NPM" run dev -- --host "$FRONTEND_HOST" 2>&1 | stream frontend "$C_FE" ) &
PIDS+=($!)

# ---- health probe: tell the user live vs mock once the API answers ----------
(
  for _ in $(seq 1 40); do
    if curl -fsS --max-time 1 "http://$API_HOST:$API_PORT/healthz" >/dev/null 2>&1; then
      echo
      ok "API up at http://$API_HOST:$API_PORT"
      cfg="$(curl -fsS --max-time 2 "http://$API_HOST:$API_PORT/config" 2>/dev/null || true)"
      [ -n "$cfg" ] && say "config: $cfg"
      ok "UI at  http://$FRONTEND_HOST:5173  (Vite may pick the next free port — see [frontend] log)"
      echo
      break
    fi
    sleep 0.5
  done
) &

# Wait on all background jobs; Ctrl-C triggers the trap above.
wait
