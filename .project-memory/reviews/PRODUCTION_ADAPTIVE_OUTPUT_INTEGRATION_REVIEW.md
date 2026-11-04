# Final Production Compatibility Review — Adaptive Output Integration

Review date: 2026-11-04  
Repository: `alexcopy/dessmonitor`  
Branch: `feat/production-adaptive-output-control`  
Review type: independent review of the local working tree; no remote GitHub state used.

## Verdicts

| Scope | Verdict |
|---|---|
| APPLICATION RELEASE WITH SHADOW DISABLED | **GO** |
| DIAGNOSTIC SHADOW ENABLEMENT | **NO_GO** |
| ACTIVE INVERTER OUTPUT CONTROL | **NOT_READY** |

The application changes can ship with shadow disabled. The diagnostic shadow adapter fails closed, but the actual DESS parsers never populate `device_observed_at`; all live evaluations will therefore have unknown measurement time, zero accepted history, and no useful time-based diagnostic. The smallest correction is to verify and map a documented measurement-time field from the official DESS response, or keep shadow disabled until that contract is confirmed. Do not promote `Timestamp` or web fallback `gts` without that verification.

Active output control remains outside this release. No output-source command mapping was established or reviewed as an active capability.

## Review basis and scope

- Local `master`, local `origin/master`, and `HEAD` all resolve to `c4ea34fc869dea157a16b2df43815586dd7c2c33` (`add allowed css`). `HEAD` has that commit as its parent; `git merge-base HEAD master` is the same base. The reviewed feature changes are currently unstaged/untracked working-tree edits, not commits. The review did not stage, commit, push, open a PR, or deploy.
- The diff against verified local `master` covers the requested paths: `app/api.py`, `app/control/shadow_output_monitor.py`, `app/weather/openweather_service.py`, `service/inverter_monitor.py`, `app/web/static/controls.js`, `app/web/static/controls.css`, `app/web/templates/controls.html`, `tests/test_shadow_output_monitor.py`, and untracked `tests/test_controls_ui.cjs`.
- `git diff --check master` passed. Only this review artifact was added by the review.

## Findings

### F-01 — Diagnostic shadow cannot accumulate production history

`DeviceData.device_observed_at` is introduced as a separate optional field. The official parser continues to populate only legacy `timestamp`; the web fallback continues to derive its display string from `gts`. Neither parser assigns `device_observed_at`. `ShadowOutputMonitor.observe` deliberately reads only that new field and treats legacy timestamp strings as unverified. This is the correct compatibility behavior and prevents synthetic measurement times, but it means every current production sample has unknown observation time and cannot enter history. The evaluator consequently remains blocked for insufficient/unknown telemetry and cannot demonstrate useful sag or anti-flapping history. This is why diagnostic shadow enablement is NO_GO pending verified timestamp provenance. The application release with shadow disabled is unaffected.

### F-02 — No application/charger/persistence regression found in the reviewed diff

- The existing DESS `timestamp` field and its official/fallback parsing are unchanged. The new `device_observed_at` is separate. The inline fixture tests exercise both official response shape (`dat` list with `Timestamp`) and web fallback shape (`dat.gts` plus `pars`) and verify that neither is asserted as device measurement time.
- Existing token refresh, one retry after `DessAuthError`, fallback only when token and secret exist, and fallback parsing are unchanged in `fetch_device_data`. The tested fallback timestamp formatting remains the prior local-time display format.
- `InverterMonitor` captures receipt/evaluation instants only when a shadow monitor exists. With the default-disabled shadow path, no observer call or additional shared-state mutation occurs. When enabled, observer exceptions are caught and rate-limited; normal inverter logging, business metrics and charger-controller tick remain outside that guard.
- The adapter has no DESS or Tuya controller reference and does not call any output-write method. Existing `BatteryChargeController` remains on its prior path. The only DESS write referenced in this code area remains the existing charger-source operation, which is distinct from inverter output-source control.
- Weather changes write one additional `output_shadow_sun_times` shared-state key using UTC Unix instants, and clear it when the current response lacks a complete valid triple. Existing `sunrise_hour`, `sunset_hour`, forecast fields and other weather state assignments are preserved. The shadow adapter checks issue-time freshness, and forecast evidence remains `None`; no forecast is invented.
- TimescaleDB collector timestamps continue to use their own `datetime.now(UTC)` collection time and existing columns. The new telemetry field is not added to shared state, SQL, or the dashboard provider. Existing dashboard timestamps continue to use `DeviceData.timestamp` exactly as before. No change to collection cadence, insert paths, or the existing web timestamp normalization was found.
- Evaluation requires timezone-aware times in the supported year domain. Unsupported, naive, future, duplicate, inconsistent-receipt, and out-of-order device sample times are unknown or rejected before history mutation. Only accepted ordered samples prune/append history. Missing limits, missing forecast, stale input, missing history, and invalid values block candidate decisions; policy retains failed-trial/cooldown lockouts.
- Shadow log additions contain decision, reason, telemetry values, mode and times. They do not log request URLs, tokens, passwords, device IDs, or authentication material. The new shadow exception message logs the exception stack from policy evaluation; the adapter receives telemetry and policy state, not API credentials.

### F-03 — Controls UI request lifecycle is logically guarded; browser rendering remains unverified

- Auto-refresh is scheduled once at 60 seconds after `DOMContentLoaded`, is skipped while the document is hidden, and runs again on visibility restoration. A `loading` guard prevents overlapping requests; the 20-second abort is awaited through `finally`, so it does not release the guard early. Since only one request can be active, an older response cannot overtake a newer request; the request-version check provides an additional commit guard.
- The selected 24h/48h/7d range is stored in session storage, restored, and kept while new 7-day API data is re-rendered. Storage errors are caught.
- Failed HTTP, JSON, schema, or fetch refreshes do not assign response data or render the transition table; the last successful table remains in the DOM, and the UI reports that the prior results remain visible. The simulated DOM test checks the last successful KPI and retry status; it does not model a real Chart.js rendering failure.
- The 10 header cells and dynamic `colSpan=10` agree. The new wrapper uses horizontal overflow and a keyboard-focusable labelled region. Narrow-viewport rendering, actual scroll behavior, chart sizing, browser storage policy, and accessibility need a real-browser check; the Node VM test does not prove visual layout.
- The diff changes no route, authentication handler, CSRF mechanism, or CSP/header setup. The page still uses the authenticated same-origin history GET and same-origin static scripts. Static inspection supports unchanged auth/CSP; this was not verified through a live browser or HTTP response.

### F-04 — Python 3.12 compatibility is manifest-supported but not runtime-proven here

`Dockerfile` targets `python:3.12-slim`. `requirements.txt` declares the runtime third-party packages found along the `run.py` import path, including FastAPI/Uvicorn, `aiohttp`, `asyncpg`, PyYAML, Tuya SDKs, logging packages, Argon2, and form support. The changed Python code adds no third-party dependency. Optional analysis modules that import Matplotlib/Pandas are not imported by the runtime entry path reviewed.

The available host interpreters are Python 3.14.7; Python 3.12 and the declared dependencies are not installed locally. Therefore package resolution/install and imports under production Python 3.12 were not executed, and no Docker build or smoke check is claimed. Run the production-image dependency/install and startup checks before deployment. `zoneinfo` lookup of `Europe/London` is also environment-data dependent in the existing web timestamp path; the Dockerfile does not explicitly install `tzdata`, although lookup failure is caught and falls back to the original timestamp string.

## Validation results

- `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`: **105 tests passed, 0 failed** (Python 3.14.7). Includes the focused shadow tests, DESS official/fallback response-shape fixtures, weather state test, and inverter-loop exception-isolation test.
- `node --test tests/test_controls_ui.cjs`: **4 tests passed, 0 failed** (Node v26.7.0).
- Changed Python modules parsed with `ast.parse`: **4 passed**. `node --check app/web/static/controls.js`: passed.
- `git diff --check master`: passed.
- No browser, Docker, live DESS, Tuya, OpenWeather, or TimescaleDB smoke test was run. No real credentials, devices, or services were contacted.

## Production checks before deployment

1. Deploy with `ADAPTIVE_OUTPUT_SHADOW_ENABLED=false` (the runtime default) for this application release.
2. Run a production-image build/install/startup check under Python 3.12, then verify health and TimescaleDB persistence using the deployment environment.
3. In a browser at desktop and narrow mobile widths, verify the 10-column transition table scrolls within its region, charts remain legible after auto-refresh, the chosen date range stays selected, and authentication/CSP headers are unchanged.
4. Before enabling diagnostic shadow, confirm documented semantics for an actual device measurement timestamp, map it to `device_observed_at`, and check a recorded sample remains ordered and timezone-aware without altering legacy `timestamp` consumers.
5. Keep active inverter output writes disabled. No output-control capability or command was verified.

## Source hashes

SHA-256 of reviewed working-tree contents at review time:

| File | SHA-256 |
|---|---|
| `app/api.py` | `4104656be4bfc77b6d617b0f97f18d6a67a3eb961df462eac1c9278a86afefdb` |
| `app/control/shadow_output_monitor.py` | `a76c3b57b72614373b4a5471ec96882d0cad7331035bedd42895c47d7d6eee7c` |
| `app/weather/openweather_service.py` | `ac6cfe6dfabb63c112f77e05ee4c7f66c04f944b689dbb7dc7921ce990a5cd31` |
| `service/inverter_monitor.py` | `31aee474be4a9d32e2830aabf7cf803dfca77604b7b615d00545f4150901979a` |
| `app/web/static/controls.js` | `54057821f7122d3cd123460fb54dd34fba33bd06782c20d44ee10bc6ac919dee` |
| `app/web/static/controls.css` | `727261df90fee2b7cd88d94006b8d7fc631e7dbb3b8afc342bd57d43739759cf` |
| `app/web/templates/controls.html` | `27e191ac35c4928994c74d6dcc46bdb1772f92e9e5340aedc039116c01abcb58` |
| `tests/test_shadow_output_monitor.py` | `90677336fe3924133ba165a7ba7a698a6b5483d091fcd4bc886c9c5aab67c351` |
| `tests/test_controls_ui.cjs` | `114e504f9000569ece73d4af686f5b38b1c01f991fe2924303b52938bd446513` |
| `Dockerfile` | `9bfdd77c7136685116293a7fd464e8d233206809b43d5bfbaea645f64de89958` |
| `requirements.txt` | `70356e807f902153705d2da5296313425da2a3fec9c0a71bb920357f2010cc1e` |
