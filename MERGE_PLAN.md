# InfraSage Merge Plan: main + feat/twin-phase-2 → integration/consolidated

**Decision: Netdata = Primary Observer** (Option A)
- Netdata handles metrics collection (CPU, memory, network, disk)
- Docker complements with container state, health, ports, restarts, precision probes
- 11 netdata tests in tests.yaml need bindings + live flags

---

## Phase 0: Setup
```bash
cd /home/divinesword/INDIA/Infrasage
git checkout feat/twin-phase-2
git checkout -b integration/consolidated
```

---

## Phase 1: Delete Unnecessary Files/Folders (from feat/twin-phase-2)

### Docs to archive (then delete)
- `docs/PHASE*.md` → `docs/archive/`
- `docs/PIPELINE_READINESS.md` → `docs/archive/`
- `docs/PROTOTYPE_INTEGRATION.md` → `docs/archive/`
- Any audit reports in `docs/` → `docs/archive/`

### Files already deleted in feat/twin-phase-2 (keep deleted)
- `netdata_charts.json` (38k lines, no refs)

### Duplicate/conflicting lab files
- Keep `lab/compose.yaml` (demo) + `lab/compose.twin.yaml` (twin) - **restore compose.twin.yaml from main**
- Delete any merged compose attempt

---

## Phase 2: Restore Diagnostic Core from main

### Directories to copy wholesale
```bash
# From main branch
git show main:backend/collectors/ > restore collectors
git show main:backend/core/ > restore core
git show main:backend/diagnosis/ > restore diagnosis
git show main:backend/processing/ > restore processing
git show main:backend/optimization/ > restore optimization
git show main:backend/energy/ > restore energy
git show main:backend/api/ > restore api
git show main:backend/main.py > restore main.py
git show main:tests/ > restore tests
git show main:pytest.ini > restore pytest.ini
```

### Key files that must come from main (hardened/security-sensitive)
- `backend/collectors/tcp_probe.py` + `tcp_probe_helper.py`
- `scripts/build_tcp_probe_image.py`
- `backend/collectors/docker.py` (but fix bugs per Phase 5)

---

## Phase 3: Apply Twin Fixes from main

### Specific files to copy from main
```bash
git show main:backend/twin/energy_client.py > backend/twin/energy_client.py
git show main:backend/twin/runner/experiment.py > backend/twin/runner/experiment.py
git show main:backend/twin/runner/lab.py > backend/twin/runner/lab.py
git show main:backend/twin/validation.py > backend/twin/validation.py
git show main:backend/twin/scoring/aggregate.py > backend/twin/scoring/aggregate.py
git show main:lab/compose.twin.yaml > lab/compose.twin.yaml
```

---

## Phase 4: Restore Lab Assets from main

```bash
# Lab directories
git show main:lab/voting_app.py > lab/voting_app.py
git show main:lab/vote/ > lab/vote/
git show main:lab/worker/ > lab/worker/
git show main:lab/result/ > lab/result/
git show main:lab/seed-data/ > lab/seed-data/
git show main:lab/healthchecks/ > lab/healthchecks/
git show main:lab/tcp-probe/ > lab/tcp-probe/

# Entry points
git show main:infrasage.sh > infrasage.sh
git show main:scripts/build_tcp_probe_image.py > scripts/build_tcp_probe_image.py
git show main:scripts/manual_checks/ > scripts/manual_checks/  # label optional
```

---

## Phase 5: Fix 8 Bugs

| # | File | Fix |
|---|------|-----|
| 1 | `backend/twin/tests/conftest.py:117-126` | `head` → `args[0]`, remove/fix `_service_from_cid` |
| 2 | `backend/collectors/docker.py` collect() | Catch per-container, parallelize stats calls |
| 3 | `backend/core/orchestrator.py` validate_evidence() | Move 15s literal → `LiveInvestigationConfig.freshness_window_seconds` |
| 4 | `backend/collectors/tcp_probe.py` valid_probe_config | Add host allowlist (127.0.0.0/8, 172.18.0.0/16, 10.0.0.0/8) |
| 5 | `backend/collectors/tcp_probe.py` result payload | Redact `docker_endpoint` |
| 6 | Unused imports (~25) | `pyflakes` → remove |
| 7 | `backend/twin/runner/experiment.py` | Remove unused `_ENERGY_SERVER_THREAD` global |
| 8 | `backend/collectors/docker.py` | CRLF→LF, French→English comments |

---

## Phase 6: Netdata Primary - Update Bindings (Decision 1)

### tests.yaml - 11 netdata tests need:
- `binding_status: verified` (if they work)
- `live: true` (if passing live run exists)
- Provider: `netdata` with proper operation bindings

### AGENTS.md - confirm:
- "Netdata is primary observer" statement
- Docker = "complements with container state, health, ports, restarts, precision probes"

---

## Phase 7: Live Test Decisions (Decision 2)

For each of 20 `binding_status: verified` tests:
- Run live in twin lab
- Set `live: true` only if passing live record exists
- Link record in `STATUS.md`

---

## Phase 8: Calibration Policy (Decisions 4, 5)

### Decision 4: Old calibration data
- Tag `experiments/data/phase2c/`, `readiness-2026-11-15/` with `schema_version: "pre-merge"`
- Move to `experiments/archive/`
- `aggregate.py` filters on `schema_version == "merged"`

### Decision 5: other_unknown class
- Document in `probabilities.json`: `usage_policy.live_diagnosis: false`
- Keep uninformative placeholder in catalogue
- Note: live path uses same priors but abstains on unknown

---

## Phase 9: Archive Old Records

```bash
mkdir -p docs/archive experiments/archive
mv docs/PHASE*.md docs/PIPELINE_READINESS.md docs/PROTOTYPE_INTEGRATION.md docs/archive/ 2>/dev/null || true
mv experiments/data/phase2c experiments/data/readiness-2026-11-15 experiments/archive/ 2>/dev/null || true
mv experiments/validation_results.json experiments/validation/seed_determinism.json experiments/archive/ 2>/dev/null || true
```

---

## Phase 10: Update AGENTS.md

Align architecture section with:
- Netdata primary, Docker complementary
- Live test flags from Phase 7
- Calibration policy from Phase 8
- Lab uses `compose.twin.yaml` (not `compose.yaml`)

---

## Phase 11: Verify Acceptance Criteria

```bash
# 1. Full test suite ≥ 441 tests
.venv/bin/python -m pytest -q tests backend/twin/tests

# 2. pyflakes clean
.venv/bin/pip install pyflakes
.venv/bin/python -m pyflakes backend/ tests/

# 3. Live tests have records in STATUS.md
# 4. AGENTS.md, README.md, STATUS.md consistent
# 5. Twin validation = "unverified" in STATUS.md
# 6. No mixed schema versions in calibration
# 7. infrasage.sh start && investigate work
# 8. TCP probe builds: scripts/build_tcp_probe_image.py
```

---

## Rollback Plan

If merge breaks:
```bash
git checkout main
git branch -D integration/consolidated
# Or cherry-pick specific fixes back to feat/twin-phase-2
```