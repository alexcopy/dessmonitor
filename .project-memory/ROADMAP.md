# DESSMONITOR ROADMAP

## Phase 0: Repository Governance and Safety (PR 0001) ✅
- [x] Bootstrap `.project-memory/` directory structure
- [x] Create AGENT_STANDARD.txt and ORCHESTRATOR_STANDARD.txt
- [x] Create ROADMAP.md, CURRENT_STATE.md
- [x] Create ADRs for agent workflow, deployment, and runtime safety
- [x] Strengthen `.gitignore` and `.dockerignore` for secrets and runtime data
- [x] Add repository safety check script
- [x] Document current state and known risks

## Phase 1: Runtime Fixes and Validation Baseline (PR 0002+)
- [ ] Fix `app/api.py` runtime issues (if any)
- [ ] Fix TimescaleDB code issues (if any)
- [ ] Fix Tuya controller code issues (if any)
- [ ] Fix OpenWeather service issues (if any)
- [ ] Establish test baseline (unit tests, integration tests)
- [ ] Add CI validation steps (lint, type-check, test)

## Phase 2: ArgoCD and GitOps Cleanup (PR 0003+)
- [ ] Consolidate `app/docker/` manifest files
- [ ] Remove `app/docker/all_in_one/` directory
- [ ] Switch from `:latest` to `${{ github.sha }}` immutable image tags
- [ ] Introduce Kustomize or Helm overlays (dev/staging/prod)
- [ ] Extract ArgoCD Application CRD from deployment manifests
- [ ] Clean up duplicate or unused manifest files

## Phase 2b: Platform Control Redesign — Staged Backend Refactor (PR 0007+)
- [x] PR 0007 — Strategy and inventory (documentation only)
- [x] PR 0008 — Disable pump automation, preserve manual switch ON/OFF
- [x] PR 0009 — Introduce generic control domain types (SwitchableLoad, ControlCommand)
- [x] PR 0010 — Backward-compatible adapter from old config to generic load
- [x] PR 0011 — Energy-aware control policy requirements documented
- [x] PR 0012 — Passive energy policy domain types
- [x] PR 0013 — Static policy configuration example without secrets
- [x] PR 0014 — Readiness evaluator, pure function only
- [x] PR 0015 — Health evaluator, pure function only
- [x] PR 0016 — Schedule profile model
- [x] PR 0017 — Weather adjustment evaluator
- [x] PR 0018A — Policy engine operating boundaries (documentation only)
- [x] PR 0018B — Passive policy engine models
- [x] PR 0018C — Pure deterministic policy decision engine
- [x] PR 0018D — Scenario matrix and regression tests
- [x] PR 0019 — Manual control queue boundary
- [x] PR 0020 — Command intent and proposal arbitration
- [x] PR 0021 — Command safety gate model
- [x] PR 0022 — Controlled execution eligibility model
- [x] PR 0023 — Runtime read-only control state snapshot
- [x] PR 0024 — Runtime read-only control snapshot adapter
- [x] PR 0025 — Web UI read-only control state API contract
- [x] PR 0026 — Web UI read-only control state endpoint implementation plan
- [x] PR 0027 — Web UI read-only control state endpoint
- [x] PR 0028b — Minimal read-only FastAPI web host bootstrap
- [x] PR 0029 — Runtime read-only control state provider
- [x] PR 0030 — Runtime read-only web host startup
- [ ] Later — runtime wiring for read-only endpoint (separate safety-reviewed PR)
- [ ] Much later — ML control (only after separate safety-reviewed approval)

**PR 0017 note:** Pure weather adjustment evaluator follows schedule profile model (PR 0016). Future PRs will add deterministic policy decision engine, command proposal, controlled execution, and ML advisory. Runtime wiring remains deferred. ML control remains deferred behind safety-reviewed gates.

**PR 0018A note:** Policy engine operating boundaries documented. 0018 split into 0018A (boundaries docs), 0018B (passive models), 0018C (pure engine), 0018D (scenario tests). 0019 (manual control API), 0020 (command proposal before automatic execution). Controlled execution, ML advisory, and ML control all remain later milestones.

## Phase 3: Safety-First Adaptive Control (PR 0004+)
- [ ] Implement safety policy and fallback mechanism for relay control
- [ ] Implement shadow/advisory mode for ML-based control
- [ ] Add monitoring and alerting for control actions
- [ ] Validate control safety before enabling production ML control

## Phase 4: ML Control Enablement (PR 0005+)
- [ ] Enable `ml_data_analyzer` in production
- [ ] Enable cloud streaming collector
- [ ] Enable ML training pipeline
- [ ] Enable ML-based adaptive control (shadow mode first)
- [ ] Gradual rollout to production control

## Phase 5: Infrastructure Hardening (Future)
- [ ] Credential rotation (manual operator action if exposure found)
- [ ] Multi-arch Docker builds
- [ ] CI/CD pipeline improvements
- [ ] Monitoring and observability enhancements

---

**Note:** ML control is NOT production-ready. Phase 3 and Phase 4 require
safety-first validation before any ML model directly operates relays or
makes control decisions.
