#!/usr/bin/env bash
# =============================================================================
# run.sh — start the full CrossExam stack with ONE command, with rich logging.
#
#   cd crossexam && ./run.sh                 # full stack (api + worker + frontend)
#   cd crossexam && ./run.sh --no-worker     # UI + API only (skip voice worker)
#   LOG_LEVEL=DEBUG ./run.sh                 # verbose backend logs
#
# Starts three processes against the project venv (./.venv) and ./.env:
#   - api      : FastAPI HTTP service on $API_PORT (token mint + upload + /config)
#   - worker   : LiveKit voice agent (STT -> LLM -> Moss retrieval -> TTS)
#   - frontend : Vite dev server on :5173 (the React UI)
#
# LOGGING (all of it):
#   - every service's stdout+stderr is captured, timestamped, and tagged with a
#     colored [service] prefix, streamed live to THIS terminal;
#   - a per-service file:   logs/api.log  logs/worker.log  logs/frontend.log
#   - a combined file:      logs/crossexam.log  (everything, interleaved, tagged)
#   - logs are truncated at the start of each run for a clean session;
#   - LOG_LEVEL (from .env or the environment) is honored by the backend.
#
# Ctrl-C stops everything (and its children).
# =============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PY="$ROOT/.venv/bin/python"
NO_WORKER=0
[ "${1:-}" = "--no-worker" ] && NO_WORKER=1

# ---- prerequisites ----------------------------------------------------------
if [ ! -x "$PY" ]; then
  echo "!! No venv at $ROOT/.venv"
  echo "   Create it once:"
  echo "     python3 -m venv .venv && .venv/bin/pip install -e 'backend[api,voice,moss]' -e 'pipeline[pdf,moss]'"
  exit 1
fi
if [ -f .env ]; then
  set -a; . ./.env; set +a
else
  echo "!! No .env — running in mock mode (copy .env.example -> .env to go live)."
fi
API_PORT="${API_PORT:-8011}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"
# Local full-stack runs should not fight over LiveKit's production health port
# (:8081). `0` asks LiveKit/aiohttp to bind any free port; production can export
# WORKER_HTTP_PORT=8081 or another pinned health-check port.
export WORKER_HTTP_PORT="${WORKER_HTTP_PORT:-0}"
# Keep one warm LiveKit process for local demos. Production can raise this when
# the retrieval index is healthy and memory capacity is sized for it.
export WORKER_IDLE_PROCESSES="${WORKER_IDLE_PROCESSES:-1}"
export WORKER_MEMORY_WARN_MB="${WORKER_MEMORY_WARN_MB:-1024}"
export WORKER_MEMORY_LIMIT_MB="${WORKER_MEMORY_LIMIT_MB:-0}"
WORKER_READY_TIMEOUT_S="${WORKER_READY_TIMEOUT_S:-120}"
# Keep uploads and Moss-degraded fallback data out of the committed demo
# fixture. Seed a runtime fixture once from the canonical test/demo fixture and
# let API + worker share that mutable copy.
RUNTIME_FIXTURE_PATH="${RUNTIME_FIXTURE_PATH:-runtime/sample_chunks.json}"
if [ "${USE_RUNTIME_FIXTURE:-true}" != "false" ]; then
  mkdir -p "$(dirname "$RUNTIME_FIXTURE_PATH")"
  if [ ! -f "$RUNTIME_FIXTURE_PATH" ]; then
    cp backend/fixtures/sample_chunks.json "$RUNTIME_FIXTURE_PATH"
  fi
  export MOCK_FIXTURE_PATH="$RUNTIME_FIXTURE_PATH"
fi
mkdir -p logs

# ---- logging setup ----------------------------------------------------------
COMBINED="logs/crossexam.log"
# Colors only when stdout is a real terminal.
if [ -t 1 ]; then
  C_API=$'\033[32m'; C_WRK=$'\033[35m'; C_FE=$'\033[36m'; C_SYS=$'\033[33m'; C_RST=$'\033[0m'
else
  C_API=''; C_WRK=''; C_FE=''; C_SYS=''; C_RST=''
fi
: > "$COMBINED"
say() { printf '%s%s [run     ] %s%s\n' "$C_SYS" "$(date '+%H:%M:%S')" "$1" "$C_RST"; }

# launch <name> <color> <logfile> <cmd...>
# captures stdout+stderr, prefixes each line with `HH:MM:SS [name]`, and fans it
# out to: the per-service file, the combined file, and this terminal (colored).
launch() {
  local name="$1" color="$2" file="$3"; shift 3
  : > "$file"
  (
    "$@" 2>&1 | while IFS= read -r line; do
      ts="$(date '+%H:%M:%S')"
      printf '%s [%-8s] %s\n' "$ts" "$name" "$line" >> "$file"
      printf '%s [%-8s] %s\n' "$ts" "$name" "$line" >> "$COMBINED"
      printf '%s%s [%-8s]%s %s\n' "$color" "$ts" "$name" "$C_RST" "$line"
    done
  ) &
  PIDS+=($!)
  LAST_PID=$!
}

# ---- stop anything already running (idempotent) -----------------------------
say "stopping any existing CrossExam processes..."
pkill -f "crossexam_backend.api" 2>/dev/null || true
pkill -f "crossexam_backend.server" 2>/dev/null || true   # broader: catches "start" + children
pkill -f "node .*vite" 2>/dev/null || true
# Free the ports we need, regardless of how the holder was started (e.g. an
# orphaned LiveKit worker still bound to its :8081 health server). pkill by
# name races; killing the port holder directly is deterministic.
for port in "$API_PORT" 8081; do
  lsof -ti tcp:"$port" 2>/dev/null | xargs kill -9 2>/dev/null || true
done
sleep 2

PIDS=()
cleanup() {
  say "shutting down..."
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
  pkill -f "crossexam_backend.api" 2>/dev/null || true
  pkill -f "crossexam_backend.server start" 2>/dev/null || true
  pkill -f "node .*vite" 2>/dev/null || true
  exit 0
}
trap cleanup INT TERM

# ---- start services ---------------------------------------------------------
say "LOG_LEVEL=$LOG_LEVEL  API_PORT=$API_PORT  WORKER_HTTP_PORT=$WORKER_HTTP_PORT  WORKER_IDLE_PROCESSES=$WORKER_IDLE_PROCESSES  WORKER_MEMORY_WARN_MB=$WORKER_MEMORY_WARN_MB  MOCK_FIXTURE_PATH=${MOCK_FIXTURE_PATH:-}  (logs in $ROOT/logs/)"
say "starting API..."
launch api "$C_API" logs/api.log "$PY" -m crossexam_backend.api

if [ "$NO_WORKER" -eq 0 ]; then
  say "starting LiveKit voice worker..."
  launch worker "$C_WRK" logs/worker.log "$PY" -m crossexam_backend.server start
  WORKER_LAUNCH_PID="$LAST_PID"
else
  say "--no-worker: skipping the voice worker"
fi

# ---- readiness banner -------------------------------------------------------
say "waiting for the API..."
API_READY_HEALTH=""
for _ in $(seq 1 30); do
  API_READY_HEALTH="$(curl -s -m2 "http://localhost:${API_PORT}/healthz" 2>/dev/null || true)"
  [ -n "$API_READY_HEALTH" ] && break
  sleep 1
done
if [ "$NO_WORKER" -eq 0 ] && ! kill -0 "${WORKER_LAUNCH_PID:-0}" 2>/dev/null; then
  say "LiveKit worker exited during startup. Last worker log lines:"
  tail -n 60 logs/worker.log 2>/dev/null || true
  say "shutting down..."
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
  pkill -f "crossexam_backend.api" 2>/dev/null || true
  pkill -f "node .*vite" 2>/dev/null || true
  exit 1
fi
if [ "$NO_WORKER" -eq 0 ]; then
  say "waiting for the LiveKit worker to register..."
  worker_ready=0
  for _ in $(seq 1 "$WORKER_READY_TIMEOUT_S"); do
    if grep -q "registered worker" logs/worker.log 2>/dev/null; then
      worker_ready=1
      break
    fi
    if ! kill -0 "${WORKER_LAUNCH_PID:-0}" 2>/dev/null; then
      say "LiveKit worker exited before registration. Last worker log lines:"
      tail -n 60 logs/worker.log 2>/dev/null || true
      say "shutting down..."
      for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
      pkill -f "crossexam_backend.api" 2>/dev/null || true
      pkill -f "node .*vite" 2>/dev/null || true
      exit 1
    fi
    sleep 1
  done
  if [ "$worker_ready" -ne 1 ]; then
    say "LiveKit worker did not register within ${WORKER_READY_TIMEOUT_S}s. Last worker log lines:"
    tail -n 60 logs/worker.log 2>/dev/null || true
    say "shutting down..."
    for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
    pkill -f "crossexam_backend.api" 2>/dev/null || true
    pkill -f "node .*vite" 2>/dev/null || true
    exit 1
  fi
fi
if [ -d frontend/node_modules ]; then
  say "starting frontend (Vite)..."
  launch frontend "$C_FE" logs/frontend.log bash -c 'cd frontend && exec npm run dev'
else
  say "frontend/node_modules missing — run 'npm install' in frontend/; skipping UI"
fi
HEALTH="$(curl -s -m10 "http://localhost:${API_PORT}/healthz" 2>/dev/null || true)"
HEALTH="${HEALTH:-${API_READY_HEALTH:-'{unreachable}'}}"
say "============================================================"
say "  CrossExam is up:"
say "    UI    : http://localhost:5173"
say "    API   : http://localhost:${API_PORT}  $HEALTH"
say "    logs  : per-service logs/{api,worker,frontend}.log + combined logs/crossexam.log"
say "  Worker is registered. Open the UI, allow the mic, then speak."
say "  Ctrl-C stops everything."
say "============================================================"

# Stream continues live above (each service is tee'd to the terminal). Hold here
# until Ctrl-C; the trap stops all children.
wait
