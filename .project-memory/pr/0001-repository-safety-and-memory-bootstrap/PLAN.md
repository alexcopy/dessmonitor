# PR 0001 — Repository Safety and Project Memory Bootstrap

## 1. Current Repository State (at HEAD `b238b508dc`)

### Identity
- **Project name**: dessmonitor — a home automation and pond energy monitoring system.
- **Language**: Python (Flask-based API).
- **Database**: TimescaleDB (PostgreSQL extension) for time-series metrics, weather data, and power mode events.
- **Deployment**: Docker container running on a Kubernetes cluster, deployed via ArgoCD from Docker Hub.

### Directory Layout (relevant to this PR)
| Path | Status | Notes |
|---|---|---|
| `.gitignore` | Exists | Already covers `config.json`, `devices.yaml`, `logs/`, `*.sqlite*`, `ml_data/`, `*.csv`, `*.jsonl`, `__pycache__/`, `.env`, and common IDE files. |
| `.dockerignore` | Exists | Covers `logs/`, `app/cache/`, `config.json`, `devices.yaml`, `__pycache__/`, `.env`, `.vscode/`. **Gaps identified below.** |
| `.project-memory/` | **Missing** | Entire directory does not exist anywhere on disk. |
| `Dockerfile` | Exists | Multi-stage build. |
| `docker-compose.yml` | Exists | Local development compose (TimescaleDB + app). |
| `run.py` | Exists | Application entry point. |
| `app/api.py` | Exists | Flask app with routes. |
| `app/ml/timescale_data_collector.py` | Exists | TimescaleDB data collector with adaptive intervals. |
| `init-db.sql` | Exists | TimescaleDB schema (device_metrics, power_mode_events, weather_data, ml_solar_training_data view). |
| `.github/workflows/build-and-deploy.yml` | Exists | CI/CD workflow. |
| `app/docker/dessmonitor-deploy.yaml` | Exists | Primary ArgoCD manifest (Namespace, Deployment, Service, Ingress). |
| `app/docker/` | Exists | Contains multiple K8s manifests including all_in_one/, Grafana, Loki, cert-manager issuer. |

### Current CI/CD Pipeline
1. **GitHub Actions** (`.github/workflows/build-and-deploy.yml`):
   - Triggered on push to `master`.
   - Builds Docker image and pushes to Docker Hub (`redcopy/dessmonitor`).
   - Tags: `latest` and `${{ github.sha }}`.
   - Uses `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` secrets.
2. **Docker Hub** stores the image.
3. **ArgoCD** deploys from Docker Hub using the GitOps manifests in `app/docker/dessmonitor-deploy.yaml` (and associated files in `app/docker/`).
4. The main manifest references `redcopy/dessmonitor:latest` — an **immutable tag problem exists** (later PR).

### Findings from Required Reads

#### Finding F-001: `.dockerignore` gaps
Current `.dockerignore` omits: `ml_data/`, `*.sqlite`, `*.sqlite3`, `*.sqlite-wal`, `*.sqlite-shm`, `*.csv`, `*.jsonl`, `.env.*`, `*.pem`, `*.key`, `*.crt`, `*.bak`, `*.swp`, `*.swo`, `.idea/`, `Thumbs.db`, `.DS_Store`.

#### Finding F-002: Multiple K8s manifest locations
Deployment manifests exist in `app/docker/` (main deploy file), `app/docker/all_in_one/` (older combined manifests), plus standalone files like `cluster-issuer.yaml`, `grafana-ingress.yaml`, `grafana-values.yaml`, `ingress-controller.yaml`, `loki-stack-values.yaml`. This is a manifest sprawl that should be rationalized in a later PR.

#### Finding F-003: No project-memory directory
No `.project-memory/` directory exists. This PR will bootstrap it.

#### Finding F-004: No tracked secret/config files found
`git ls-files` confirms none of the dangerous patterns (`config.json`, `.env`, `*.pem`, `*.sqlite`, etc.) are currently tracked by Git. No `git rm --cached` actions are needed in this PR.

#### Finding F-005: `web_fallback_url.txt` not in `.dockerignore`
`web_fallback_url.txt` is referenced as a ConfigMap mount in the K8s manifest but is not excluded from the Docker image build.

#### Finding F-006: All required directories exist
`app/docker`, `app/ml`, `app/weather`, `app/tuya`, `app/monitoring`, `service`, `shared_state` — all present.

#### Finding F-007: `devices_prod.yaml` not in `.gitignore`
`devices_prod.yaml` is missing from both `.gitignore` and `.dockerignore`.

## 2. PR 0001 Scope

PR 0001 is the **first bootstrap PR**. It adds project memory infrastructure, repository governance, and safety hardening. It **must not** alter production runtime behavior, deployment pipeline logic, CI/CD workflow steps, Docker build behavior, or any application code.

### 2.1 Files to Create

| # | File Path | Purpose |
|---|---|---|
| 1 | `.project-memory/` | Root directory for project memory |
| 2 | `.project-memory/AGENT_STANDARD.txt` | Agent behavioral contract adapted for dessmonitor |
| 3 | `.project-memory/ORCHESTRATOR_STANDARD.txt` | Orchestrator behavioral contract adapted for dessmonitor |
| 4 | `.project-memory/ROADMAP.md` | High-level roadmap with phases |
| 5 | `.project-memory/CURRENT_STATE.md` | Current repository state snapshot |
| 6 | `.project-memory/ADR/ADR-0001-agent-workflow.md` | ADR defining the four-agent workflow |
| 7 | `.project-memory/ADR/ADR-0002-dockerhub-argocd-deployment.md` | ADR documenting current CI/CD pipeline |
| 8 | `.project-memory/ADR/ADR-0003-runtime-safety-before-ml-control.md` | ADR establishing runtime safety as prerequisite |
| 9 | `.project-memory/pr/.gitkeep` | Placeholder for PR artifact directory |
| 10 | After strengthening `.dockerignore` and `.gitignore` | See sections 2.2 and 2.3 below |

### 2.2 Files to Edit — `.dockerignore`

**Current content:**
```
logs/
app/cache/
config.json
devices.yaml
__pycache__/
*.py[cod]
*.swp
.vscode/
.env
```

**Add the following missing patterns:**
```
.git/
.gitignore
.dockerignore
ml_data/
*.sqlite
*.sqlite3
*.sqlite-wal
*.sqlite-shm
*.csv
*.jsonl
.env.*
*.pem
*.key
*.crt
*.bak
*.bak~
*.swo
.idea/
Thumbs.db
.DS_Store
web_fallback_url.txt
config_cache.json
fallback_data.json
devices_prod.yaml
```

**Rationale:** Prevent secret files, runtime data, ML training data, database files, credential files, IDE artifacts, and ephemeral files from being included in the Docker image.

### 2.3 Files to Edit — `.gitignore`

**Gaps identified** (additions only, existing entries preserved):
- `devices_prod.yaml` — production device config
- `.project-memory/` — local project-memory artifacts (PR reports, reviews)
- `token_cache.json` — if any token cache file is generated at root

**Current `.gitignore` already covers:**
- `config.json`, `config_cache.json`, `fallback_data.json`, `devices.yaml` — present
- `web_fallback_url.txt` — present
- `logs/`, `*.log`, `*.pid` — present
- `*.sqlite3` — present
- `ml_data/`, `*.sqlite`, `*.sqlite-wal`, `*.sqlite-shm`, `*.csv`, `*.jsonl` — present
- `app/cache/dess_token.json` — present
- `__pycache__/`, `*.py[cod]` — present
- `.env` — present
- `.venv/`, `venv/`, `env/` — present
- `.idea/`, `.vscode/`, `*.swp`, `*.swo`, `*.bak` — present
- `.DS_Store`, `Thumbs.db` — present

### 2.4 Repository Safety Check Script (Optional)

**Decision:** Include a lightweight safety check script at `scripts/check-repo-safety.sh`.

**Rationale:** Provides a repeatable validation that can be run before commits to detect accidentally tracked runtime artifacts. Does not require external services. Uses only `git ls-files` with pattern matching.

**What it checks:**
- Are any forbidden patterns tracked by Git? (`config.json`, `config_cache.json`, `fallback_data.json`, `devices.yaml`, `devices_prod.yaml`, `web_fallback_url.txt`, `app/cache/**`, `logs/**`, `ml_data/**`, `*.sqlite*`, `*.csv`, `*.jsonl`, `.env*`, `*.pem`, `*.key`, `*.crt`)
- Are there any local untracked files matching those patterns (operator warning about unignored secrets)?
- Does `.dockerignore` cover all required patterns?

**Output:** Exit code 0 if clean; prints warnings to stdout. Non-zero if tracked secrets are found (blocking).

### 2.5 Initial `.project-memory/pr/` Artifact Layout

For this PR's agent workflow:
```
.project-memory/pr/0001-repository-safety-and-memory-bootstrap/
    PLAN.md           ← This file (planning agent)
    PLAN_REVIEW.yaml   ← Plan-review agent output
    CODER_REPORT.txt   ← Coder agent implementation report
    PRECOMMIT_REVIEW.yaml ← Precommit-review agent output
```

## 3. Out of Scope — Later PRs

| Later Work | Target PR | Notes |
|---|---|---|
| ML control enabling | PR 0002+ | Enable ml_data_analyzer, cloud streaming, ML training |
| TimescaleDB production enablement | PR 0002+ | Ensure DB connection, schema migrations, monitoring |
| Tuya/DESS/OpenWeather/Inverter/Relay runtime changes | Separate PRs | Bug fixes, new features, config changes |
| K8s manifest restructuring | PR 0003+ | Consolidate `app/docker/` files, overlay structure, remove all_in_one/ |
| Immutable image tags in ArgoCD | PR 0003+ | Switch from `:latest` to `${{ github.sha }}` |
| GitOps overlays (dev/staging/prod) | PR 0003+ | Kustomize or Helm overlays |
| ArgoCD Application manifest cleanup | PR 0003+ | Separate Application CRD from deployment manifests |
| Credential rotation | Manual operator action | If credential exposure is found, document and rotate manually |
| CI/CD pipeline changes (build steps, secrets, multi-arch) | PR 0004+ | Requires careful testing |

## 4. Agent Workflow for PR 0001

| Step | Agent | Artifact Produced | Action |
|---|---|---|---|
| 1 | **plan** | `.project-memory/pr/0001-repository-safety-and-memory-bootstrap/PLAN.md` | Inspects repo, writes plan |
| 2 | **plan-review** | `.project-memory/pr/0001-repository-safety-and-memory-bootstrap/PLAN_REVIEW.yaml` | Reviews PLAN.md only |
| 3 | **coder** | `.project-memory/pr/0001-repository-safety-and-memory-bootstrap/CODER_REPORT.txt` | Implements approved plan |
| 4 | **precommit-review** | `.project-memory/pr/0001-repository-safety-and-memory-bootstrap/PRECOMMIT_REVIEW.yaml` | Reviews final diff + validation |

**Rule:** No agent may proceed unless the previous artifact is approved.

## 5. Repository Safety Requirements

### 5.1 Check for Accidentally Tracked Artifacts

Before implementation, run:
```bash
git ls-files | grep -E '(config\.json|config_cache\.json|fallback_data\.json|devices\.yaml|devices_prod\.yaml|web_fallback_url\.txt|app/cache/|logs/|ml_data/|\.sqlite|\.sqlite3|\.sqlite-wal|\.sqlite-shm|\.csv|\.jsonl|\.env|\.env\.|\.pem|\.key|\.crt)'
```

If any files are found, they must be removed from the index with:
```bash
git rm --cached <file>
```
**Do not delete the local file.**

### 5.2 Git Archive for Handoff

Repository handoff (zipped) must use:
```bash
git archive --format=zip --output=dessmonitor-<tag>.zip HEAD
```
or
```bash
git ls-files | zip -@ dessmonitor-<tag>.zip
```
**Do not zip the raw folder** — this includes untracked and ignored files.

### 5.3 Production Risk: `:latest` Tag

The ArgoCD manifest (`app/docker/dessmonitor-deploy.yaml`) references `redcopy/dessmonitor:latest`. This is a mutable tag and a production risk. Addressing this is **out of scope for PR 0001** and is routed to **PR 0003**.

## 6. CI/CD and ArgoCD Documentation

### Current Deployment Path
```
[Git push to master]
       ↓
[GitHub Actions: build-and-deploy.yml]
       ↓  builds Docker image
[Docker Hub: redcopy/dessmonitor:{latest,sha}]
       ↓  ArgoCD syncs
[Kubernetes cluster: namespace dess]
       ↓  applies
[app/docker/dessmonitor-deploy.yaml + other manifests]
```

### What PR 0001 Does NOT Change
- GitHub Actions workflow steps, triggers, secrets, or image publishing behavior.
- Docker build process or Dockerfile.
- Docker Hub push or tag strategy.
- ArgoCD sync policy, Application manifest, or deployment settings.
- Ingress, Service, or Deployment specs.

### Later Work Identified
1. **Immutable image tags** in ArgoCD: Switch from `:latest` to `${{ github.sha }}` (or a semantic version).
2. **GitOps overlays**: Introduce `kustomize` overlays for dev/staging/prod environments.
3. **ArgoCD Application manifest**: Extract as a separate CRD from the Deployment manifest.
4. **Manifest consolidation**: Remove `app/docker/all_in_one/` and deduplicate deployment files.

## 7. Implementation Checklist for Coder Agent

- [ ] Create `.project-memory/` directory structure
- [ ] Create `.project-memory/AGENT_STANDARD.txt`
- [ ] Create `.project-memory/ORCHESTRATOR_STANDARD.txt`
- [ ] Create `.project-memory/ROADMAP.md`
- [ ] Create `.project-memory/CURRENT_STATE.md`
- [ ] Create `.project-memory/ADR/ADR-0001-agent-workflow.md`
- [ ] Create `.project-memory/ADR/ADR-0002-dockerhub-argocd-deployment.md`
- [ ] Create `.project-memory/ADR/ADR-0003-runtime-safety-before-ml-control.md`
- [ ] Create `.project-memory/pr/.gitkeep`
- [ ] Update `.dockerignore` with all missing patterns from section 2.2
- [ ] Update `.gitignore` with `devices_prod.yaml` and `.project-memory/`
- [ ] Create `scripts/check-repo-safety.sh`
- [ ] Run safety check: `git ls-files | grep -E <forbidden_patterns>` — verify no tracked secrets
- [ ] If tracked secrets found, run `git rm --cached` (do not delete local files)
- [ ] Verify `test -f .project-memory/pr/0001-repository-safety-and-memory-bootstrap/PLAN.md`
- [ ] Verify all grep checks pass (see Validation Checklist below)

## 8. Validation Checklist (Post-Implementation)

| # | Check | Command / Criterion |
|---|---|---|
| 1 | HEAD matches | `git rev-parse --verify HEAD` = `b238b508dc18a416d2ce76257d6f6cbe5990078f` |
| 2 | Branch is correct | `git branch --show-current` = `0001-repository-safety-and-memory-bootstrap` |
| 3 | Working tree clean | `git status --short` shows only expected new files |
| 4 | PLAN.md exists | `test -f .project-memory/pr/0001-repository-safety-and-memory-bootstrap/PLAN.md` |
| 5 | GitHub Actions documented | `grep -q "GitHub Actions" .project-memory/pr/.../PLAN.md` |
| 6 | Docker Hub documented | `grep -q "Docker Hub" .project-memory/pr/.../PLAN.md` |
| 7 | ArgoCD documented | `grep -q "ArgoCD" .project-memory/pr/.../PLAN.md` |
| 8 | Four-agent flow | `grep -q "plan-review" .project-memory/pr/.../PLAN.md` |
| 9 | Four-agent flow | `grep -q "precommit-review" .project-memory/pr/.../PLAN.md` |
| 10 | No tracked secrets | `git ls-files \| grep -E '^('"${FORBIDDEN_PATTERNS}"')$'` returns empty |
| 11 | .dockerignore covers all | Verify patterns from section 2.2 exist in `.dockerignore` |
| 12 | .gitignore covers gaps | Verify `devices_prod.yaml` and `.project-memory/` are in `.gitignore` |

## 9. Boundary Confirmations

- **This PR does not alter production runtime behavior.** No Python, SQL, YAML (runtime), Dockerfile, or workflow logic is changed except `.dockerignore` and `.gitignore` patterns.
- **This PR does not change the CI/CD pipeline.** The GitHub Actions workflow, Docker Hub push, and ArgoCD sync remain identical.
- **This PR does not enable ML, TimescaleDB, Tuya, or any device control.** Those remain in their current state.
- **This PR does not restructure Kubernetes manifests.** The `app/docker/` directory is documented but not modified.
- **No credentials are rotated in this PR.** If credential exposure is found, it is documented for manual operator action.
