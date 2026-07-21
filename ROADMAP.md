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

The embedded FastAPI/Uvicorn web host is runtime-integrated (PR 0031).
`GET /control/state` is operational and returns live runtime load state when
`WEB_HOST_ENABLED=true`. The endpoint is read-only. No authenticated operator
UI exists. No web write path is permitted. The manual control queue,
arbitration, safety gate, and execution eligibility exist as pure
architectural boundaries but are not yet wired as a complete runtime executor.
Direct web-to-Tuya or web-to-relay calls are forbidden. ML control remains
deferred.

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

The current strategic direction is the **authenticated web operator surface**.
Milestones use sequencing gates only. No calendar dates are invented.

---

### PR 0032 — Canonical Web Operator Surface Roadmap

- **Gate:** starts now. Must merge before any new frontend implementation PLAN.md is accepted.
- **Scope:** Governance documentation only.
- **Outcomes:**
  - Create canonical root `ROADMAP.md`.
  - Resolve active-roadmap location (`.project-memory/ROADMAP.md` → historical only).
  - Define the web operator surface as the current development direction.
  - Preserve completed backend/control work as historical context.
  - Establish sequencing gates for planners and reviewers.
  - No application implementation.

---

### PR 0033 — Authentication Foundation and Login Page

- **Gate:** after PR 0032 is merged.
- **Scope:** Application and GitOps secret provisioning, separately reviewable.
- **Outcomes:**
  - One operator account.
  - `/login` page, `POST /login`, `POST /logout`.
  - Secure authenticated session.
  - Credentials and session secret supplied only through environment variables.
  - Password hash verification.
  - Generic login failure messages.
  - Login throttling.
  - Protected HTML routes and `/api/v1` routes.
  - Protected `GET /control/state`.
  - Minimal non-sensitive `/healthz`.
  - Tests proving unauthenticated API access is denied.
  - No dashboard implementation beyond the minimum authenticated landing shell.
  - No device writes. No hardware access.

---

### PR 0034 — Live Read-Only Dashboard Shell

- **Gate:** after authentication is production-validated.
- **Outcomes:**
  - Authenticated root dashboard.
  - Server-served HTML, CSS, vanilla JavaScript.
  - Responsive desktop and mobile layout.
  - Short polling without page reload.
  - Maximum one outstanding request; request cancellation or timeout.
  - Bounded retry backoff; slower polling when browser tab is hidden.
  - Visible connection states: connecting, online, stale, degraded, offline.
  - Visible last successful refresh time.
  - Normal polling interval ~5s, request timeout shorter than stale threshold,
    stale presentation ~15s without fresh data, offline ~60s without communication.
  - No manual ON/OFF controls. No raw logs. No write API.

---

### PR 0035 — Runtime Heartbeat and Inverter Observability Contract

- **Gate:** after the live dashboard shell is stable.
- **Outcomes:**
  - Structured backend fields for: snapshot_created_at, runtime_heartbeat_at,
    devices_refreshed_at, inverter_refreshed_at, service_started_at.
  - Optional runtime cycle identifier or counter.
  - Inverter read model: operating mode, battery voltage, battery percentage,
    charge current, discharge current, PV-1 voltage and power, PV-2 voltage and
    power, AC input voltage, output load percentage, output power, observed_at,
    warnings.
  - Explicit freshness/status calculation inputs.
  - Dashboard distinguishes HTTP health from runtime health.
  - No parsing of formatted log text as the primary data source.
  - No hardware mutation.

---

### PR 0036 — Device Runtime Duration and Daily Energy Metrics

- **Gate:** after heartbeat timestamps and source freshness are defined.
- **Outcomes:**
  - Extend the stable read model with: currently_on, current_on_seconds,
    day_on_seconds, last_state_changed_at, last_observed_at,
    configured_load_watts, day_energy_kwh, energy_source or energy_is_estimated,
    available, controllable, warnings.
  - Backend computes duration independently of browser session.
  - Browser opening time must not reset device duration.
  - Daily counters have explicit timezone and reset policy.
  - Application restart behavior documented.
  - Measured vs estimated energy are distinguishable.
  - Unavailable or uncomputable values are null, not fabricated zeroes.
  - Sensor fields support: water_temperature_c, ambient_temperature_c,
    humidity_percent, sensor battery percentage, observed_at, warnings.
  - No hardware mutation.

---

### PR 0037 — Complete Read-Only Operator Dashboard

- **Gate:** after the read model for runtime, inverter, and devices is stable.
- **Outcomes:**
  - Operator-friendly inverter summary: mode, battery voltage/percentage,
    charge/discharge current, PV voltage/power, AC input, load percentage,
    output power.
  - Device state table or cards: ON/OFF, continuous ON duration, daily runtime,
    daily energy.
  - Water and ambient temperatures.
  - Explicit unavailable states; freshness badges; runtime heartbeat.
  - Structured warning/event feed (no raw log exposure).
  - Accessible labels and keyboard navigation.
  - No write controls.

---

### PR 0038 — Authenticated Manual Command Queue API and UI

- **Gate:** after the read-only dashboard is production-stable; before any
  hardware execution is enabled.
- **Outcomes:**
  - Authenticated command intent endpoint.
  - CSRF protection.
  - Operator identity in command metadata; request reason; idempotency key.
  - Manual control queue integration.
  - Queue status display.
  - Command states: queued, accepted, rejected, expired, cancelled.
  - UI may request ON/OFF.
  - No direct device execution. No Tuya call. No relay call.
  - No claim that a queued command changed hardware.

---

### PR 0039 — Safety-Gated Controlled Manual Execution

- **Gate:** separate architecture and safety review must approve execution;
  PR 0038 queue behavior must already be stable.
- **Outcomes:**
  - Command proposal/arbitration integration.
  - Safety gate evaluation.
  - Execution eligibility evaluation.
  - Controlled executor boundary.
  - Device command audit result.
  - Idempotency. Timeout and failure behavior.
  - State reconciliation after attempted execution.
  - Explicit protections for pond life-support devices.
  - No bypass route. No ML execution.

---

### PR 0040 — Audited Runtime Configuration Overrides

- **Gate:** after controlled manual commands are safe and auditable.
- **Outcomes:**
  - Temporary operator overrides separate from devices.yaml.
  - Explicit TTL/expiration.
  - Original and overridden values; operator identity; reason; audit state.
  - Conflict rules.
  - Safety-invariant priority.
  - Automatic fallback to declarative baseline.
  - No direct browser editing of devices.yaml.
  - No hidden permanent mutations.

---

### PR 0041+ — Historical Metrics, Events, and Charts

- **Gate:** after current-state semantics are stable.
- **Outcomes:**
  - TimescaleDB-backed history.
  - Battery voltage and state-of-charge charts.
  - Output power charts. PV production charts.
  - Temperature charts. Device runtime history. Daily energy history.
  - Structured event history. Retention and downsampling strategy.
  - Do not make historical charts a blocker for the first useful live dashboard.

---

## Backlog

Items deferred beyond the current web operator surface roadmap. No sequencing
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
- ML advisory display.
- ML control — only after independent safety approval (ADR-0003).
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
