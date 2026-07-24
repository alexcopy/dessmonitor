# DESSMONITOR ROADMAP

> **Canonical active roadmap.** This is the single sequencing authority for all
> planners, reviewers, coders, and committers. The file at
> `.project-memory/ROADMAP.md` is retained as historical project context only
> and is not the active roadmap.
>
> Roadmap changes belong to Chief Architect review. No implementation agent may
> modify this file.

---

## Vision

dessmonitor provides a safety-gated, operator-governed energy management
substrate. The platform exposes an authenticated web operator surface for
manual oversight of switchable loads, backed by a pure deterministic control
pipeline. Every hardware action flows through arbitration, safety gates, and
controlled execution eligibility before reaching a device.

The web operator surface is the product. The control model is replaceable.
The substrate — domain types, policy engine, command pipeline, safety gates,
web host, and observability feed — is the enduring platform.

---

## Current State

The authenticated operator dashboard is fully implemented and running.

The repository contains:

- **Authentication:** Argon2 password verification, session management, CSRF
  protection, login/logout routes, protected HTML and JSON routes, login
  throttling, security headers. Session cookie is HttpOnly, SameSite=Lax,
  time-limited.

- **Embedded web host:** FastAPI application with Uvicorn embedded in the
  existing runtime process. Configurable via `WEB_HOST_ENABLED` environment
  variable. Signal handlers suppressed — `run.py` owns all signals.

- **Live read-only dashboard:** Short polling with `fetch()` and
  `AbortController`. Connection-state machine (connecting, online, stale,
  degraded, offline). Visibility-aware polling. Bulma 1.0.4 locally served
  and SHA-256 pinned. Plain JavaScript — no framework. Summary cards for
  total/ON/OFF/UNKNOWN loads. Current Loads table (9 columns). Sensors table
  (6 columns). Startup reset badge. Unavailable state.

- **Canonical device observations:** `DeviceObservationState` with
  `ObservationValue.ON`, `OFF`, `UNKNOWN`. Freshness thresholds: 180s fresh,
  360s stale. Malformed values silently preserve prior state.

- **device_type-authoritative load/sensor separation:** `normalize_device_type()`
  maps aliases (thermometer, watertemp, termo, etc.) to canonical types
  (thermo, switch, pump, multi_switch). `classify_projection_kind()` uses
  ONLY normalized device_type. No name-based classification.

- **Canonical sensor snapshot pipeline:** `SensorReadSnapshot` frozen dataclass
  with 12 safe fields. `TelemetryRegistry` with `register_sensor_descriptor()`,
  `update_water_temperature()`, `get_all_readings()`. Sensors propagate through
  `RuntimeControlSnapshotAdapterInput` → `ControlStateSnapshotInput` →
  `ControlStateSnapshot` → FastAPI serialization → dashboard Sensors table.
  Water temperature renders correctly with freshness badges.

- **Load metadata preservation:** `RuntimeLoadState` and `LoadControlSnapshot`
  carry description, device_type, mapping_status, startup_reset_result, enabled,
  and communication_status through the complete snapshot pipeline.

- **Inverter metrics** exist in `shared_state` (battery voltage/SOC, PV power,
  output power, working mode) but are NOT yet projected into the typed
  `ControlStateSnapshot`. No `InverterReadSnapshot` exists.

- **Signed DESS API URLs** are logged with credentials (token, sign, salt,
  company-key, device identifiers) in `app/api.py` lines 235, 238, 343. This
  is a security concern requiring PR0035.

- **Sensor fallback** uses `run_coroutine_threadsafe().result()` which blocks
  the polling thread. This is a reliability concern requiring PR0046.

- **Telemetry consumers** (`MLDataCollector`, `DeviceStatusLogger`) still read
  `shared_state["water_temp"]` directly instead of `TelemetryRegistry`.
  `DeviceStatusLogger` still uses legacy `ANALOG_TYPES` and name-based
  classification. These are deferred to PR0045.

---

## Architectural Invariants

These are non-negotiable. Every PR and every runtime path must respect them.

### Web Host

| # | Invariant |
|---|-----------|
| W1 | FastAPI is the application web host. |
| W2 | Uvicorn is embedded in the existing runtime process. |
| W3 | Do not introduce Flask for the operator interface. |
| W4 | Do not create a second container or application image for the initial frontend. |
| W5 | Do not change the existing automation entrypoint merely to serve the UI. |

### Initial Frontend

| # | Invariant |
|---|-----------|
| F1 | The initial frontend is server-served HTML, CSS, and vanilla JavaScript. |
| F2 | Do not introduce React, Vue, Angular, Node, npm, Vite, Webpack, or a separate frontend build pipeline. |
| F3 | A framework may only be considered later through a separate ADR after the basic product is operational. |

### Live Update Model

| # | Invariant |
|---|-----------|
| L1 | Initial live updates use short polling. Do not begin with WebSockets or long polling. |
| L2 | Poll requests must never overlap. Maximum one outstanding request. |
| L3 | Polling supports request timeout, retry, exponential or bounded backoff, tab-visibility handling, and explicit stale/offline states. |
| L4 | A successful HTTP response alone does not prove runtime health. |

### Heartbeats and Freshness

| # | Invariant |
|---|-----------|
| H1 | HTTP response time and runtime heartbeat time are separate concepts. |
| H2 | The read model exposes distinct timestamps: snapshot creation, last runtime loop, last device refresh, last inverter refresh. |
| H3 | The frontend derives ONLINE, STALE, DEGRADED, and OFFLINE states from explicit backend timestamps and status fields. |

### Authentication

| # | Invariant |
|---|-----------|
| A1 | Authentication is required before expanding ingress from the single diagnostic endpoint to a full operator interface. |
| A2 | The first implementation supports one operator account. |
| A3 | Credentials and session secrets come only from environment variables populated by Kubernetes Secrets. |
| A4 | No credentials, password hashes, session keys, or secret values may be committed to Git. |
| A5 | Passwords are stored as a strong password hash, preferably Argon2. |
| A6 | Session cookies must be Secure, HttpOnly, SameSite=Lax or stricter, and time-limited. |
| A7 | Do not store authentication tokens in localStorage. Do not use a frontend-visible permanent bearer token. |
| A8 | Do not use HTTP Basic authentication as the primary product login flow. |
| A9 | HTML routes redirect unauthenticated users to /login. JSON API routes return 401. |
| A10 | Login attempts require basic rate limiting or throttling. |
| A11 | Authentication errors must not leak whether a username or password was specifically incorrect. |
| A12 | Logout is an explicit POST operation. CSRF protection is required before write actions are added. |

### Public Health

| # | Invariant |
|---|-----------|
| PH1 | A minimal unauthenticated /healthz endpoint may exist. |
| PH2 | /healthz must disclose no device names, inverter values, configuration, credentials, internal topology, or control state. |
| PH3 | Detailed state endpoints remain authenticated. |

### Read Model

| # | Invariant |
|---|-----------|
| R1 | The web API is a structured read model, not a view of raw internal objects. |
| R2 | Do not expose raw device status dictionaries, raw extra dictionaries, Tuya device identifiers, control keys, API keys, tokens, passwords, or email addresses. |
| R3 | Missing sensor values are represented as null or explicit unavailable state, never silently converted to zero. |
| R4 | API field names must be stable and unambiguous. |

### Logs and Events

| # | Invariant |
|---|-----------|
| EV1 | Do not expose log files directly. Do not implement arbitrary log tailing through the browser. |
| EV2 | Convert useful runtime information into sanitized structured events. |
| EV3 | Structured events include: event_id, created_at, level, component, device_id (where safe), event code, operator-safe message. |
| EV4 | Stack traces, credentials, provider payloads, and sensitive runtime content must not be exposed to the frontend. |

### Device Commands

| # | Invariant |
|---|-----------|
| D1 | No FastAPI route or frontend handler may call Tuya, relay controllers, or hardware directly. |
| D2 | Manual web actions must follow: authenticated request → manual command intent → manual control queue → command proposal/arbitration → command safety gate → execution eligibility → controlled executor → audited result. |
| D3 | Read-only frontend milestones must not accidentally enable execution. |
| D4 | Life-support devices require explicit safety rules before operator OFF commands can execute. |
| D5 | Commands must later support idempotency and audit metadata. |

### Configuration

| # | Invariant |
|---|-----------|
| C1 | devices.yaml remains the declarative baseline. |
| C2 | The frontend must not edit devices.yaml directly. |
| C3 | Runtime configuration changes are modeled as separate operator overrides. |
| C4 | Overrides include: override_id, target device, field, original value, requested value, operator identity, reason, creation time, expiration time/TTL, status. |
| C5 | Safety invariants have higher priority than operator overrides. |
| C6 | Expired overrides automatically fall back to devices.yaml values. |
| C7 | Persistent configuration changes remain a GitOps workflow, not an untracked browser mutation. |

### Safety

| # | Invariant |
|---|-----------|
| S1 | Existing automation behavior must remain unchanged until a dedicated runtime write/execution PR is approved. |
| S2 | Manual queue support alone does not authorize hardware execution. |
| S3 | Controlled execution remains behind explicit safety review. |
| S4 | ML control remains deferred (ADR-0003). |
| S5 | No frontend milestone may weaken pond life-support protections. |
| S6 | No frontend milestone may bypass command arbitration or safety gates. |

### Deployment

| # | Invariant |
|---|-----------|
| DP1 | Application image changes and GitOps deployment changes remain separate, reviewable changes where practical. |
| DP2 | Production deployment uses an immutable image reference or digest. |
| DP3 | Ingress, Service, TLS, and application behavior must not be mixed into an unrelated frontend feature PR. |
| DP4 | Authentication secrets are provisioned through the external GitOps repository, never added to the public application repository. |

---

## Current Milestones

The current strategic direction is the **health-first operator surface**.
Milestones use sequencing gates only. No calendar dates are invented.

The authenticated dashboard, canonical sensor pipeline, and device-type-based
load/sensor separation are complete. The next fifteen PRs build the complete
read-only operator surface with route-backed tabs, inverter observability,
system health overview, and TimescaleDB-backed history.

---

### PR0035 — Redact Signed DESS Request Diagnostics

- **Gate:** None (independent security fix).
- **Root cause:** `app/api.py` logs and prints signed URLs containing token,
  sign, salt, company-key, pn/sn/device parameters to stdout (line 238) and
  info logger (lines 235, 343). These credentials would be exposed in log
  aggregation and container stdout.
- **Outcome:** All signed DESS URLs are redacted before logging/printing. Only
  safe diagnostic tokens (e.g., `action=queryDeviceLastData`) remain legible.
- **Main files:** `app/api.py`
- **Out of scope:** Tuya credential handling. ML collector logging.
- **Acceptance:** grep for `sign=` and `token=` in log output returns zero
  matches under normal logging levels. Application functions correctly (no
  regression).
- **Security:** Prevents DESS API credential leakage through logs and stdout.

---

### PR0036 — Runtime Heartbeat and Source Freshness Contract

- **Gate:** None (independent contract layer).
- **Root cause:** The `runtime_state` dict in `build_runtime_read_model` has no
  freshness metadata — callers cannot tell when each data source last produced
  a valid response (device observation, sensor telemetry, inverter API).
- **Outcome:** `runtime_state` gains `snapshot_created_at` (existing),
  `runtime_heartbeat_at`, `devices_refreshed_at`, `inverter_refreshed_at`.
  Sources declare their freshness independently. No source declares freshness
  from another source's response.
- **Main files:** `app/web_runtime_integration.py`, `run.py` (adds timestamp
  capture), `ControlStateSnapshot` may gain source_freshness fields.
- **Out of scope:** Inverter read model. Sensor read model. Device status
  changes.
- **Acceptance:** `runtime_state` dict contains four distinct timestamps. A
  test can set each to a different value and verify all four survive to the
  endpoint snapshot.
- **Security:** No new exposure. Timestamps are safe ISO strings.

---

### PR0037 — Typed Inverter Observability Snapshot

- **Gate:** PR0036 complete.
- **Root cause:** Inverter metrics (battery_voltage, battery_soc, output_power,
  pv_total_power, working_mode, etc.) exist only in `shared_state`. No typed
  snapshot carries them through the control pipeline to the dashboard.
- **Outcome:** `InverterReadSnapshot` frozen dataclass with safe fields
  (battery voltage/SOC/currents, PV voltages/powers, output
  voltage/power/apparent, AC input, load %, working mode, inverter
  `observed_at`). Written by `InverterMonitor` into
  `runtime_state["inverter"]`. Read by the snapshot adapter.
- **Main files:** `app/control/control_state_snapshot.py`
  (`InverterReadSnapshot`), `service/inverter_monitor.py` (writes typed dict
  downstream), `app/web_runtime_integration.py` (passes inverter into
  `runtime_state`), `app/control/runtime_snapshot_adapter.py` (`_parse_inverter`
  helper), `app/web_control_state_provider.py` (reads
  `runtime_state["inverter"]`).
- **Out of scope:** Dashboard rendering. Route-backed tabs. History/charts.
- **Acceptance:** `runtime_state["inverter"]` with typed dict →
  `ControlStateSnapshot` contains inverter field with correct values. Missing
  inverter data produces defaults, not errors. Inverter field survives JSON
  serialization.
- **Security:** No Tuya IDs or property codes. Only safe numeric/string
  metrics.

---

### PR0038 — Canonical Inverter and Heartbeat Web Pipeline

- **Gate:** PR0036 and PR0037 complete.
- **Root cause:** Inverter metrics and freshness have typed contracts
  (PR0036+0037) but no web pipeline carries them to the dashboard. The
  dashboard cannot display inverter state or source freshness.
- **Outcome:** The complete pipeline from `InverterMonitor` → `runtime_state` →
  parse → snapshot → endpoint → dashboard is wired and tested.
- **Main files:** All files from PR0036+0037 plus `run.py` wiring, dashboard
  rendering changes to display inverter summary and freshness badges in the
  existing single-page layout (before route-backed tabs are added).
- **Out of scope:** Route-backed tabs. Device-level changes. Sensor changes.
- **Acceptance:** Dashboard renders inverter summary with battery voltage, SOC,
  output power, working mode, and source freshness badges.
- **Security:** No new exposure.

---

### PR0039 — Route-Backed Bulma Tabs Shell

- **Gate:** PR0038 complete.
- **Root cause:** The single-page layout mixes system summary, device table,
  and sensor table on one scroll surface. Operators need route-backed tab
  navigation to focus on one domain at a time without losing scroll position.
- **Outcome:** Six route-backed tabs: Overview, Energy, Devices, Sensors,
  System, History. Each tab is a GET route returning a Jinja template fragment
  or full page with the same navbar. Active tab is highlighted. Tab switch
  does not reload the full page — uses `fetch()` and DOM replacement.
  JavaScript handles polling lifecycle per visible tab. URL path changes with
  tab selection.
- **Main files:** `app/web_routes.py` (new GET routes for tabs), `index.html`
  (becomes tab shell with navbar and content container),
  `app/web/static/dashboard.js` (tab switching, per-tab polling control).
- **Out of scope:** Full content for every tab. Energy detail page. Device
  detail. System diagnostics. History charts.
- **Acceptance:** Six tabs render in navbar. Clicking a tab changes the URL
  path and replaces the content area without full page reload. Active tab is
  highlighted. Polling continues across tab switches.
- **Security:** All routes are authenticated. No write methods.

---

### PR0040 — System Health Overview

- **Gate:** PR0038 and PR0039 complete.
- **Root cause:** The Overview tab exists as a route shell (PR0039) but the
  content is still the old single-page layout. It has not been redesigned as a
  health-first summary.
- **Outcome:** Overview tab shows: runtime heartbeat, inverter freshness,
  device observation freshness per device, sensor freshness, battery
  voltage/SOC, charge/discharge current, output power and load %, PV total
  power, AC input state, water temperature, concise actionable warnings.
  Active-first device subset shown. No full device inventory.
- **Main files:** `app/web/templates/index.html` (Overview section),
  `dashboard.js` (polling/adapter for Overview), `dashboard.css` (health cards
  layout).
- **Out of scope:** Energy detail tab. Devices tab. History tab. Charts.
- **Acceptance:** Overview displays inverter state, source freshness, counts,
  active loads, and warnings. Full inventory is not in Overview.
- **Security:** No new exposure.

---

### PR0041 — Energy Detail Tab

- **Gate:** PR0039 complete.
- **Root cause:** The Energy domain (inverter working mode, battery, PV, AC,
  output power detail, measured vs estimated vs derived consumption) has no
  dedicated page with Bulma cards and levels.
- **Outcome:** Energy tab shows full inverter detail: working mode badge,
  battery voltage/SOC/charge/discharge currents, PV1/PV2 voltage and power,
  output voltage/active power/apparent power, AC input voltage/frequency, AC
  output load %. Measured output power, estimated managed-device load, and
  derived unattributed consumption are explicitly distinguished by labels and
  visual hierarchy.
- **Main files:** `app/web/templates/energy.html` (new), `dashboard.js` (energy
  tab rendering), `dashboard.css` (energy-specific cards).
- **Out of scope:** Historical charts. Sensor health. Device commands.
- **Acceptance:** Energy tab renders all inverter fields with correct units.
  Three power types (measured, managed, derived) visibly separated.
- **Security:** No new exposure.

---

### PR0042 — Active / Problems / All Devices Tab

- **Gate:** PR0039 complete.
- **Root cause:** The current device table renders all devices regardless of
  state. Operators need an active-first view and a problems view.
- **Outcome:** Devices tab with three sub-views: Active (confirmed ON first,
  sorted by freshness), Problems (stale/unhealthy/UNKNOWN devices), All
  (complete sorted table). Active devices visible before OFF/unavailable.
- **Main files:** `app/web/templates/devices.html` (new), `dashboard.js`
  (device filtering/sorting), existing load fields used as-is.
- **Out of scope:** Device commands. Device history. Configuration changes.
- **Acceptance:** Active tab shows only confirmed-ON devices sorted by
  freshness. Problems tab shows stale/unhealthy/UNKNOWN devices. All tab
  matches current load table. Counts shown per view.
- **Security:** No new exposure. No write methods.

---

### PR0043 — Sensor Health Tab

- **Gate:** PR0039 complete.
- **Root cause:** Sensors are currently rendered below the device table on one
  page. With tab navigation, Sensors get their own full page.
- **Outcome:** Sensors tab renders the existing `SensorReadSnapshot` table in a
  dedicated view. Adds ambient temperature and humidity if available across the
  snapshot pipeline.
- **Main files:** `app/web/templates/sensors.html` (new), `dashboard.js`
  (sensor tab rendering).
- **Out of scope:** Sensor configuration. Sensor history. Device state.
- **Acceptance:** Sensors tab renders all registered sensor descriptors with
  freshness badges, N/A for unavailable values.
- **Security:** No new exposure.

---

### PR0044 — System Diagnostics and Structured Warnings

- **Gate:** PR0038 and PR0039 complete.
- **Root cause:** Startup reset status, polling quarantine counts, and warnings
  are rendered inline below summary cards. No dedicated system tab for
  structured diagnostics.
- **Outcome:** System tab shows startup reset status and gate, per-device reset
  results, polling quarantine and backoff state, runtime uptime, source
  freshness summary, and structured warning feed (not raw logs).
- **Main files:** `app/web/templates/system.html` (new), `runtime_state` gains
  `warning_events` field (list of safe structured warnings), `dashboard.js`
  (system tab rendering), `dashboard.css`.
- **Out of scope:** Log files. Raw log display. Alerting. Notification.
- **Acceptance:** System tab displays structured runtime health. No raw logs or
  exception text exposed.
- **Security:** No log content or exception text reaches the frontend.

---

### PR0045 — Canonical Telemetry Consumers

- **Gate:** PR0034j and PR0034h complete (already done).
- **Root cause:** `MLDataCollector` and `DeviceStatusLogger` still read
  `shared_state["water_temp"]` directly instead of `TelemetryRegistry`.
  `DeviceStatusLogger` still uses the legacy `ANALOG_TYPES` set and name-based
  classification instead of `normalize_device_type` and
  `classify_projection_kind`.
- **Outcome:** All telemetry consumers read from the canonical
  `TelemetryRegistry` and use `device_type`-based classification.
  `MLDataCollector` reads `telemetry_registry.get_all_readings()`.
  `DeviceStatusLogger` uses `classify_projection_kind()`. `ANALOG_TYPES`
  removed from `device_status_logger.py`.
- **Main files:** `app/ml/ml_data_collector.py`,
  `app/monitoring/device_status_logger.py`.
- **Out of scope:** `shared_state` removal. Inverter data migration.
- **Acceptance:** `MLDataCollector` produces identical data reading from
  `TelemetryRegistry` vs `shared_state`. `DeviceStatusLogger` correctly
  classifies thermo devices using canonical types instead of `ANALOG_TYPES`.
- **Security:** No new exposure.

---

### PR0046 — Async Sensor Fallback Hardening

- **Gate:** PR0034h complete (already done).
- **Root cause:** The individual-sensor-fallback path in
  `status_updater_async.py` uses `run_coroutine_threadsafe().result()` with a
  timeout — this blocks the polling thread when the fallback RPC hangs.
  Additionally, the fallback is triggered unconditionally for any batch with
  missing telemetry, creating chatty extra RPCs.
- **Outcome:** Fallback uses a bounded semaphore, runs fully async within the
  existing polling coroutine, and only fires when a sensor parent has returned
  empty status for N consecutive cycles (configurable threshold).
- **Main files:** `app/tuya/status_updater_async.py`.
- **Out of scope:** Permission-denied isolation, quarantine, retry backoff.
- **Acceptance:** Fallback is async, non-blocking, and rate-limited. Existing
  isolation behavior unchanged.
- **Security:** No new exposure.

---

### PR0047 — Device Runtime and Daily Energy Metrics

- **Gate:** PR0042 complete.
- **Root cause:** Device daily runtime and energy are computed in-device
  (`tick()` on `RelayChannelDevice`) but not exposed through the typed
  snapshot. The dashboard cannot display per-device runtime or daily energy.
- **Outcome:** `RuntimeLoadState` and `LoadControlSnapshot` gain
  `today_run_seconds` and `today_kwh` fields. Dashboard Devices tab renders
  daily runtime and energy.
- **Main files:** `app/control/runtime_snapshot_adapter.py`,
  `control_state_snapshot.py`, `web_runtime_integration.py`, `dashboard.js`.
- **Out of scope:** Historical energy. TimescaleDB energy tracking.
- **Acceptance:** Dashboard shows per-device daily runtime and kWh. Values
  update each polling cycle. Unavailable devices show null.
- **Security:** No new exposure.

---

### PR0048 — TimescaleDB History Foundation and Read API

- **Gate:** PR0038 complete (stable live read semantics).
- **Root cause:** `TimescaleDataCollector` writes data but there is no read
  API, no query contract, and no bounded ingestion boundary.
- **Outcome:** TimescaleDB ingestion is bounded: only inverter metrics and
  device states with explicit schema. A read-only query API is added under
  `/control/history` with query parameters: `metric_name`, `from_ts`, `to_ts`,
  `aggregation` (raw/avg/min/max), `resolution`. Auth-protected. Paginated.
- **Main files:** `app/web_host.py` (new query route),
  `app/control/history_query.py` (query contract types and safe SQL
  generation), `run.py` (wiring). Existing `TimescaleDataCollector` is not
  modified.
- **Out of scope:** Historical chart rendering in the browser. Downsampling
  implementation. Retention policy configuration.
- **Acceptance:** `GET /control/history?metric=battery_voltage` returns a typed
  JSON array of `{timestamp, value}` within the requested window. Empty window
  returns empty array. Malformed parameters return 400.
- **Security:** Read-only, auth-protected. SQL injection prevented by typed
  parameterized queries. No schema exposure.

---

### PR0049 — Historical Charts, Retention and Downsampling

- **Gate:** PR0048 complete.
- **Root cause:** TimescaleDB has data but no browser charts, no retention
  policy, and no downsampling.
- **Outcome:** History tab renders time-series charts using Canvas or SVG (not
  a third-party charting library). Chart types: battery voltage, state of
  charge, output power, PV production, temperatures, device daily energy.
  Retention policy configurable via environment variable. Downsampling for
  queries spanning >7 days.
- **Main files:** `app/web/static/history.js` (new), history tab template,
  `dashboard.js` (history tab integration). `TimescaleDataCollector` may gain
  retention config.
- **Out of scope:** Advanced chart types. Export. Comparison overlays.
- **Acceptance:** History tab renders at least `battery_voltage` chart for a
  24h window. Data older than retention window is not returned. Downsampling
  reduces points for >7d queries.
- **Security:** No new exposure.

---

## Deferred Work

Items preserved after the fifteen-PR sequence (PR0035-PR0049). No sequencing
commitment is made within this block.

### Manual Command Queue API and UI

- Authenticated command intent endpoint.
- CSRF protection.
- Operator identity in command metadata.
- Manual control queue integration.
- Queue status display.
- No direct device execution.

### Safety-Gated Controlled Manual Execution

- Command proposal/arbitration integration.
- Safety gate evaluation.
- Execution eligibility evaluation.
- Controlled executor boundary.
- Pond life-support protections.

### Audited Runtime Configuration Overrides

- Temporary operator overrides with TTL.
- Original vs overridden values.
- Safety-invariant priority.
- Automatic fallback to declarative baseline.

### ML Advisory Display

- Read ML data as advisory overlay.
- No control authority.

### ML Control

- Requires independent safety approval per ADR-0003.
- Deferred beyond all fifteen PRs and the three execution blocks above.

### Additional Backlog

Items deferred beyond the current operator surface roadmap. No sequencing
commitment is made.

- Evaluate Server-Sent Events after short polling is proven.
- Evaluate a frontend framework only if vanilla JavaScript becomes a proven
  maintenance constraint.
- Multi-user authentication and roles.
- Password reset and account management.
- External identity provider.
- WebAuthn or multi-factor authentication.
- Persistent audit database.
- Notification delivery.
- Historical reports (beyond basic charts).
- Advanced mobile layout. PWA/offline support. User-defined dashboards.
- Safe editing of persistent configuration through a future GitOps workflow.
- ArgoCD and GitOps cleanup (consolidate manifests, remove :latest tag,
  Kustomize/Helm overlays, extract ArgoCD Application CRD).
- CI/CD pipeline improvements (multi-arch Docker builds, automated
  lint/type-check/test gates).
- Credential rotation procedure.
- Monitoring and alerting for control actions.
- Infrastructure hardening (health checks, graceful degradation, circuit
  breakers for external dependencies).

---

## Historical Context

Completed milestones preserved for traceability. These are NOT current work.

### Repository Governance (Phase 0)

| PR | Title | Status |
|----|-------|--------|
| 0001 | Repository safety and memory bootstrap | ✅ |
| 0002 | Runtime critical fixes | ✅ |
| 0003 | Validation baseline | ✅ |
| 0004 | Fix legacy YAML baseline | ✅ |
| 0005 | Runtime smoke validation | ✅ |
| 0006 | Image publishing boundary | ✅ |

### Platform Control Redesign (Phase 2b)

| PR | Title | Status |
|----|-------|--------|
| 0007 | Platform control redesign strategy | ✅ |
| 0008 | Disable pump automation, preserve manual switch control | ✅ |
| 0009 | Introduce generic control domain types | ✅ |
| 0010 | Map relay channel to switchable load | ✅ |
| 0011 | Energy-aware control policy requirements | ✅ |
| 0012 | Energy policy domain types | ✅ |
| 0013 | Static energy policy config example | ✅ |
| 0014 | Readiness evaluator (pure function) | ✅ |
| 0015 | Health evaluator (pure function) | ✅ |
| 0016 | Schedule profile model | ✅ |
| 0017 | Weather adjustment evaluator | ✅ |
| 0018A | Policy engine operating boundaries | ✅ |
| 0018B | Passive policy engine models | ✅ |
| 0018C | Pure deterministic policy decision engine | ✅ |
| 0018D | Policy decision scenario matrix tests | ✅ |
| 0019 | Manual control queue boundary | ✅ |
| 0020 | Command intent and proposal arbitration | ✅ |
| 0021 | Command safety gate model | ✅ |
| 0022 | Controlled execution eligibility model | ✅ |
| 0023 | Runtime read-only control state snapshot | ✅ |
| 0024 | Runtime read-only control snapshot adapter | ✅ |
| 0025 | Web UI read-only control state API contract | ✅ |
| 0026 | Web UI read-only control state endpoint plan | ✅ |
| 0027 | Web UI read-only control state endpoint | ✅ |
| 0028b | Bootstrap read-only FastAPI web host | ✅ |
| 0029 | Runtime read-only control state provider | ✅ |
| 0030 | Runtime read-only web host startup | ✅ |
| 0031 | Integrate read-only web host into runtime | ✅ |

### Operator Surface Foundation (Phase 3)

| PR | Title | Status |
|----|-------|--------|
| 0032 | Canonical web operator surface roadmap | ✅ |
| 0033 | Authentication foundation and login page | ✅ |
| 0034 | Live read-only dashboard shell | ✅ |
| 0034a | Canonical device observation state | ✅ |
| 0034b | Plain-text agent artifact standard | ✅ |
| 0034c | Explicit Tuya device property mapping | ✅ |
| 0034d | Import-safe Tuya runtime configuration | ✅ |
| 0034e | Resilient Tuya status polling | ✅ |
| 0034f | Sensor telemetry projection | ✅ |
| 0034g | Separate load and sensor projection | ✅ |
| 0034h | Bootstrap thermo sensors | ✅ |
| 0034j | Canonical sensor snapshot pipeline | ✅ |

**ML control is not production-ready.** ADR-0003 requires safety policy,
shadow/advisory mode, and fallback mechanism before any ML model may operate
relays or make control decisions.

---

## Planner and Reviewer Discipline

- Planners must plan only the first incomplete Current Milestone.
- A planner may not combine two milestone PRs without an explicit roadmap amendment.
- `plan-review` must block a PLAN.md that skips an incomplete milestone.
- `plan-review` must block implementation before required preceding gates.
- `precommit-review` must verify implementation stays within the approved milestone.
- Frontend-only work must not be blocked merely for being frontend work when the
  current roadmap milestone explicitly requires executable frontend behavior.
- Documentation-only output is valid only for PR 0032 and other milestones
  explicitly marked governance-only.
- No implementation agent may modify ROADMAP.md.
- Roadmap changes belong to Chief Architect review.
- Runtime execution/write milestones require independent safety review.
- Each PR must retain executable-first validation appropriate to its scope.
- The precommit anti-committee rule must not incorrectly reject frontend
  implementation when the approved current milestone requires a working
  frontend. However, a frontend mockup without executable behavior, route
  integration, tests, or API contract compliance must still be blocked.
