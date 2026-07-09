# PR 0006 — Image Publishing Boundary

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `8b88342fe56ed97bbeb6422166aaf3ef7c866432` |
| Branch | `git branch --show-current` | `0006-image-publishing-boundary` |
| Working tree | `git status --short` | clean (no local changes) |

The precondition passes. Branch is `0006-image-publishing-boundary` and working tree is clean.

## 2. Bug in Existing Documentation

The existing project-memory documents (ADR-0002, CURRENT_STATE.md) incorrectly claim that ArgoCD deploys using GitOps manifests from `app/docker/` within **this** repository. The operator has clarified that this is wrong.

**Corrected deployment boundary:**

- **This repository** owns: application source code, validation scripts, Dockerfile, Docker build input, and Docker image publishing to Docker Hub.
- **A separate external repository** owns: the real ArgoCD Application manifests, Kubernetes deployment manifests, GitOps configuration, and the deployed application state.
- The `app/docker/` files in this repository are **legacy, auxiliary, or non-authoritative**. They must not be treated as production truth or used as the basis for ArgoCD changes.

This misunderstanding was never caught because previous PRs (1-5) correctly avoided modifying `app/docker/` files. However, the documentation has been inaccurate since PR 0001.

## 3. What This Repository Owns (Image Publishing)

Evidence in this repository for image publishing:

| Artifact | File | What It Proves |
|---|---|---|
| Docker Hub image repository | `.github/workflows/build-and-deploy.yml` line 7 | `IMAGE_NAME: redcopy/dessmonitor` |
| Docker Hub credentials | `.github/workflows/build-and-deploy.yml` lines 28-31 | `docker/login-action` with `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` secrets |
| Image tags | `.github/workflows/build-and-deploy.yml` lines 35-36 | Tags: `latest` and `${{ github.sha }}` |
| Trigger on master | `.github/workflows/build-and-deploy.yml` line 4 | `push: branches: [ master ]` |
| Docker build definition | `Dockerfile` | Application Docker image definition |
| Multi-arch build support | `.github/workflows/build-and-deploy.yml` line 16 | `docker/setup-qemu-action@v3` |

## 4. What Cannot Be Proven From This Repository

The following are **not** owned or defined in this repository (they live in the external GitOps repository):

| Item | Why It Cannot Be Proven Here |
|---|---|
| Real ArgoCD Application CRDs | Not present in this repository |
| Real Kubernetes Deployment manifests | `app/docker/` files are legacy/non-authoritative |
| Real Service, Ingress, or Namespace definitions | `app/docker/` files are legacy/non-authoritative |
| ArgoCD sync policy, destination cluster, or revision | External GitOps repo is authoritative |
| Real deployed image tag | Requires querying the Kubernetes cluster, which is out of scope |
| Real namespace `dess` configuration | `app/docker/dessmonitor-deploy.yaml` defines it locally but the real source is external |

## 5. PR 0006 Implementation Scope

### 5.1 Must Create

| File | Description |
|---|---|
| `.project-memory/DEPLOYMENT_BOUNDARY.md` | Document the corrected deployment boundary. States: this repo builds and publishes Docker images; the real ArgoCD/GitOps source of truth is external; `app/docker/` files are non-authoritative. |
| `scripts/check-image-publishing-boundary.sh` | Static validation script that checks: `.project-memory/DEPLOYMENT_BOUNDARY.md` exists and contains key phrases: "Docker Hub", "external GitOps repository", "ArgoCD", "non-authoritative". Exits 0 if boundary is documented correctly, 1 if not. |

### 5.2 Must Edit

| File | Change |
|---|---|
| `.project-memory/CURRENT_STATE.md` | Correct the "Deployment Pipeline" section (item 3) to state that the real ArgoCD/GitOps source of truth is in a separate external repository, and that `app/docker/` files in this repository are legacy/non-authoritative. |
| `.github/workflows/validate.yml` | Add an additive step `bash scripts/check-image-publishing-boundary.sh` after the project-memory structure check. The workflow remains validation-only with no Docker build/push, no secrets, no deploy steps. |

### 5.3 Must Not Edit

All other files, **including but not limited to**:
- `run.py` and all files under `app/`, `service/`, `shared_state/`
- `.github/workflows/build-and-deploy.yml`
- `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.gitignore`
- `app/docker/*` (all files — even if they contain incorrect info, leave them untouched)
- `init-db.sql`, `requirements.txt`
- `scripts/check-repo-safety.sh`, `scripts/check-project-memory.sh`, `scripts/validate-yaml.py`, `scripts/check-runtime-smoke.py`
- `.project-memory/ADR/ADR-0002-dockerhub-argocd-deployment.md` (superseded by DEPLOYMENT_BOUNDARY.md but left intact for audit trail)
- `.project-memory/pr/0006-image-publishing-boundary/PLAN.md` (locked)
- `.project-memory/pr/0006-image-publishing-boundary/PLAN_REVIEW.yaml` (locked)

### 5.4 Artifact Layout

```
.project-memory/pr/0006-image-publishing-boundary/
    PLAN.md                    ← This file (planning agent, LOCKED)
    PLAN_REVIEW.yaml           ← Plan-review agent (LOCKED after approval)
    CODER_REPORT.txt           ← Coder agent implementation report
    PRECOMMIT_REVIEW.yaml      ← Precommit-review agent
```

### 5.5 Agent Workflow

| Step | Agent | Artifact | Constraint |
|---|---|---|---|
| 1 | plan | `PLAN.md` | Writes plan |
| 2 | plan-review | `PLAN_REVIEW.yaml` | Reviews PLAN.md only. PLAN.md and PLAN_REVIEW.yaml are LOCKED after approval |
| 3 | coder | `CODER_REPORT.txt` | Implements approved plan. Must NOT edit PLAN.md or PLAN_REVIEW.yaml |
| 4 | precommit-review | `PRECOMMIT_REVIEW.yaml` | Reviews final diff + validation. Must NOT edit PLAN.md or PLAN_REVIEW.yaml |

## 6. Detailed Specifications

### 6.1 DEPLOYMENT_BOUNDARY.md Content Requirements

The file must contain these statements:

1. **This repository owns:** application source code, validation, Docker build input, and Docker image publishing to Docker Hub (`redcopy/dessmonitor`).
2. **This repository does NOT own:** the real ArgoCD Applications or real Kubernetes deployment manifests. ArgoCD/GitOps source of truth is in a separate external repository.
3. **`app/docker/` files in this repository are legacy, auxiliary, or non-authoritative.** They must not be treated as production truth. Agents must not modify ArgoCD assumptions from this repository's local manifests.
4. **Image publishing behavior** (`.github/workflows/build-and-deploy.yml`) must remain stable unless explicitly planned and reviewed.
5. **ML control** remains disabled until a later safety-reviewed PR.

### 6.2 Image Publishing Boundary Script (`scripts/check-image-publishing-boundary.sh`)

**Requirements:**
- Bash script, consistent style with existing `scripts/check-project-memory.sh`.
- No network access, no Docker Hub queries, no GitHub API calls, no Kubernetes queries, no ArgoCD queries.
- No secrets required. Does not read secret values.
- Does not mutate files. Read-only.
- Checks:
  1. `.project-memory/DEPLOYMENT_BOUNDARY.md` exists.
  2. File contains the string "Docker Hub".
  3. File contains the string "external GitOps repository".
  4. File contains the string "ArgoCD".
  5. File contains the string "non-authoritative".
- Prints actionable error messages for each missing check.
- Exit code 0 if all checks pass, 1 if any check fails.

### 6.3 CURRENT_STATE.md Update

Replace the current "Deployment Pipeline" section in `CURRENT_STATE.md`:

**Current (incorrect):**
```
3. **ArgoCD** deploys from Docker Hub using GitOps manifests in `app/docker/dessmonitor-deploy.yaml`
```

**Corrected:**
```
3. **ArgoCD** deploys from Docker Hub. The real ArgoCD/GitOps source of truth is in a separate
   external repository. The `app/docker/` files in this repository are legacy, auxiliary, or
   non-authoritative — they are NOT the real GitOps source of truth.
```

### 6.4 validate.yml Update

Add one step after the existing project-memory check:

```yaml
      - name: 🔍 Image publishing boundary check
        run: bash scripts/check-image-publishing-boundary.sh
```

The final workflow will be:

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

      - name: 🔍 Runtime smoke validation
        run: python scripts/check-runtime-smoke.py

      - name: 🔍 Image publishing boundary check
        run: bash scripts/check-image-publishing-boundary.sh
```

## 7. Validation Commands

Run these after implementation to verify correctness:

```bash
# 1. Verify boundary document exists
test -f .project-memory/DEPLOYMENT_BOUNDARY.md && echo "EXISTS" || echo "MISSING"

# 2. Run the boundary check script
bash scripts/check-image-publishing-boundary.sh
echo "Exit code: $?"

# 3. Verify compileall still passes
python -m compileall -q . -x '/(ml_data|\.project-memory|\.git|venv|\.venv)/'
echo "Exit code: $?"

# 4. Verify YAML validation still passes
python scripts/validate-yaml.py
echo "Exit code: $?"

# 5. Verify repository safety still passes
bash scripts/check-repo-safety.sh
echo "Exit code: $?"

# 6. Verify project-memory structure
bash scripts/check-project-memory.sh
echo "Exit code: $?"

# 7. Verify runtime smoke validation (skipped locally if deps missing, but verify script exists)
test -f scripts/check-runtime-smoke.py && echo "SMOKE_SCRIPT: EXISTS"

# 8. Verify only allowed files changed
git diff --name-only
# Should only show:
#   .project-memory/DEPLOYMENT_BOUNDARY.md
#   scripts/check-image-publishing-boundary.sh
#   .project-memory/CURRENT_STATE.md
#   .github/workflows/validate.yml

# 9. Verify locked artifacts unchanged
git diff --name-only HEAD -- .project-memory/pr/0006-image-publishing-boundary/PLAN.md
# Should produce no output

git diff --name-only HEAD -- .project-memory/pr/0006-image-publishing-boundary/PLAN_REVIEW.yaml
# Should produce no output

# 10. Verify build-and-deploy workflow is NOT modified
git diff --name-only HEAD -- .github/workflows/build-and-deploy.yml
# Should produce no output

# 11. Verify app/docker files are NOT modified
git diff --name-only HEAD -- app/docker/
# Should produce no output

# 12. Verify validate.yml has no docker steps
grep -c "docker" .github/workflows/validate.yml
# Should output: 0  (the word "docker" should not appear in validate.yml)

# 13. Verify coder artifact exists
test -f .project-memory/pr/0006-image-publishing-boundary/CODER_REPORT.txt && echo "CODER_REPORT: OK"
```

## 8. CODER_REPORT.txt Requirements

The coder must produce `CODER_REPORT.txt` with these sections:
- `TASK COMPLETE`
- `BLOCKERS`
- `WARNINGS`
- `FILES CHANGED` (list of files created/modified)
- `VALIDATION RUN` (output of all validation commands from section 7)
- `DEVIATIONS FROM PLAN`
- `BOUNDARY CONFIRMATIONS`
- `NEXT REQUIRED ACTION`

## 9. Out of Scope — Later Work

| Work | Reason | Target |
|---|---|---|
| ADR-0002 update | Superseded by DEPLOYMENT_BOUNDARY.md but left intact for audit trail | N/A |
| `app/docker/` file cleanup | Not in scope — these are legacy files that should not be modified | Future PR |
| External GitOps repo changes | This repo does not own that repository | N/A |
| Immutable image tags | Requires Docker Hub publishing change — blocked by boundary stability | Future PR |
| ArgoCD restructuring | The real ArgoCD lives in the external repo | N/A |
| ML control enablement | Blocked by ADR-0003 | PR 0008+ |

## 10. Boundary Confirmations

- **No runtime behavior changes**: No edits to `run.py`, `app/`, `service/`, `shared_state/`.
- **No Docker image publishing behavior changes**: `build-and-deploy.yml` is untouched.
- **No ArgoCD behavior changes**: This repo does not own the real ArgoCD manifests. No edits to `app/docker/`.
- **No Kubernetes behavior changes**: `app/docker/` files are not modified. Their non-authoritative status is documented but the files themselves are left as-is.
- **No ML control enabled**: ML code is untouched.
- **No dependency changes**: `requirements.txt` unchanged.
- **No secrets or credentials**: The boundary check script is static text validation only.
- **No file mutation**: All scripts are read-only.
- **Locked artifacts**: PLAN.md and PLAN_REVIEW.yaml are locked after approval.
- **Existing validation ADR-0002 is outdated but preserved**: The corrected boundary is documented in the new DEPLOYMENT_BOUNDARY.md. ADR-0002 is left as-is for audit trail — it may be superseded in a later PR.
