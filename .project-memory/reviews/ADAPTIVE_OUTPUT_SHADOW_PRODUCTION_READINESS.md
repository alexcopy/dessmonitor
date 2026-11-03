# Production Shadow Integration — Independent Readiness Review

Review date: 2026-11-03
Repository: `/Users/alexred/Projects/Python/dessmonitor`
Branch: `feat/adaptive-output-control`
HEAD: `5a6b9fb`
Review scope: local working tree only; no production connection, restart, deployment, commit, or push.

## Files inspected

Implementation and tests:

- `run.py` (uncommitted; shadow startup gate and optional monitor construction, lines 135–155)
- `service/inverter_monitor.py` (uncommitted; monitor loop isolation, lines 24–60)
- `app/control/shadow_output_monitor.py` (untracked; adapter, input mapping, history, logging)
- `tests/test_shadow_output_monitor.py` (untracked; six focused tests)
- `app/control/adaptive_output_policy.py` (approved evaluator; freshness, safety and decision gates)
- `app/control/output_policy_config.py` (approved loader)
- `app/api.py` (DESS telemetry parsing and `DeviceData` contract)
- `app/weather/openweather_service.py` and controller consumers (weather state contract)
- `devices.yaml`, `Dockerfile`, `requirements.txt`, `docker-compose.yml`, `app/docker/dessmonitor-deploy.yaml`, and `.github/workflows/build-and-deploy.yml`

No source or test files were changed during this review. The only file created for this review is this artifact.

## Runtime integration and isolation

`ADAPTIVE_OUTPUT_SHADOW_ENABLED` defaults to the string `false`; only `1`, `true`, `yes`, or `on` enables construction (`run.py:135–154`). When disabled, `shadow_monitor` remains `None`; the inverter monitor calls it only when non-None (`service/inverter_monitor.py:48`). Enabling/disabling therefore needs an environment setting and process restart, not a source edit.

When enabled, configuration import/load and adapter construction are inside startup exception handling (`run.py:137–153`). An absent or invalid policy falls back to an adapter with unknown limits. Each observation is invoked after the existing SolarAwareController and charge-controller ticks, in a worker thread, under its own exception handler (`service/inverter_monitor.py:31–55`). This path does not pass a DESS/Tuya client, device manager, relay, or actuator to the adapter. The adapter imports only the pure evaluator and has no hardware calls (`app/control/shadow_output_monitor.py:8, 23–27`). No code in the shadow call changes the existing controller inputs or outputs. This supports source-level isolation; the test also exercises a raising shadow observer while confirming the charge-controller tick still ran (`tests/test_shadow_output_monitor.py:85–118`).

Shadow decision logging is capped at one record per 300 seconds by monotonic time (`app/control/shadow_output_monitor.py:52–62`); monitor-level exception logging has a separate 300-second cap (`service/inverter_monitor.py:48–55`). The logged fields are decision, reason codes, quality, timestamp, inverter/PV/battery measurements, observed mode, lockout expiry, and history length. The adapter logs no configuration contents or credentials. The bounded deque has `maxlen=12000`; process restart creates a new empty adapter (`app/control/shadow_output_monitor.py:26–31`).

The main loop retains the existing fetch, metrics, SolarAwareController, and charge-controller paths regardless of the feature flag. No adaptive actuator method is called by the adapter or monitor integration. This is a read-only shadow integration, not autonomous output-source switching.

## Timestamp provenance and freshness

`DeviceData.timestamp` is an optional string (`app/api.py:26–30`). The primary DESS parser copies its timestamp text without assigning timezone information (`app/api.py:585–601`). The web fallback receives epoch milliseconds, converts them through `time.localtime`, and stores a naive `YYYY-MM-DD HH:MM:SS` string (`app/api.py:526–537`).

The shadow adapter accepts only timezone-aware datetimes or strings with an explicit offset; it returns `None` for naive or malformed timestamps and does not substitute acquisition time (`app/control/shadow_output_monitor.py:11–20, 33–34`). `evaluated_at` is supplied explicitly by `datetime.now(timezone.utc)` in the monitor (`service/inverter_monitor.py:50`). The evaluator separately checks sample age (180-second default) and staleness/gaps (`adaptive_output_policy.py:654–685`). It does not receive `received_at`, so transport delay is not independently measured.

The normal web-fallback timestamp path cannot currently create verified shadow history: its local-time timestamp is intentionally rejected. Primary API timestamp timezone semantics and actual production timestamp format were not verified, so the possibility of timezone-aware primary timestamps is not evidence that production history accumulates. Restart begins empty and no synthetic history is added. Strictly increasing, non-future samples only are appended; duplicates/out-of-order samples are not promoted (`shadow_output_monitor.py:44–51`).

The runtime boundary does not enforce a supported snapshot date range before subtracting the eight-day retention window (`shadow_output_monitor.py:44–46`). A read-only probe with an aware year-0001 timestamp raises `OverflowError: date value out of range` during cutoff calculation. The inverter loop catches evaluator exceptions, so this does not escape that loop, but the boundary defect can cause repeated failed evaluations (rate-limited in logs) instead of a conservative decision. The date-domain behavior must be corrected before enablement.

## Telemetry and solar inputs

The adapter maps `working_state`, `output_power`, `pv_total_power`, `battery_voltage`, `battery_charging_current`, and `battery_discharging_current` directly from `DeviceData` (`shadow_output_monitor.py:35–40`). DESS field mappings include `Working State`, `Output Active Power`, `PV total Power`, battery voltage, and charge/discharge current (`app/api.py:154–170`). No Tuya total is used. `None` values remain `None`; there is no zero substitution. Total measured inverter output is therefore represented by DESS active output power, but whether that field is actually supplied and valid in Line Mode in production was not verified.

The adapter always supplies `sunrise=None`, `sunset=None`, and `forecast=None` (`shadow_output_monitor.py:41–43`). The current weather service stores integer sunrise/sunset hours (`app/weather/openweather_service.py:124–135, 158–165`); those values are not minute-precision observations and are not used by the adapter. The adapter has no forecast observation mapping or freshness timestamp. Missing forecast does not become favorable.

The actual local `devices.yaml` loads as `UnconfiguredOutputPolicy`. Thus no approved output-policy limits or thresholds are currently available from this file, and the adapter uses the conservative default baseline with unknown safety limits. The evaluator requires fresh forecast plus an eligible solar classification to consider battery operation; with the adapter’s permanently unknown sun/forecast inputs, and the unconfigured baseline, a legitimate `CONSIDER` cannot be produced through this integration. BLOCK/unknown reasons and measured fields can still be logged, but the resulting stream is functionally limited and cannot validate the intended positive decision path.

## History, polling, and warmup

The actual inverter monitor polls every 60 seconds (`run.py:154`), independent of Timescale collection intervals. The adapter uses only live `DeviceData`; it does not read Timescale Grid Mode records. Its retention is hard-bounded at 12,000 samples and prunes older than eight days based on sample time (`shadow_output_monitor.py:29–30, 44–51`). The policy/configuration duration bounds are intended to keep required history within that horizon. However, sample timestamps are generated by the device/API and the actual cadence/continuity of distinct observations was not established. The evaluator requires continuity and rejects stale gaps; repeated timestamps or gaps do not create synthetic samples.

The adapter evaluates before appending the current sample, so a restarted process starts with insufficient history. Missing warmup and telemetry remain conservative. Future observations are not appended; however, pruning is performed before the future-time rejection, so a far-future sample can evict existing history. This is fail-closed for decisions but unnecessarily destroys valid warmup history and should be corrected as part of a follow-up before enablement.

## Docker and deployment findings

- `Dockerfile:1–13` uses `python:3.12-slim` and installs `requirements.txt`; it copies the source, then starts `python run.py` (`Dockerfile:15–22`). `requirements.txt` declares the modules missing in the local environment, including `aiohttp`, `colorama`, and `concurrent-log-handler`.
- `docker-compose.yml` also describes `redcopy/dessmonitor:latest`, builds from the local Dockerfile, and mounts `devices.yaml` read-only (`docker-compose.yml:3–16`). It is not evidence that Compose is the production route.
- `.github/workflows/build-and-deploy.yml` runs validation on `master` push or manual dispatch, installs requirements, runs `scripts/check-runtime-smoke.py`, builds and pushes `latest` plus a commit-SHA image, then clones `alexcopy/dess-argo`, updates the GitOps deployment image tag, commits, and pushes (`.github/workflows/build-and-deploy.yml:3–7, 13–28, 30–73`). It references GitHub secrets `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`, and `ARGO_DEPLOY_KEY`; their availability was not checked and their values were not accessed.
- The local Kubernetes manifest has `redcopy/dessmonitor:latest`, read-only configuration mounts and process/log-file readiness probes (`app/docker/dessmonitor-deploy.yaml:43–89`). The workflow’s GitOps source is a separate repository and is not present in this checkout. The live Argo reconciliation state, actual production image reference, environment injection, credentials, and release/rollback procedure are therefore inferred from repository configuration, not verified.
- Neither local Compose nor the checked-in Kubernetes manifest declares `ADAPTIVE_OUTPUT_SHADOW_ENABLED`. Its default keeps this feature disabled in those manifests. How to inject the flag into the actual production workload is not verified.
- The local smoke script was run and failed to import five modules because the local Python environment lacks installed requirements. Those modules are declared in `requirements.txt`, and the Docker build installs that file, but no equivalent Docker build/container smoke test was run. A production-image runtime smoke test is therefore NOT VERIFIED, not a pass.
- The CI readiness check is the `python3 scripts/check-runtime-smoke.py` step. The manifest checks the `run.py` process and `/app/logs/loki.log`; a Loki sidecar tails that log in the local Kubernetes manifest. No live log, health, or startup verification was performed.

No deployment credentials or platform access were requested or used. A previous image reference cannot be established from this local repository state. A rollback by restoring the previous image tag in the GitOps deployment and allowing its reconciler to apply it is a reasonable inference from the workflow, but the previous tag, reconciliation behavior, and operator procedure are not verified; it is not presented as an executable production rollback instruction.

## Offline verification

- Full offline suite: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py'` — **94 passed, 0 failed**.
- The suite includes 70 Phase 1A tests, Phase 1B configuration tests, and six shadow integration tests.
- Focused probes (simulated, local only): timezone-aware timestamp accepted; naive timestamp unknown; stale timestamp classified stale; future timestamp unknown and not retained; missing Line Mode output remains unknown; restart starts with empty history and BLOCK; repeated decision logs are throttled to 2 records across 5 evaluations. All passed.
- Actual `devices.yaml` loader probe — `UnconfiguredOutputPolicy`.
- Runtime date-boundary probe — aware year-0001 timestamp raises `OverflowError` in adapter retention calculation.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/check-runtime-smoke.py` — **failed**: missing local `concurrent_log_handler`, `aiohttp`, and `colorama` (and an asyncpg warning). Docker installs declared requirements, but equivalent container validation was not run.
- No production or hardware actions were made. No adaptive actuator calls are present in the inspected integration.

## Required corrections and deployment prerequisites

Before enabling shadow observation, the smallest concrete corrections are:

1. At the runtime boundary, reject timestamps outside a supported datetime domain without raising; only prune after confirming a usable, non-future timestamp. Preserve the evaluator’s freshness rules. Avoid evicting valid history for a future sample.
2. Establish and pass genuinely timezone-aware DESS observation timestamps (and, if available, acquisition time separately). Until then, fallback/naive timestamps must remain unknown; do not assign a guessed timezone.
3. Supply an operator-approved `output_source_policy` in the deployed configuration and verified minute-precision sunrise/sunset plus timestamped forecast observations, or explicitly deploy knowing shadow can only emit conservative blocked/unknown results. Current `devices.yaml` and current adapter do not supply them.
4. Verify the production image build/startup, actual GitOps image reference and reconciler, the environment-variable injection method, required CI secrets/access, and a rollback to a known previous image before any release operation.

The code-only disable switch is `ADAPTIVE_OUTPUT_SHADOW_ENABLED=false` (also the default); changing an already-running process requires a restart, which was not performed. To enable, the process environment must contain an accepted true value and the workload must be restarted through the verified release procedure. Verify the startup `[OUTPUT_SHADOW] enabled; observe only` record followed by rate-limited `[OUTPUT_SHADOW] decision=...` records and the existing process/log health checks. Confirm no commands by source review and the integration’s adapter-only dependencies; no production command trace was available. For rollback, restore the known previous image reference through the verified GitOps procedure; that procedure is not currently verified, so deployment must wait for that operational verification.

## Release verdicts

**RELEASE WITH SHADOW DISABLED: NOT_VERIFIED.** The local code keeps the feature disabled by default and the integration is source-level isolated, but the actual production deployment route, current image, GitOps reconciliation, and rollback procedure are not verified. Smallest prerequisite: verify those operational facts and the previous image before releasing any code.

**ENABLE SHADOW IN PRODUCTION: NO_GO.** The fallback timestamp path is naive and cannot build verified history; actual primary timestamp semantics are unknown; the supported date domain is not enforced at the adapter boundary; the deployed policy is unconfigured; and minute-precision solar/forecast inputs are not connected. Smallest prerequisite: address items 1–3 above and demonstrate trusted history and expected conservative/eligible outcomes with verified production telemetry before setting the enable flag.

## Summary status

| Check | Verdict | Basis |
|---|---|---|
| Runtime isolation | PASS (source-level; disabled default) | Optional monitor, isolated try/except, no actuator reference in adapter |
| Timestamp handling | FAIL | Naive fallback cannot accumulate verified history; year-domain probe overflows |
| Telemetry mapping | PASS (mapping only) | Direct actual inverter fields; missing values retained as unknown; production Line Mode availability unverified |
| History and warmup | FAIL (production evidence) | Bounded/reset/fail-closed, but genuine timestamp continuity is not established |
| No hardware writes | PASS (source-level) | No DESS/Tuya write path or actuator object in shadow adapter |
| Docker runtime | NOT_VERIFIED | Dependencies declared and installed by Dockerfile; equivalent image smoke not run |
| Deployment and rollback | NOT_VERIFIED | GitHub/GitOps flow inferred; live state, access, previous tag, reconciliation and rollback unverified |
