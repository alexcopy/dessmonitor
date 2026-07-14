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
