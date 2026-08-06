#!/bin/bash
set -e

# cd to project root (parent of scripts/)
cd "$(dirname "$0")/.."

# Load .env — keep set -a on so all vars are exported to child processes
set -a
if [ -f .env ]; then
  # shellcheck disable=SC1091
  source .env
  echo "Loaded .env"
else
  echo "WARNING: No .env file found"
fi

# Kill anything on our ports first
lsof -ti:8100 2>/dev/null | xargs kill -9 2>/dev/null || true
lsof -ti:5173 2>/dev/null | xargs kill -9 2>/dev/null || true
sleep 1

echo "=== Starting Mira ==="

# Load private key from file if path is set (App mode)
if [ -n "$MIRA_GITHUB_PRIVATE_KEY_PATH" ]; then
  KEY_PATH="$MIRA_GITHUB_PRIVATE_KEY_PATH"
  # Try with .pem extension if file not found
  [ ! -f "$KEY_PATH" ] && [ -f "${KEY_PATH}.pem" ] && KEY_PATH="${KEY_PATH}.pem"
  if [ -f "$KEY_PATH" ]; then
    export MIRA_GITHUB_PRIVATE_KEY
    MIRA_GITHUB_PRIVATE_KEY=$(cat "$KEY_PATH")
    echo "Loaded GitHub private key from $KEY_PATH"
  else
    echo "WARNING: Private key file not found: $MIRA_GITHUB_PRIVATE_KEY_PATH"
  fi
fi

# Ensure index directory exists
export MIRA_INDEX_DIR="${MIRA_INDEX_DIR:-./data/indexes}"
mkdir -p "$MIRA_INDEX_DIR"

# Set defaults only if not already set by .env
export ADMIN_PASSWORD="${ADMIN_PASSWORD:-admin}"
export MIRA_MODEL="${MIRA_MODEL:-anthropic/claude-sonnet-4-6}"

# Resolve PAT alias for logging
_PAT="${MIRA_GITHUB_TOKEN:-${GITHUB_TOKEN:-}}"

# Debug
echo "  MIRA_GITHUB_APP_ID=${MIRA_GITHUB_APP_ID:-(not set)}"
if [ -n "$_PAT" ]; then
  echo "  MIRA_GITHUB_TOKEN=(set)"
else
  echo "  MIRA_GITHUB_TOKEN=(not set)"
fi
echo "  DATABASE_URL=${DATABASE_URL:-(not set)}"
echo "  MIRA_INDEX_DIR=${MIRA_INDEX_DIR}"

# Prefer the project venv, then uv, then PATH
if [ -x .venv/bin/mira ]; then
  MIRA_BIN=".venv/bin/mira"
elif command -v uv >/dev/null 2>&1; then
  MIRA_BIN="uv run mira"
else
  MIRA_BIN="mira"
fi

# Optional deployment config
CONFIG_ARGS=()
if [ -f mira.yaml ]; then
  CONFIG_ARGS=(--config mira.yaml)
  echo "  Config: mira.yaml"
fi

# Start single server (dashboard API + webhooks on same port).
# mira serve supports App mode OR PAT mode (MIRA_GITHUB_TOKEN + MIRA_WEBHOOK_SECRET).
echo "Starting Mira server on port 8100..."
# shellcheck disable=SC2086
$MIRA_BIN serve --host 0.0.0.0 --port 8100 "${CONFIG_ARGS[@]}" &
SERVER_PID=$!

sleep 2

# Start frontend if present (path is ui/mira in current tree)
UI_PID=""
if [ -d ui/mira ]; then
  echo "Starting frontend on port 5173..."
  (
    cd ui/mira
    VITE_API_URL=http://localhost:8100 npm run dev
  ) &
  UI_PID=$!
elif [ -d UI/mira ]; then
  echo "Starting frontend on port 5173..."
  (
    cd UI/mira
    VITE_API_URL=http://localhost:8100 npm run dev
  ) &
  UI_PID=$!
else
  echo "No ui/mira tree — dashboard served by backend if ui_dist is built."
fi

sleep 2
echo ""
echo "=== Mira is running ==="
echo "  Dashboard:  http://localhost:5173 (or :8100 if SPA is bundled)"
echo "  API:        http://localhost:8100"
if [ -n "$MIRA_GITHUB_APP_ID" ] || [ -n "$_PAT" ]; then
  echo "  Webhook:    http://localhost:8100/github/webhook"
  echo "  Point ngrok at: http://localhost:8100"
  if [ -n "$_PAT" ] && [ -z "$MIRA_GITHUB_APP_ID" ]; then
    echo "  Mode: PAT — set repo/org webhook to https://<ngrok-url>/github/webhook"
  else
    echo "  Mode: App — set GitHub App webhook URL to https://<ngrok-url>/github/webhook"
  fi
else
  echo "  GitHub: not configured (set MIRA_GITHUB_TOKEN+MIRA_WEBHOOK_SECRET or App creds)"
fi
echo ""
echo "  Login with: admin / ${ADMIN_PASSWORD}"
echo ""
echo "Press Ctrl+C to stop"

if [ -n "$UI_PID" ]; then
  trap "kill $SERVER_PID $UI_PID 2>/dev/null" EXIT
else
  trap "kill $SERVER_PID 2>/dev/null" EXIT
fi
wait
