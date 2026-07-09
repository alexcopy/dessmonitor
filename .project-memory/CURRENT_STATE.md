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
3. **ArgoCD** deploys from Docker Hub using GitOps manifests in `app/docker/dessmonitor-deploy.yaml`
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
