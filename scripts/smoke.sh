#!/usr/bin/env bash
# muninn-py SDK smoke test.
# Validates: server reachability -> CLI commands -> synthetic trade -> API docs.
#
# Usage: ./scripts/smoke.sh
#        MUNINN_HOST=http://my-server:8080 ./scripts/smoke.sh
#
# If the Muninn server is not running and the sibling muninn repo exists,
# the script will boot infrastructure + app via Docker and tear it down on exit.

set -euo pipefail

MUNINN_HOST="${MUNINN_HOST:-http://localhost:8080}"
MUNINN_REPO="${MUNINN_REPO:-$(cd "$(dirname "$0")/../.." && pwd)/muninn}"
TEARDOWN=false
TIMEOUT=60
PASSED=0
FAILED=0

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'

pass() { echo -e "${GREEN}  ✓ $1${NC}"; PASSED=$((PASSED + 1)); }
fail() { echo -e "${RED}  ✗ $1${NC}"; FAILED=$((FAILED + 1)); }
info() { echo -e "${YELLOW}  → $1${NC}"; }
step() { echo -e "\n${CYAN}[$1] $2${NC}"; }

cleanup() {
  if [ "$TEARDOWN" = true ]; then
    info "Tearing down Docker resources..."
    docker compose -f "${MUNINN_REPO}/docker-compose.yml" down -v --remove-orphans 2>/dev/null || true
  fi
}
trap cleanup EXIT

# --- Step 1: Server reachability -------------------------------------------

step 1 "Checking Muninn server at ${MUNINN_HOST}"

HEALTH_OK=false
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${MUNINN_HOST}/actuator/health" 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
  HEALTH_OK=true
  pass "Server already running"
fi

if [ "$HEALTH_OK" = false ]; then
  if [ -d "$MUNINN_REPO" ] && [ -f "${MUNINN_REPO}/docker-compose.yml" ]; then
    info "Server not reachable — booting from ${MUNINN_REPO}"
    TEARDOWN=true

    # Start infrastructure (postgres, redpanda, minio)
    docker compose -f "${MUNINN_REPO}/docker-compose.yml" up -d

    # Build and start the app container on the compose network
    if [ -f "${MUNINN_REPO}/Dockerfile" ]; then
      docker compose -f "${MUNINN_REPO}/docker-compose.yml" up -d --build muninn 2>/dev/null || true
    fi

    # If no app container, try running via Maven wrapper
    if ! curl -sf "${MUNINN_HOST}/actuator/health" >/dev/null 2>&1; then
      if [ -x "${MUNINN_REPO}/mvnw" ]; then
        info "Starting app via mvnw spring-boot:run (background)..."
        (cd "$MUNINN_REPO" && ./mvnw -q spring-boot:run &) 2>/dev/null
      fi
    fi

    # Wait for health
    info "Waiting up to ${TIMEOUT}s for server health..."
    for i in $(seq 1 "$TIMEOUT"); do
      HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${MUNINN_HOST}/actuator/health" 2>/dev/null || echo "000")
      if [ "$HTTP_CODE" = "200" ]; then
        HEALTH_OK=true
        break
      fi
      sleep 1
    done

    if [ "$HEALTH_OK" = true ]; then
      pass "Server became healthy after ${i}s"
    else
      fail "Server did not become healthy within ${TIMEOUT}s"
      echo -e "${RED}Cannot continue without a running server.${NC}"
      exit 1
    fi
  else
    echo -e "${RED}Server is not reachable at ${MUNINN_HOST} and sibling repo not found at ${MUNINN_REPO}.${NC}"
    echo ""
    echo "Options:"
    echo "  1. Start the Muninn server manually and re-run this script"
    echo "  2. Set MUNINN_HOST to point at a running instance"
    echo "  3. Clone the muninn repo alongside muninn-py:"
    echo "       git clone <muninn-url> ${MUNINN_REPO}"
    exit 1
  fi
fi

# --- Step 2: CLI smoke checks ---------------------------------------------

step 2 "Running CLI commands"

if muninn features list --host "$MUNINN_HOST" >/dev/null 2>&1; then
  pass "muninn features list"
else
  fail "muninn features list (exit code $?)"
fi

if muninn replay list --host "$MUNINN_HOST" >/dev/null 2>&1; then
  pass "muninn replay list"
else
  fail "muninn replay list (exit code $?)"
fi

# --- Step 3: Synthetic trade event -----------------------------------------

step 3 "Posting synthetic trade event"

EVENT_ID=$(python3 -c "import uuid; print(uuid.uuid4())" 2>/dev/null || echo "00000000-0000-7000-8000-000000000001")
NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

TRADE_JSON=$(cat <<EOF
{
  "eventId": "${EVENT_ID}",
  "eventTime": "${NOW}",
  "ingestTime": "${NOW}",
  "source": "smoke-test",
  "instrument": {
    "symbol": "BTC-USDT",
    "baseAsset": "BTC",
    "quoteAsset": "USDT",
    "exchange": {
      "id": "binance",
      "displayName": "Binance Spot",
      "timezone": "UTC"
    }
  },
  "sequenceNumber": 1,
  "schemaVersion": 1,
  "price": 67500.50,
  "size": 0.01,
  "side": "BUY",
  "exchangeTradeId": "smoke-test-001"
}
EOF
)

RESPONSE=$(curl -s -X POST "${MUNINN_HOST}/api/v1/events/trade" \
  -H "Content-Type: application/json" \
  -d "${TRADE_JSON}" \
  -w "\n%{http_code}")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)

if [ "$HTTP_CODE" = "201" ] || [ "$HTTP_CODE" = "200" ]; then
  pass "Trade event accepted (HTTP ${HTTP_CODE})"
else
  fail "Trade event rejected (HTTP ${HTTP_CODE})"
fi

# --- Step 4: API docs endpoint ---------------------------------------------

step 4 "Checking API docs"

API_DOCS_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${MUNINN_HOST}/api-docs" 2>/dev/null || echo "000")
if [ "$API_DOCS_CODE" = "200" ]; then
  pass "OpenAPI spec available at /api-docs"
else
  fail "OpenAPI spec returned HTTP ${API_DOCS_CODE}"
fi

# --- Summary ---------------------------------------------------------------

echo ""
echo -e "${CYAN}═══════════════════════════════════════${NC}"
if [ "$FAILED" -eq 0 ]; then
  echo -e "${GREEN}  Smoke test passed  (${PASSED} checks)${NC}"
else
  echo -e "${RED}  Smoke test done  (${PASSED} passed, ${FAILED} failed)${NC}"
fi
echo -e "${CYAN}═══════════════════════════════════════${NC}"
echo ""

exit "$FAILED"
