# DESSMONITOR CURRENT STATE

## Project Identity
- **Name:** dessmonitor
- **Type:** Home automation and pond energy monitoring system
- **Language:** Python (Flask-based API)
- **Database:** TimescaleDB (PostgreSQL extension) for time-series metrics, weather data, and power mode events

## Deployment Pipeline
1. **GitHub Actions** (`.github/workflows/build-and-deploy.yml`):
   - Triggered on push to `master`
   - Builds Docker image and pushes to Docker Hub (`redcopy/dessmonitor`)
   - Tags: `latest` and `${{ github.sha }}`
   - Uses `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` secrets
2. **Docker Hub** stores the image at `redcopy/dessmonitor`
3. **ArgoCD** deploys from Docker Hub. The real ArgoCD/GitOps source of truth is in a separate
   external repository. The `app/docker/` files in this repository are legacy, auxiliary, or
   non-authoritative — they are NOT the real GitOps source of truth.
4. The main manifest references `redcopy/dessmonitor:latest` — a mutable tag (known risk)

## Key Components
- **Dess API** (`app/api.py`): Flask-based API for inverter data
- **Tuya Integration** (`app/tuya/`): Relay control via Tuya IoT platform
- **Weather Service** (`app/weather/`): OpenWeather integration
- **ML Data Collectors** (`app/ml/`): CSV/SQLite and TimescaleDB collectors (not production-enabled)
- **Inverter Monitor** (`service/inverter_monitor.py`): Polls Dess API for inverter data
- **Smart Home Controller** (`app/service/smart_home_controller.py`): Business logic for device control
- **Shared State** (`shared_state/`): In-memory state sharing between components

## Repository Structure
- `.project-memory/` — Project memory and governance (bootstrapped in PR 0001)
- `app/` — Application code
- `service/` — Background services
- `shared_state/` — Shared state module
- `scripts/` — Utility scripts
- `app/docker/` — Kubernetes and ArgoCD manifests
- `app/ml/` — ML-related code (not production-ready)

## PR 0001 Scope
PR 0001 bootstraps repository governance and safety. It does NOT change:
- Runtime Python code
- Dockerfile or docker-compose.yml
- CI/CD workflows
- Kubernetes manifests
- ArgoCD configuration
- Database schema

## PR 0007 — Platform Control Redesign Phase
PR 0007 starts the platform control redesign phase. It documents strategy only.
- The physical pump / water pump is no longer a physical device.
- Pump-specific control logic is obsolete.
- Generic ON/OFF switchable-load control remains a product requirement.
- The next implementation step (PR 0008) will disable/isolate pump automation
  while preserving manual switch control.

## PR 0008 — Pump Automation Disabled by Default
PR 0008 disables active pump automation by default. Manual relay/switch ON/OFF control remains available.

## PR 0009 — Generic Control Domain Types Introduced
PR 0009 introduces generic control domain types (SwitchableLoad, ControlCommand, etc.)
in `app/control/domain.py`. These are passive data definitions only — no runtime
control paths are wired yet. Manual relay/switch ON/OFF remains unchanged. Pump
automation remains disabled by default from PR 0008.

## PR 0010 — Relay-to-SwitchableLoad Mapping Added
PR 0010 adds a passive adapter mapping relay-channel-shaped objects to SwitchableLoad
instances. The mapping is not wired into runtime behavior yet. Manual relay/switch ON/OFF
remains unchanged. Pump automation remains disabled by default from PR 0008.

## PR 0011 — Energy-Aware Control Policy Requirements
PR 0011 documents energy-aware control as the core product policy direction.
Runtime automation is not enabled by PR 0011. Manual relay/switch ON/OFF remains
unchanged. Pump automation remains disabled by default from PR 0008. ML control
remains deferred.

## PR 0012 — Passive Energy Policy Domain Types
PR 0012 adds passive energy policy domain types (PowerSource, TimeOfDay, Season,
WeatherCondition, LoadClass, DevicePriority, VoltageSnapshot, WeatherForecastSignal,
BatteryReservePolicy, DeviceEnergyPolicy, ReadinessInput, ReadinessResult, HealthInput,
HealthStatus, HealthCheckResult, EnergyPolicyContext, EnergyPolicyDecision) in
`app/control/energy_policy.py`. These are passive data definitions only — no runtime
control paths are wired yet. Runtime automation is not enabled. Manual relay/switch
ON/OFF remains unchanged. Pump automation remains disabled by default from PR 0008.
ML control remains deferred.

## PR 0013 — Static No-Secret Energy Policy Config Example
PR 0013 adds a static no-secret energy policy config example in
`examples/energy_policy.example.yaml`. The example is documentation-only and is
not runtime-loaded. No config loader, evaluator, or runtime wiring is added.
Runtime automation is not enabled. Manual relay/switch ON/OFF remains unchanged.
Pump automation remains disabled by default from PR 0008. ML control remains disabled.

## PR 0014 — Pure Readiness Evaluator
PR 0014 adds a pure deterministic readiness evaluator in `app/control/readiness.py`.
The evaluator is not runtime-wired. It does not switch devices. Runtime automation
is not enabled. Manual relay/switch ON/OFF remains unchanged. Pump automation remains
disabled by default from PR 0008. ML control remains disabled.

## PR 0015 — Pure Health Evaluator
PR 0015 adds a pure deterministic health evaluator in `app/control/health.py`.
The evaluator is not runtime-wired. It does not switch devices or retry switching.
Runtime automation is not enabled. Manual relay/switch ON/OFF remains unchanged.
Pump automation remains disabled by default from PR 0008. ML control remains disabled.

## PR 0016 — Schedule Profile Model
PR 0016 adds a passive schedule profile model in `app/control/schedule_profile.py`
with frozen dataclasses (ScheduleWindow, ScheduleProfile, LoadScheduleProfile)
defining time-of-day windows, day-of-week applicability, seasonal applicability,
and check interval configuration. The model is not runtime-wired. No schedule
execution is implemented. Runtime automation is not enabled. Manual relay/switch
ON/OFF remains unchanged. Pump automation remains disabled by default from
PR 0008. ML control remains disabled.

## PR 0017 — Weather Adjustment Evaluator
PR 0017 adds a pure deterministic weather adjustment evaluator in
`app/control/weather_adjustment.py`. The evaluator translates a passive
WeatherForecastSignal into an advisory energy adjustment result (decision,
factor, reason, follow-up). It does not fetch weather, import OpenWeather,
execute control, or switch devices. It is not runtime-wired. Runtime
automation is not enabled. Manual relay/switch ON/OFF remains unchanged.
Pump automation remains disabled by default from PR 0008. ML control
remains disabled.

## PR 0018A — Policy Engine Operating Boundaries
PR 0018A records policy engine operating boundaries, battery/inverter extrema, pond/fish
life-support invariants, forecast-aware strategy rules, and a scenario matrix in
`.project-memory/POLICY_DECISION_ENGINE.md`. No decision engine implementation yet.
No runtime wiring. No execution. No automation. Pond/fish aeration life-support invariant
recorded. Battery/inverter extrema recorded. Future sub-PRs: 0018B (models), 0018C
(decision function), 0018D (scenario tests).

## PR 0018B — Passive Policy Engine Models
PR 0018B adds seven passive policy engine model types in
`app/control/policy_models.py`: BatteryOperatingWindow, EnergyBudget,
PondSafetyContext, ForecastStrategyContext, LoadCandidate, PolicyDecisionInput,
PolicyDecisionResult. All types are frozen dataclasses with no evaluation logic.
No evaluate_policy_decision implementation. No command proposal. No runtime wiring.
No automation enabled. Pond aeration life-support term recorded. Battery/inverter
boundaries recorded.

## PR 0018C — Pure Deterministic Policy Decision Engine
PR 0018C implements `evaluate_policy_decision()` in `app/control/policy_decision.py`.
The engine combines battery window, energy budget, load wattage, readiness, health,
schedule, weather adjustment, forecast strategy, and pond life-support context into
a single advisory PolicyDecisionResult. The engine is pure, deterministic, and
side-effect-free. It does not execute commands, propose actions, or wire into runtime.
No automation enabled. No ML control.

## PR 0018D — Policy Decision Scenario Matrix Tests
PR 0018D documents 16 operating scenarios in `.project-memory/POLICY_DECISION_SCENARIOS.md`
and provides a standalone Python validation script (`scripts/check-policy-decision-scenarios.sh`)
that asserts evaluate_policy_decision reason strings and decision behavior for each scenario.
No runtime wiring added. No command proposal. No execution. No automation.

## PR 0019 — Manual Control Queue Boundary
PR 0019 adds a pure manual control queue boundary in
`app/control/manual_control_queue.py` with 5 passive types (ManualControlStatus,
ManualControlCommand, ManualControlQueueItem, ManualControlQueueSnapshot,
ManualControlQueueResult) and 2 pure functions (enqueue_manual_control_command,
cancel_manual_control_command). No hardware execution. No command proposal.
No runtime wiring. No API endpoints. No automation.

## PR 0020 — Command Intent and Proposal Arbitration
PR 0020 adds pure command intent/proposal arbitration in
`app/control/command_arbitration.py`. Autonomous operation remains default.
Operator/web UI input is an override/correction layer. CommandProposal is
modeled but not executed. No API endpoints. No runtime wiring. No execution.
No automation. No ML control.

## PR 0021 — Command Safety Gate Model
PR 0021 adds a pure command safety gate model in
`app/control/command_safety_gate.py` with 5 types (SafetyGateStatus,
SafetyGateCheck, CommandSafetyContext, CommandSafetyGateInput,
CommandSafetyGateResult) and the pure function
`evaluate_command_safety_gate()`. Documents safety gate architecture in
`.project-memory/COMMAND_SAFETY_GATES.md`. No executor. No runtime wiring.
No API endpoints. No hardware execution.

## PR 0022 — Controlled Execution Eligibility Model
PR 0022 adds a pure controlled execution eligibility model in
`app/control/execution_eligibility.py` with 5 types (ExecutionEligibilityStatus,
ExecutionEligibilityMode, ExecutionEligibilityContext, ExecutionEligibilityInput,
ExecutionEligibilityResult) and the pure function
`evaluate_execution_eligibility()`. Documents controlled execution eligibility
architecture in `.project-memory/CONTROLLED_EXECUTION_ELIGIBILITY.md`.
execution_allowed_now is always false. No executor. No runtime wiring.
No API endpoints. No hardware execution.

## PR 0023 — Runtime Read-Only Control State Snapshot
PR 0023 adds a read-only control state snapshot model in
`app/control/control_state_snapshot.py` with 6 types (ControlStateSnapshotStatus,
LoadControlSnapshot, ControlPipelineSnapshot, ControlModeSnapshot,
ControlStateSnapshotInput, ControlStateSnapshot) and the pure function
`build_control_state_snapshot()`. Packages already-computed control-layer state
for future web UI/observability. No executor. No runtime wiring. No API endpoints.
No hardware execution.

## Known Follow-Up Risks
1. **Mutable `:latest` tag** in ArgoCD manifest — production risk (PR 0003)
2. **Manifest sprawl** in `app/docker/` — multiple locations for K8s manifests (PR 0003)
3. **No test baseline** — no unit or integration tests (PR 0002)
4. **ML code not production-ready** — requires safety validation before enablement (PR 0004+)
5. **TimescaleDB not production-enabled** — requires connectivity and migration verification (PR 0002+)
6. **No CI validation** — no lint, type-check, or test steps in CI (PR 0002+)

## Repository Safety
- `.gitignore` covers: `config.json`, `devices.yaml`, `logs/`, `*.sqlite*`, `ml_data/`, `*.csv`, `*.jsonl`, `__pycache__/`, `.env`, IDE files, and more
- `.dockerignore` covers: `logs/`, `app/cache/`, `config.json`, `devices.yaml`, `__pycache__/`, `.env`, `.vscode/`, and more
- No tracked secrets or runtime artifacts found in Git index
- `devices_prod.yaml` is now ignored (added in PR 0001)
- `.project-memory/` is now ignored (added in PR 0001)
