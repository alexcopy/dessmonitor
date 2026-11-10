# Adaptive battery model data contract

This worker performs passive, non-actionable analysis. It has no inverter transport
or actuator dependency and does not use these calculations to write settings.

## Telemetry that the repository actually stores

`app/api.py` maps the EASUN labels `Battery Voltage`, `Battery Charging Current`,
`Battery Discharge Current`, `Battery Capacity`, `PV total Power` and
`Output Active Power`. Its display formats label the first five as V, A, %, and W;
those labels do not independently validate the device's sensor semantics. The
`Battery Capacity` field is copied into `battery_soc`, but its accuracy as SOC is
unverified. No battery temperature or chemistry is mapped.

`inverter_metrics` stores the reported values and one timestamp. The collector in
`app/ml/timescale_data_collector.py` writes `datetime.now(UTC)` to that timestamp.
`DeviceData.timestamp` is mapped from the DESS `Timestamp` label, but its meaning
is unverified and it is not persisted in `inverter_metrics`. Therefore all model
times and transitions are collection-time brackets, not verified device times.
Exact repeated payloads in the rolling history and the bounded durable confirmation
signatures are excluded from independent-confirmation counts. Payload changes remain
only a proxy for independent samples because the schema has no device sample
identifier; cached component fields cannot be conclusively identified.

The solar fields contain only the EASUN-visible aggregate. The schema has no
controller identity, array orientation, external-PV, or external-charger-current
fields. Other charging paths can contribute while EASUN reports zero charge. The
model therefore retains `EXTERNAL_CHARGING_POSSIBLE`,
`TOTAL_BATTERY_CURRENT_UNOBSERVED`, and `CHARGING_RELATIONSHIP_UNVERIFIED` as
limitations; an observed voltage rise is not assigned to a particular charger.

PV power is the reported PV-side value; output active power is an AC-output value.
The model never subtracts them to derive battery DC power. Charge/discharge Ah and
Wh values are trapezoidal integrations of reported battery current and voltage over
positive collection intervals no longer than 15 minutes. They are explicitly
reported proxies, not battery capacity, health or validated energy measurements.
The `easun_reported_*` throughput fields clarify that these are measured-channel
integrals only. They do not represent total battery Ah/Wh when other sources are
unmeasured. Simultaneous nonzero EASUN charge/discharge channels are described but
excluded from the simpler interval integration because their semantics are unclear.

Integration is continuous only across consecutive collections. A collection with a
missing current (or voltage, or a negative value, or a battery voltage of 0.0 V or
below) is a continuity barrier: the
interval spanning it is excluded and never bridged by its neighbours, even when
both neighbours report current. Differing payloads at one collection time are
ambiguous and excluded. Battery-voltage validity has one definition,
`app.battery_intervals.valid_battery_voltage` (finite and strictly above 0 V); the
worker, Battery Grid, fast context, multi-source charging context and virtual
threshold replay all use it, so 0.0 V never yields a slope, Ah/Wh coverage or a
sampled boundary crossing (`INVALID_ZERO_VOLTAGE`). The grid still shows the raw
reported value (`battery_voltage_v_reported_raw`, `invalid_fields`) for diagnostics.
The multi-source charging context does not pair collections itself: its slopes,
voltage-rise labels and current-context groups come only from intervals the shared
policy accepted with a slope (>= 60 s), so cached repeats, 10 s collections, missing
or invalid measurements, conflicting channels and gaps over 15 minutes are never
trend evidence there either. The worker reports `integrated_seconds`,
`elapsed_collection_seconds`, `integration_coverage_ratio`, per-reason
`excluded_interval_seconds`, and a `PARTIAL_COVERAGE_*` throughput status whenever
any interval was excluded. `COMPLETE` additionally requires the integrated
collection duration to equal the full elapsed duration; cached repetitions can
never leave a short observed interval labeled complete. Exact consecutive battery
payload repeats are conservatively excluded in **both** worker and grid, including
intervals bordering a cached row. This may undercount true charge or discharge
but does not invent independent observations. Partial coverage is never
presented as a complete observed zero. The historical grid reports Ah/Wh for a derived row only when its whole span
is covered, otherwise null with covered/elapsed seconds and exclusion reasons.
Total battery throughput is always null; only EASUN-reported channels are integrated.

Elapsed time keeps fractional seconds. Voltage slopes are reported only for
intervals of at least 60 s (`MIN_SLOPE_INTERVAL_S`); shorter positive intervals
are labelled `INTERVAL_TOO_SHORT_FOR_SLOPE` and are not counted as independent
voltage evidence. Slopes in both the worker and Battery Grid also require the
shared interval policy to accept the full span; missing channels, cached repeats,
invalid voltage, ambiguous timestamps and gaps over 15 minutes yield no slope.
Identical or out-of-order collection times derive no interval. Grid rows include
covered seconds, elapsed seconds and their coverage ratio for the EASUN channel.

Per-interval voltage/current response ratios are reported only as relative field
proxies; they are not labeled or used as internal-resistance estimates.

Capacity stays `CAPACITY_NOT_IDENTIFIABLE` until trusted SOC anchors, chemistry,
verified current semantics/units, device measurement times and battery temperature
are available. No capacity or health percentage is fabricated.

## Windows and replay

Fast context uses at most six consecutive distinct payloads in the latest 20 minutes.
The three-hour model supports current observational diagnostics. Once per UTC day,
the worker processes at most 2,000 records from the preceding 24 hours (reading one
extra row to detect truncation) and compares the recent three hours with the preceding
21 hours. Other worker evaluations read only the three-hour window. The daily summary
is stored in the existing JSON proposal state and does not reset it.

Virtual threshold pairs use the application's nominal 0.5 V increment, its
application-allowlisted voltage ranges, and a configurable radius of 0–2 increments
around the historical references. Set `ADAPTIVE_OUTPUT_VIRTUAL_RADIUS_STEPS` to
change that radius; the default is 1. These are application rules only. The live
device catalogue, live settings, battery chemistry, manufacturer limits, BMS limits
and real inverter switching logic are not queried or verified. The replay counts
sampled voltage-boundary crossings only. It does not claim predicted switches,
cycling frequency or battery exposure under load. Every pair remains blocked from
recommendation and `NON_ACTIONABLE`.

The operator page exposes a signed, time-keyset-paginated observation API backed by
the unique `inverter_metrics.time` key. Every visible row is derived only from itself
and its own `GRID_CONTEXT_ROWS` (8) immediately older stored rows, whatever the page
size, so a stored observation shows identical derived values at any `limit` or cursor
page. The query fetches the page plus exactly that many older context rows (a second
bounded PK-ordered `LIMIT`, ignoring the range lower bound); context rows are never
rendered. One context row was not enough (a cached repeat needs the predecessor of
the interval's older row) and neither is a fixed `limit + 2` when invalid rows sit
between valid voltages. If no valid previous voltage lies inside the bounded window
the row reports `NO_VALID_VOLTAGE_WITHIN_BOUNDED_CONTEXT` instead of assuming
continuity. Because `time` is the primary key, duplicate collection times cannot come
from the database. The historical grid
marks exact repeated payloads; the behavior grid excludes repeated battery-field
tuples from independent response intervals. A changed payload remains only a proxy
for a new device sample. The existing authenticated decision journal supplies the
virtual-decision grid; no additional per-minute journal or database migration is
introduced.

## State and safety

`online-exploratory-6` accepts proposal/cooldown state written by versions 4 and 5.
The confirmation signature history is bounded to 32 entries and worker re-evaluation
alone does not add confirmation. Confirmation progress belongs to one exact pending
candidate (`HYPOTHETICAL_RAISE|LOWER:<volts>`): a different candidate or direction never
inherits it, a re-evaluation without a new accepted sample that proposes a different
(or no) candidate clears it, and malformed pending state (any version) counts as
zero; well-formed v4/v5 pending state keeps its count. Failure journal entries
carry forward the latest compatible proposal state (including active target and
cooldown), and legacy error entries without state cause recovery from the latest
compatible persisted evaluation. The next successful evaluation may still clear
pending confirmation when actual candidate support disappears. Invalid, naive,
or future `last_change_at` timestamps are never assumed to mean an expired
cooldown: a different candidate remains blocked, with the remaining duration
reported as unknown. No schema migration, threshold change, device call, or
hardware write is part of the worker.

## Decision Log battery voltage presentation

Journal entries carry three fields for the latest collected battery voltage:

- `battery_v` — the valid measurement (finite, strictly above 0 V), otherwise `null`. Never a substituted 0.
- `battery_v_reported_raw` — the value as received, for diagnostics only (NaN/±Infinity stored as text so the entry stays strict JSON).
- `battery_v_status` — `VALID`, `MISSING`, `INVALID_ZERO_VOLTAGE`, `INVALID_NEGATIVE_VOLTAGE`, `INVALID_NON_FINITE_VOLTAGE` or `INVALID_NON_NUMERIC_VOLTAGE` (`app/battery_intervals.py`, same rule as `valid_battery_voltage`).

Older entries only stored the raw value in `battery_v`. The decisions API and export classify those on read (`battery_v_validity_source: DERIVED_ON_READ_FROM_LEGACY_ENTRY`) without rewriting the stored row; new entries report `RECORDED_BY_WORKER`. The page re-applies the same rule, so an invalid value is shown as `Invalid (reported 0.0 V; INVALID_ZERO_VOLTAGE; excluded from calculations)` in the latest evaluation, each history row and the expanded details, and a missing value as `Unavailable`. These fields are presentation only; calculations, reason codes and evaluation identity are unchanged.
