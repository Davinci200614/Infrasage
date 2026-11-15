#!/usr/bin/env bash
# test_twin.sh — Full digital twin test sequence (Phases 1-3)
# Usage: ./test_twin.sh
# Press Ctrl+C at any time to stop and clean up.

set -euo pipefail

PROJECT="infrasage-lab"
COMPOSE_FILES=("-f" "lab/compose.twin.yaml" "-f" "lab/docker-compose.infrasage.yml")
VENV_PY=".venv/bin/python"

# ── trap: clean up on exit (normal or interrupt) ──────────────────────────
cleanup() {
    echo
    echo "=== Cleaning up lab stack ==="
    sg docker -c "docker compose -p $PROJECT ${COMPOSE_FILES[*]} down -v" 2>/dev/null || true
    echo "=== Done ==="
}
trap cleanup EXIT INT TERM

# ── helpers ────────────────────────────────────────────────────────────────
step() { echo; echo "▶ $*"; }
ok()   { echo "  ✓ $*"; }
die()  { echo "  ✗ $*" >&2; exit 1; }

require() {
    command -v "$1" >/dev/null 2>&1 || die "Missing: $1 (install it first)"
}

# ── prerequisites ──────────────────────────────────────────────────────────
step "Checking prerequisites"
require docker
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 not available (need 'docker compose')"
require curl
require jq
require sg
[[ -x "$VENV_PY" ]] || die "Venv not found at $VENV_PY (run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt)"
ok "All tools present"

# ── 1. Unit tests (fake Docker) ────────────────────────────────────────────
step "Running unit tests (backend/twin/tests)"
$VENV_PY -m pytest backend/twin/tests -q
ok "Twin unit tests passed"

# ── 2. Start lab stack ─────────────────────────────────────────────────────
step "Starting lab stack ($PROJECT)"
sg docker -c "docker compose -p $PROJECT ${COMPOSE_FILES[*]} up -d --wait"
ok "Stack up"

step "Verifying service health"
sg docker -c "docker exec infrasage-lab-redis-1 redis-cli ping" | grep -q PONG || die "Redis not responding"
sg docker -c "docker exec infrasage-lab-db-1 pg_isready -U postgres" | grep -q "accepting connections" || die "Postgres not ready"
ok "Redis + Postgres healthy"

# ── 3. Baseline vote end-to-end ────────────────────────────────────────────
step "Baseline vote (vote → redis → worker → db → result)"
VOTER="baseline-$(date +%s)"
curl -sS -X POST -d "vote=a" -H "Cookie: voter_id=$VOTER" http://127.0.0.1:8080/ >/dev/null
sleep 1
RESULT=$(sg docker -c "docker exec infrasage-lab-db-1 psql -U postgres -d postgres -tAc \"select vote from votes where id='$VOTER'\"" 2>/dev/null || echo "")
[[ "$RESULT" == "a" ]] || die "Baseline vote didn't land (got: '$RESULT')"
ok "Baseline vote recorded: $VOTER → a"

# ── 4. Manual fault injection verify ───────────────────────────────────────
step "Fault injection: redis_process_stopped"
sg docker -c "docker stop infrasage-lab-redis-1"
ok "Redis stopped"

step "Verify frontend errors (vote should fail or timeout)"
# With worker restart policy, vote may eventually succeed after retries.
# We just verify the fault is visible in the system (Redis is down).
sleep 1
! sg docker -c "docker exec infrasage-lab-redis-1 redis-cli ping" 2>/dev/null | grep -q PONG || die "Redis should be down"
ok "Redis confirmed down (fault visible)"

step "Revert fault"
sg docker -c "docker start infrasage-lab-redis-1"
sleep 5  # worker needs time to reconnect to Redis
VOTER2="revert-$(date +%s)"
curl -sS -X POST -d "vote=a" -H "Cookie: voter_id=$VOTER2" http://127.0.0.1:8080/ >/dev/null
sleep 2
RESULT2=$(sg docker -c "docker exec infrasage-lab-db-1 psql -U postgres -d postgres -tAc \"select vote from votes where id='$VOTER2'\"" 2>/dev/null || echo "")
[[ "$RESULT2" == "a" ]] || die "Vote didn't recover after revert (got: '$RESULT2')"
ok "Stack healthy after revert"

# ── 5. Full experiment run (real diagnostic) ───────────────────────────────
step "Running full experiment via experiment.run()"
RECORD=$($VENV_PY -c "
from backend.twin.runner import experiment
rec = experiment.run('redis_process_stopped', seed=42, warmup_seconds=2.0)
import json
print(json.dumps(rec))
")

LAB_STATUS=$(echo "$RECORD" | jq -r .lab_status)
RUN_ID=$(echo "$RECORD" | jq -r .run_id)
GT_LABEL=$(echo "$RECORD" | jq -r .ground_truth_label)
SCORED=$(echo "$RECORD" | jq -r .scored)
DECISION=$(echo "$RECORD" | jq -r .diagnosis.decision)

echo "  lab_status:      $LAB_STATUS"
echo "  run_id:          $RUN_ID"
echo "  ground_truth:    $GT_LABEL"
echo "  scored:          $SCORED"
echo "  diagnosis:       $DECISION"

[[ "$LAB_STATUS" == "valid" ]] || die "Experiment lab_status=$LAB_STATUS (expected valid)"
[[ "$GT_LABEL" == "redis_process_stopped" ]] || die "Wrong ground truth label"
# Real diagnostic may abstain (no calibrated likelihoods yet)
[[ "$DECISION" == "abstain" || "$DECISION" == "$GT_LABEL" ]] || die "Unexpected decision: $DECISION"
ok "Experiment completed successfully"

# ── 6. Show run record ─────────────────────────────────────────────────────
step "Run record (experiments/runs/$RUN_ID.json)"
cat "experiments/runs/$RUN_ID.json" | jq .

# ── 7. Ground truth file ───────────────────────────────────────────────────
step "Ground truth (experiments/ground_truth/$RUN_ID.json)"
cat "experiments/ground_truth/$RUN_ID.json" | jq .

echo
echo "=== ALL TESTS PASSED ==="
echo "The experiment tears down its replica; the exit trap also attempts cleanup."
