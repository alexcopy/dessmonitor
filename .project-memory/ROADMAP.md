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
- [ ] PR 0018 — Deterministic policy decision engine, no hardware execution
- [ ] PR 0019 — Manual control API or command queue
- [ ] PR 0020 — Command proposal before automatic execution
- [ ] Later — Controlled execution with safety gates
- [ ] Later — ML advisory
- [ ] Much later — ML control (only after separate safety-reviewed approval)

**PR 0017 note:** Pure weather adjustment evaluator follows schedule profile model (PR 0016). Future PRs will add deterministic policy decision engine, command proposal, controlled execution, and ML advisory. Runtime wiring remains deferred. ML control remains deferred behind safety-reviewed gates.

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
