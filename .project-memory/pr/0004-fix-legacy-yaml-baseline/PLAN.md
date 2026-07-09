# PR 0004 — Fix Legacy YAML Baseline

## 1. Precondition Results

| Check | Command | Output |
|---|---|---|
| HEAD | `git rev-parse --verify HEAD` | `cfcf9785474e042b5c6cf7393c557c6f9ba1932e` |
| Branch | `git branch --show-current` | `0004-fix-legacy-yaml-baseline` |
| Working tree | `git status --short` | clean (no local changes) |

The precondition passes. Branch is `0004-fix-legacy-yaml-baseline` and working tree is clean.

## 2. Why PR 0004 Exists

### 2.1 Current Deployment Pipeline

The current production deployment pipeline is:

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

PR 0004 does NOT change this pipeline. The active deployment manifest is `app/docker/dessmonitor-deploy.yaml`. The file being fixed (`app/docker/all_in_one/deployment.yaml`) is a historical/example manifest not used by ArgoCD.

### 2.2 Validation Discovery

PR 0003 added a YAML syntax validation script (`scripts/validate-yaml.py`) and a GitHub Actions CI workflow (`validate.yml`). Running the validator against the current repository reveals a **pre-existing YAML syntax error** in `app/docker/all_in_one/deployment.yaml`. This file was never validated before, so the error was silently tolerated.

PR 0004's sole purpose is to fix this single YAML syntax error so that `python scripts/validate-yaml.py` exits with code 0. No runtime behavior, deployment pipeline, or functional content is changed.

## 3. Exact YAML Validation Failure

### Command Executed

```bash
python scripts/validate-yaml.py
```

### Output

```
ERROR: app/docker/all_in_one/deployment.yaml: line 17: mapping values are not allowed here

❌ 1 error(s) in 22 file(s) checked.
EXIT CODE: 1
```

All other 21 YAML files validate successfully.

### Error Analysis

**File:** `app/docker/all_in_one/deployment.yaml`

**Root cause:** Lines 17-24 use **semicolons (`;`)** to separate multiple YAML key-value pairs on a single line. In YAML, a semicolon is not a key-value separator — it is interpreted as plain scalar content. When PyYAML encounters `name: logs   ; mountPath: /app/logs`, the parser sees `name: logs` as a valid mapping key-value pair, but then encounters `; mountPath: /app/logs` as unexpected content (because YAML does not allow a value to start with `;` after a mapping value). The error "mapping values are not allowed here" originates on line 17 but the actual structural problem is the semicolon syntax used throughout lines 17-24.

**Affected lines:**

| Line | Current (invalid) | Must be (valid) |
|---|---|---|
| 17 | `- name: logs   ; mountPath: /app/logs` | Separate lines for each key-value pair |
| 18 | `- name: cache  ; mountPath: /app/app/cache` | Same |
| 19 | `- name: cfg    ; mountPath: /app/devices.yaml ; subPath: devices.yaml ; readOnly: true` | Same (3 key-value pairs) |
| 20 | `- name: secret ; mountPath: /run/secrets/config.json ; subPath: config.json ; readOnly: true` | Same (3 key-value pairs) |
| 22 | `- name: logs   ; persistentVolumeClaim: {claimName: dess-logs-pvc}` | Same |
| 23 | `- name: cache  ; persistentVolumeClaim: {claimName: dess-cache-pvc}` | Same |
| 24 | `- name: cfg    ; configMap: {name: dess-config}` | Same |

The **YAML block sequence** entries on these lines must be split into properly indented multiline mappings. A semicolon-separated line like:

```yaml
- name: logs   ; mountPath: /app/logs
```

Must become:

```yaml
- name: logs
  mountPath: /app/logs
```

### Distinction: YAML Syntax vs. Kubernetes Behavior

This is a **pure YAML syntax correction**. The fix does NOT change:
- Image name (`ghcr.io/you/dessmonitor:latest` remains as-is).
- Namespace, labels, ports, resource limits, replicas.
- Volume names, mount paths, subPaths, PV claims, ConfigMaps, Secrets.
- Service, Ingress, or any other K8s resource definition.
- This manifest is **not applied by ArgoCD** — the active deployment is `app/docker/dessmonitor-deploy.yaml`, which references `redcopy/dessmonitor:latest`. The `all_in_one/deployment.yaml` file appears to be a historical/example manifest.

## 4. PR 0004 Implementation Scope

### 4.1 Must Fix

Edit only `app/docker/all_in_one/deployment.yaml` to correct the YAML syntax error. The fix is:
1. Remove semicolons from lines 17-20 (volumeMounts) and lines 22-24 (volumes).
2. Split each semicolon-separated line into properly indented multiline YAML.
3. Preserve all original key names, values, and order.
4. Maintain the same indentation level as the original file.

### 4.2 Must Not Fix

- **Do not rename** the file or directory.
- **Do not move** the file to another location.
- **Do not change** image names, tags, namespaces, labels, ports, resource limits, or any other K8s field values.
- **Do not change** the active `app/docker/dessmonitor-deploy.yaml`.
- **Do not delete** the `all_in_one` directory.
- **Do not edit** other files in `app/docker/all_in_one/`.
- **Do not edit** `scripts/validate-yaml.py` — the validator is correct; the legacy YAML is invalid.
- **Do not edit** `scripts/check-repo-safety.sh`, `.github/workflows/validate.yml`, or any other script.

### 4.3 Files Coder May Edit

| File | Change Description |
|---|---|
| `app/docker/all_in_one/deployment.yaml` | Fix YAML syntax: replace semicolon-separated inline mappings with proper multiline block sequence entries |

### 4.4 Files Coder Must Not Edit

All files not listed in 4.3 above, **including but not limited to**:
- `app/docker/dessmonitor-deploy.yaml` and all other files in `app/docker/`
- `app/docker/all_in_one/` files other than `deployment.yaml`
- `.github/workflows/build-and-deploy.yml` and `.github/workflows/validate.yml`
- `scripts/validate-yaml.py`, `scripts/check-repo-safety.sh`, `scripts/check-project-memory.sh`
- `run.py` and all files under `app/`, `service/`, `shared_state/`
- `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.gitignore`
- `init-db.sql`, `requirements.txt`
- `.project-memory/` standards, ADRs, ROADMAP.md, CURRENT_STATE.md
- `.project-memory/pr/0004-fix-legacy-yaml-baseline/PLAN.md` (locked)
- `.project-memory/pr/0004-fix-legacy-yaml-baseline/PLAN_REVIEW.yaml` (locked)

### 4.5 Artifact Layout

```
.project-memory/pr/0004-fix-legacy-yaml-baseline/
    PLAN.md                    ← This file (planning agent, LOCKED)
    PLAN_REVIEW.yaml           ← Plan-review agent (LOCKED after approval)
    CODER_REPORT.txt           ← Coder agent implementation report
    PRECOMMIT_REVIEW.yaml      ← Precommit-review agent
```

### 4.6 Agent Workflow

| Step | Agent | Artifact | Constraint |
|---|---|---|---|
| 1 | plan | `PLAN.md` | Writes plan |
| 2 | plan-review | `PLAN_REVIEW.yaml` | Reviews PLAN.md only. PLAN.md and PLAN_REVIEW.yaml are LOCKED after approval |
| 3 | coder | `CODER_REPORT.txt` | Implements approved plan. Must NOT edit PLAN.md or PLAN_REVIEW.yaml |
| 4 | precommit-review | `PRECOMMIT_REVIEW.yaml` | Reviews final diff + validation. Must NOT edit PLAN.md or PLAN_REVIEW.yaml |

## 5. Detailed Fix Specification

### Current Content (`app/docker/all_in_one/deployment.yaml`)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: dessmonitor
spec:
  replicas: 1
  selector:
    matchLabels: {app: dessmonitor}
  template:
    metadata:
      labels: {app: dessmonitor}
    spec:
      containers:
      - name: dessmonitor
        image: ghcr.io/you/dessmonitor:latest   # или «dessmonitor:latest», если push в локальный registry
        volumeMounts:
        - name: logs   ; mountPath: /app/logs
        - name: cache  ; mountPath: /app/app/cache
        - name: cfg    ; mountPath: /app/devices.yaml ; subPath: devices.yaml ; readOnly: true
        - name: secret ; mountPath: /run/secrets/config.json ; subPath: config.json ; readOnly: true
      volumes:
      - name: logs   ; persistentVolumeClaim: {claimName: dess-logs-pvc}
      - name: cache  ; persistentVolumeClaim: {claimName: dess-cache-pvc}
      - name: cfg    ; configMap: {name: dess-config}
      - name: secret ; secret: {secretName: dess-secret}
```

### Required Fix

The semicolons must be removed and each value pair must be on its own line. The corrected file should look like:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: dessmonitor
spec:
  replicas: 1
  selector:
    matchLabels: {app: dessmonitor}
  template:
    metadata:
      labels: {app: dessmonitor}
    spec:
      containers:
      - name: dessmonitor
        image: ghcr.io/you/dessmonitor:latest   # или «dessmonitor:latest», если push в локальный registry
        volumeMounts:
        - name: logs
          mountPath: /app/logs
        - name: cache
          mountPath: /app/app/cache
        - name: cfg
          mountPath: /app/devices.yaml
          subPath: devices.yaml
          readOnly: true
        - name: secret
          mountPath: /run/secrets/config.json
          subPath: config.json
          readOnly: true
      volumes:
      - name: logs
        persistentVolumeClaim:
          claimName: dess-logs-pvc
      - name: cache
        persistentVolumeClaim:
          claimName: dess-cache-pvc
      - name: cfg
        configMap:
          name: dess-config
      - name: secret
        secret:
          secretName: dess-secret
```

**What changed:**
- Lines 17-20 (volumeMounts): The `;` separator is removed, each key-value pair is on its own line, indented one level deeper than the `- name:` line.
- Lines 22-25 (volumes): Same approach. For the `persistentVolumeClaim:` values, the original used flow-style `{claimName: ...}` which is valid YAML and is preserved. Same for `configMap: {name: ...}` and `secret: {secretName: ...}`. However, the `;` must still be removed and each volume entry split into separate lines.

**What did NOT change:**
- All key names and string values are identical.
- The image tag (`ghcr.io/you/dessmonitor:latest`) is unchanged.
- All other file content is identical.
- The file is still a single-document Kubernetes Deployment manifest.

## 6. Validation Commands

Run these after implementation to verify correctness:

```bash
# 1. Verify YAML validation passes
python scripts/validate-yaml.py
echo "Exit code: $?"

# 2. Verify the specific file is valid YAML
python -c "
import yaml, sys
with open('app/docker/all_in_one/deployment.yaml') as f:
    docs = list(yaml.safe_load_all(f))
print(f'{len(docs)} document(s) parsed')
for i, doc in enumerate(docs):
    if doc is not None:
        print(f'  Doc {i+1}: kind={doc.get(\"kind\",\"?\")}, name={doc.get(\"metadata\",{}).get(\"name\",\"?\")}')
"

# 3. Verify only the target file changed
git diff --name-only
# Should show: app/docker/all_in_one/deployment.yaml

# 4. Verify locked artifacts unchanged
git diff --name-only HEAD -- .project-memory/pr/0004-fix-legacy-yaml-baseline/PLAN.md
# Should produce no output

git diff --name-only HEAD -- .project-memory/pr/0004-fix-legacy-yaml-baseline/PLAN_REVIEW.yaml
# Should produce no output

# 5. Verify Python syntax still valid
python -m compileall -q . -x '/(ml_data|\.project-memory|\.git|venv|\.venv)/'
echo "Exit code: $?"

# 6. Verify repository safety still passes
bash scripts/check-repo-safety.sh
echo "Exit code: $?"

# 7. Verify project-memory structure
bash scripts/check-project-memory.sh
echo "Exit code: $?"

# 8. Verify coder artifact exists
test -f .project-memory/pr/0004-fix-legacy-yaml-baseline/CODER_REPORT.txt && echo "CODER_REPORT: OK"

# 9. Verify deploy.yaml is NOT modified
git diff --name-only HEAD -- app/docker/dessmonitor-deploy.yaml
# Should produce no output — active deployment manifest untouched

# 10. Verify build-and-deploy workflow is NOT modified
git diff --name-only HEAD -- .github/workflows/build-and-deploy.yml
# Should produce no output
```

## 7. CODER_REPORT.txt Requirements

The coder must produce `CODER_REPORT.txt` with these sections:
- `TASK COMPLETE`
- `BLOCKERS`
- `WARNINGS`
- `FILES CHANGED` (list of files modified)
- `VALIDATION RUN` (output of all validation commands from section 6)
- `DEVIATIONS FROM PLAN`
- `BOUNDARY CONFIRMATIONS`
- `NEXT REQUIRED ACTION`

## 8. Out of Scope — Later Work

| Work | Reason | Target |
|---|---|---|
| Other files in `app/docker/all_in_one/` | No validation errors found for those files | N/A |
| `app/docker/` manifest consolidation | ADR-0002 identifies this for later | PR 0005+ |
| Active deployment manifest cleanup | `dessmonitor-deploy.yaml` is the active file, not modified | N/A |
| ArgoCD restructuring | Blocked by roadmap ordering | PR 0005+ |
| ML control enablement | Blocked by ADR-0003 | PR 0006+ |
| Unit/integration tests | Too broad for this PR | PR 0006+ |

## 9. Boundary Confirmations

- **YAML syntax only**: The fix converts invalid YAML semicolon syntax to valid YAML block sequence notation. No Kubernetes behavior, runtime logic, or deployment intent is changed.
- **Deployment pipeline preserved**: `build-and-deploy.yml`, `validate.yml`, Dockerfile, docker-compose.yml are all untouched. ArgoCD continues to use `app/docker/dessmonitor-deploy.yaml` unchanged.
- **No runtime behavior changes**: No edits to `run.py`, `app/`, `service/`, `shared_state/`.
- **No ML control enabled**: ML code is untouched.
- **No Docker image publishing changes**: No changes to CI workflows or Docker configuration.
- **No K8s behavior changes**: The `all_in_one/deployment.yaml` manifest is a historical/example file not used by ArgoCD (the active manifest is `app/docker/dessmonitor-deploy.yaml`). Even if it were active, the YAML fix preserves all key-value pairs semantically — only the syntax notation changes.
- **Locked artifacts**: PLAN.md and PLAN_REVIEW.yaml are locked after approval.
- **Validator is correct, legacy file is wrong**: The validator correctly identified invalid YAML. No changes to `scripts/validate-yaml.py`.
