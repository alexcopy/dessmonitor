# PR 0003 — Validation Baseline

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `72ef3ab9eeacf66c695c8c25ef70f04ea9854b8d` |
| Branch | `git branch --show-current` | `0003-validation-baseline` |
| Working tree | `git status --short` | clean (no local changes) |

The precondition passes. Branch is `0003-validation-baseline` and working tree is clean.

## 2. Current Validation State

### 2.1 Current CI/CD Deployment Pipeline

The current deployment pipeline is:

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

This PR 0003 does NOT change this pipeline. The new `validate.yml` workflow is additive — it runs independently and never publishes images, connects to Docker Hub, or modifies ArgoCD.

### 2.2 Existing CI Workflow

The only workflow is `.github/workflows/build-and-deploy.yml` (GitHub Actions). It:
- Runs on push to `master` and `workflow_dispatch`.
- Builds Docker image and pushes to Docker Hub (`redcopy/dessmonitor`).
- Tags with `latest` and `${{ github.sha }}`.
- Has **no validation steps** before the build. No syntax check, no lint, no safety check.
- Requires `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` secrets.

### 2.3 Existing Scripts

Only `scripts/check-repo-safety.sh` exists (created in PR 0001). It checks:
1. Tracked forbidden patterns (secrets, runtime data, configs).
2. Untracked forbidden files (warning only).
3. `.dockerignore` coverage for required patterns.

It runs locally without network, Docker, or external service dependencies.

### 2.4 Existing Validation Gaps

- No Python syntax validation in CI or local workflows.
- No project-memory governance structure validation.
- No YAML syntax validation (important for K8s manifests, Docker Compose, CI workflows, and agent definitions).
- No `tests/` directory (too broad for PR 0003; deferred to later PR).
- No CI-level validation gate before the Docker build step.

### 2.5 Required Dependencies Already Present

- `requirements.txt` includes `PyYAML==6.0.2` — YAML parsing is available without adding dependencies.
- `python3` is available on GitHub Actions runners (ubuntu-latest) and locally.

### 2.6 Findings

#### Finding F-001: No validation workflow
The CI pipeline builds and pushes on every push to `master` with zero validation. A syntax or safety error would only be caught after the Docker image is built or during runtime.

#### Finding F-002: No project-memory structure check
There is no automated check that the required governance files (AGENT_STANDARD.txt, ORCHESTRATOR_STANDARD.txt, ROADMAP.md, CURRENT_STATE.md, ADR directory) exist.

#### Finding F-003: No YAML validation
The repository has 20+ YAML files (K8s manifests, Docker Compose, CI workflow, agent definitions). A YAML syntax error in any of these could break deployment or agent behavior. Multi-document YAML (`app/docker/dessmonitor-deploy.yaml` has 4 documents separated by `---`) must be supported.

#### Finding F-004: Python syntax not validated
`run.py` and all `app/` Python files are never syntax-checked before build.

## 3. PR 0003 Implementation Scope

This PR adds validation checks only. It does not change runtime behavior, Docker Hub publishing, ArgoCD configuration, or ML control.

### 3.1 Must Implement

1. **Add a new GitHub Actions validation workflow** `.github/workflows/validate.yml` that runs on `pull_request` and `push` to `master`. This workflow must:
   - Checkout the repository.
   - Set up Python 3.12.
   - Install dependencies (`pip install -r requirements.txt`).
   - Run Python syntax validation (`python -m compileall -q .` with exclude patterns for `ml_data`, `.project-memory`, `.git`).
   - Run YAML syntax validation (`scripts/validate-yaml.py` against all YAML/yml files).
   - Run repository safety check (`scripts/check-repo-safety.sh` — already exists).
   - Run project-memory structure check (`scripts/check-project-memory.sh` — to be created).
   - **Must NOT** require Docker Hub credentials.
   - **Must NOT** build or push Docker images.
   - **Must NOT** require network access, Docker, Tuya, DESS, OpenWeather, TimescaleDB, or K8s cluster access.

2. **Create `scripts/validate-yaml.py`** — Python script that:
   - Finds all `*.yaml` and `*.yml` files under the repo root (excluding `.project-memory/`, `ml_data/`, `venv/`, `.venv/`).
   - Reads each file.
   - Uses `yaml.safe_load_all()` to handle multi-document YAML (separated by `---`).
   - Prints actionable error messages like `ERROR: <file>:<line>: <error description>`.
   - Returns exit code 0 on success, 1 on failure.
   - Does NOT require Docker, K8s API, or network access.
   - Does NOT read secret values.

3. **Create `scripts/check-project-memory.sh`** — Shell script that:
   - Verifies the existence of required governance files:
     - `.project-memory/AGENT_STANDARD.txt`
     - `.project-memory/ORCHESTRATOR_STANDARD.txt`
     - `.project-memory/ROADMAP.md`
     - `.project-memory/CURRENT_STATE.md`
     - `.project-memory/ADR/ADR-0001-agent-workflow.md`
     - `.project-memory/ADR/ADR-0002-dockerhub-argocd-deployment.md`
     - `.project-memory/ADR/ADR-0003-runtime-safety-before-ml-control.md`
   - Returns exit code 0 if all exist, 1 if any are missing.
   - Prints the missing files list.

### 3.2 Must Edit

| File | Change |
|---|---|
| `.github/workflows/validate.yml` | **Create** — new validation workflow |
| `scripts/validate-yaml.py` | **Create** — YAML validation script |
| `scripts/check-project-memory.sh` | **Create** — project-memory structure check script |

### 3.3 Must Not Edit

All other files, **including but not limited to**:
- `run.py` and all files under `app/`, `service/`, `shared_state/`
- `.github/workflows/build-and-deploy.yml` (unchanged — preserved as-is)
- `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.gitignore`
- `init-db.sql`
- `app/docker/*` (K8s manifests)
- `requirements.txt` (PyYAML is already present)
- `.project-memory/` standards, ADRs, ROADMAP.md, CURRENT_STATE.md
- `.project-memory/pr/0003-validation-baseline/PLAN.md` (locked)
- `.project-memory/pr/0003-validation-baseline/PLAN_REVIEW.yaml` (locked)

### 3.4 Artifact Layout

```
.project-memory/pr/0003-validation-baseline/
    PLAN.md                    ← This file (planning agent, LOCKED)
    PLAN_REVIEW.yaml           ← Plan-review agent (LOCKED after approval)
    CODER_REPORT.txt           ← Coder agent implementation report
    PRECOMMIT_REVIEW.yaml      ← Precommit-review agent
```

### 3.5 Agent Workflow

| Step | Agent | Artifact | Constraint |
|---|---|---|---|
| 1 | plan | `PLAN.md` | Writes plan |
| 2 | plan-review | `PLAN_REVIEW.yaml` | Reviews PLAN.md only. PLAN.md and PLAN_REVIEW.yaml are LOCKED after approval |
| 3 | coder | `CODER_REPORT.txt` | Implements approved plan. Must NOT edit PLAN.md or PLAN_REVIEW.yaml |
| 4 | precommit-review | `PRECOMMIT_REVIEW.yaml` | Reviews final diff + validation. Must NOT edit PLAN.md or PLAN_REVIEW.yaml |

## 4. Detailed Specifications

### 4.1 CI Validation Workflow (`.github/workflows/validate.yml`)

```yaml
name: Validate

on:
  pull_request:
    branches: [ master ]
  push:
    branches: [ master ]

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - name: ⬇️  Checkout
        uses: actions/checkout@v4

      - name: 🔧 Set up Python 3.12
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: 📦 Install dependencies
        run: pip install -r requirements.txt

      - name: 🔍 Python syntax validation
        run: python -m compileall -q . -x '/(ml_data|\.project-memory|\.git|venv|\.venv)/'

      - name: 🔍 YAML syntax validation
        run: python scripts/validate-yaml.py

      - name: 🔍 Repository safety check
        run: bash scripts/check-repo-safety.sh

      - name: 🔍 Project-memory structure check
        run: bash scripts/check-project-memory.sh
```

**Key properties:**
- Runs on both `pull_request` (pre-merge) and `push` to `master` (post-merge).
- Does NOT require Docker Hub credentials — no `docker/login-action` step.
- Does NOT build or push Docker images — no `docker/build-push-action` step.
- All steps are deterministic, offline, and local.
- Does NOT call external services.

### 4.2 YAML Validation Script (`scripts/validate-yaml.py`)

**Requirements:**
- Python 3 script using the built-in `os`, `sys`, `glob`, and the already-available `yaml` (PyYAML).
- Find all `*.yaml` and `*.yml` files recursively, excluding:
  - `.project-memory/` directory
  - `ml_data/` directory
  - `.git/` directory
  - `venv/`, `.venv/` directories
- For each file:
  - Read the file content.
  - If the content is empty or contains only whitespace, skip (some agent YAML files may be empty).
  - Use `yaml.safe_load_all()` to parse multi-document YAML.
  - Iterate over all documents; if any document fails to parse, capture the error with line number.
- Print errors in format: `ERROR: <relative_path>: line <lineno>: <description>`
- On first error, continue to check remaining files (report all errors, not just the first).
- Exit code 0 if all files parse successfully, exit code 1 if any error.

**Important edge cases handled:**
- Multi-document YAML (`---` separator) — uses `safe_load_all()`.
- Empty YAML files — skip gracefully.
- Files with only comments — valid YAML, accept.
- Large files — not expected, but no size limit on reading.
- Files with special characters in paths — use `os.path.relpath()` for display.

### 4.3 Project-Memory Structure Check (`scripts/check-project-memory.sh`)

**Requirements:**
- Bash script (matching `check-repo-safety.sh` style).
- Check each file listed in section 3.1 item 3.
- Print `OK: <file>` if found, `MISSING: <file>` if not.
- Exit code 0 if all files present, 1 if any missing.
- No dependencies beyond core UNIX utilities (`test`, `echo`, etc.).

### 4.4 Python Syntax Validation (CI-only, no script needed)

Uses the standard library `compileall` module:
```
python -m compileall -q . -x '/(ml_data|\.project-memory|\.git|venv|\.venv)/'
```

- `-q` suppresses per-file success output, shows only errors.
- `-x` excludes directories that should not be checked (ml_data, project-memory, .git, virtual envs).
- No separate script needed — inline in the GitHub Actions CI workflow.

## 5. Validation Commands

Run these after implementation to verify correctness:

```bash
# 1. Verify YAML syntax of the new workflow itself
python -c "import yaml; yaml.safe_load(open('.github/workflows/validate.yml')); print('validate.yml: OK')"

# 2. Verify YAML syntax of all YAML files
python scripts/validate-yaml.py
echo "Exit code: $?"

# 3. Verify Python syntax of all project files
python -m compileall -q . -x '/(ml_data|\.project-memory|\.git|venv|\.venv)/'
echo "Exit code: $?"

# 4. Verify project-memory structure
bash scripts/check-project-memory.sh
echo "Exit code: $?"

# 5. Verify repository safety
bash scripts/check-repo-safety.sh
echo "Exit code: $?"

# 6. Verify new files are tracked (git add --dry-run shows nothing)
git status --short

# 7. Verify build-and-deploy.yml is NOT modified
git diff .github/workflows/build-and-deploy.yml | head -5
# Should show no output (no changes to existing workflow)

# 8. Verify coder artifact exists
test -f .project-memory/pr/0003-validation-baseline/CODER_REPORT.txt && echo "CODER_REPORT: OK"

# 9. Verify precommit-review artifact exists
test -f .project-memory/pr/0003-validation-baseline/PRECOMMIT_REVIEW.yaml && echo "PRECOMMIT_REVIEW: OK"
```

## 6. CODER_REPORT.txt Requirements

The coder must produce `CODER_REPORT.txt` with these sections:
- `TASK COMPLETE`
- `BLOCKERS`
- `WARNINGS`
- `FILES CHANGED` (list of files created/modified)
- `VALIDATION RUN` (output of all validation commands from section 5)
- `DEVIATIONS FROM PLAN`
- `BOUNDARY CONFIRMATIONS`
- `NEXT REQUIRED ACTION`

## 7. Out of Scope — Later Work

| Work | Reason | Target |
|---|---|---|
| Unit / integration tests | Requires test framework decision, too broad | PR 0004+ |
| Linting (flake8, pylint, mypy) | Requires config decisions, more invasive | PR 0004+ |
| Type checking | Requires type annotations enforcement | PR 0004+ |
| `requirements.txt` changes | PyYAML already present; no new deps needed | N/A |
| Update `build-and-deploy.yml` | Must not change existing pipeline behavior | N/A |
| `app/api.py` credential exposure | Documented in PR 0002, needs dedicated PR | PR 0004+ |
| K8s manifest consolidation | ADR-0002, deferred to later | PR 0005+ |
| ML control enablement | Blocked by ADR-0003 | PR 0006+ |

## 8. Boundary Confirmations

- **Deployment pipeline preserved**: No changes to `build-and-deploy.yml`. The new `validate.yml` workflow is additive — it runs validation only, never builds or pushes to Docker Hub, and never connects to ArgoCD.
- **No runtime behavior changes**: No edits to `run.py`, `app/`, `service/`, `shared_state/`.
- **No ML control enabled**: No ML code is touched.
- **No K8s/ArgoCD restructuring**: No changes to `app/docker/` or any manifest files.
- **No TimescaleDB production changes**: Environment configuration is untouched.
- **No secrets or credentials involved**: The validation workflow does not use any secrets. It does not require Docker Hub credentials.
- **No Docker image publishing changes**: The validate.yml workflow contains no Docker steps.
- **No dependency changes**: PyYAML is already present in `requirements.txt`.
- **Locked artifacts**: PLAN.md and PLAN_REVIEW.yaml are locked after approval. Coder and precommit-review must not edit them.
- **No broad refactors**: Only files created are the new GitHub Actions workflow, YAML validator script, and project-memory checker script.
