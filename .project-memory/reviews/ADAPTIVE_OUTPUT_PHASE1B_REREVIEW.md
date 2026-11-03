# Independent Phase 1B re-review — configuration safety

## Review metadata and verdict

- Review date: 2026-11-03; repository `/Users/alexred/Projects/Python/dessmonitor`; branch `feat/adaptive-output-control`; HEAD `1155d0776c74ae7705541fee8997f12d9a56bb87`, two commits ahead of upstream.
- Phase 1B loader SHA-256 `9a1cf55f279324abb63d0d818784e7691a7234ac5c66ecba32901dceb69d31a4`; tests SHA-256 `9c5678a3201533d48300410cda7908257c6769104c33350de849a3e060c8adca`. Both match the requested hashes. Both files are untracked and were read from the working tree.
- Phase 1A evaluator SHA-256 `e671810fe66649032039c8cfb290d1e6431b077e7620199d233d6de50dbb8d25`; tests SHA-256 `fc63e662a2355a975307a41555e3ca9f7cca085622747c6836a9ebebf152420c`. Both match the approved Phase 1A acceptance record; `git diff HEAD` shows no changes to them, the device initializer or devices.yaml.
- Read the previous Phase 1B review, updated loader and tests, and relevant Phase 1A and architecture contract. No code, tests, configuration or prior review was changed.

**Verdict: APPROVED.** The five findings were corrected for the stated contracts, and the independent checks found no unresolved blocker or major defect. Approval is for the offline, shadow-only configuration layer, within the explicitly declared snapshot-date domain. It is not hardware approval or authorization for active control.

## Correction verification matrix

| Brief ID / prior review ID | Result | Evidence |
|---|---|---|
| B-01 / B-01 duplicate YAML | PASS | Composed YAML nodes are inspected before `safe_load` constructs mappings; duplicate policy/root keys and policy/root merges are rejected, including through aliases. Safe YAML parsing remains in use. |
| B-02 / M-01 public constructors | PASS | Exact nested model types and mode/version/write flags validated; each nested model validates primitives and bounds. Mutation and direct-construction probes rejected invalid inputs. |
| B-03 / M-02 partial voltage | PASS | Known infeasible exit/enter/maximum/hysteresis combinations rejected; partial and unknown feasible values remain accepted. |
| B-04 / M-03 duration overflow | PASS within stated domain | All 13 YAML minute fields accept at most 10,080 minutes. Simultaneous maximums project correctly; independent boundary-date sag, escalation and churn evaluations completed without overflow. |
| B-05 / M-04 timezone purity | PASS | Pure parser checks syntax without ZoneInfo or file access; file loader resolves non-null names; `timezone_verified` remains false. |

## B-01: duplicate keys, aliases, merges and safe parsing

`app/control/output_policy_config.py:292–325` walks composed nodes before `yaml.safe_load` at `:328–336`. Independent temporary-file probes produced the following `OutputPolicyConfigError` results, rather than silently choosing a value:

- Different duplicate discharge limits (5 and 75), null/numeric duplicate charge limits, and repeated identical charge limits: rejected at the exact baseline field path.
- Duplicate root `output_source_policy`: rejected at the root policy path. Duplicate nested sunrise offset: rejected at its nested path.
- Duplicate limit hidden inside an aliased mapping defined outside the policy: rejected at the policy baseline field path. A benign non-merge mapping alias was accepted.
- Root merge, nested merge with explicit overriding limit, and a merge sequence containing conflicting limits: rejected, including cases that YAML might otherwise override. This implementation deliberately rejects all root and policy merge keys, including nonconflicting ones; that restriction should remain visible to integrators.
- Malformed YAML and `!!python/object/apply` tags: rejected as invalid YAML; no object construction or external command occurred.

The pre-parsed mapping API cannot recover duplicates already discarded by a different parser. Consumers of YAML must use the validated file-loading boundary or enforce equivalent duplicate detection upstream. Non-policy root sections are not subjected to policy duplicate checking, except root merges are rejected; they are outside this targeted policy contract.

## B-02: public construction and deep immutability

Independent `dataclasses.replace` probes on a valid public configuration rejected `mode="active"`, `allow_output_writes=True`, version 2, a dict substituted for `baseline`, and a list substituted for `audit`. Each nested settings class independently rejected at least one wrong mutable or invalid value: baseline list, timezone list, NaN sag window, list expiry, boolean dwell, false mandatory safety setting and list audit setting. The unconfigured model rejects `configured=True`. Exact built-in primitive validation and exact nested class checks are at `:18–168`, with numeric/boolean helpers at `:204–225`.

Parser-produced configs do not retain source dictionaries: the test suite mutates a source nested baseline after parsing and the result remains unchanged. Frozen nested dataclasses and primitive-only fields prevent ordinary later mutation; projections are equivalent on repeated calls and do not alter configuration. Retained fields such as sunrise offset, adaptation settings, command limit and audit controls remain present. `to_baseline()` cannot take an invalid ordinary public configuration without the constructor first rejecting it. As with normal Python frozen dataclasses, deliberate use of low-level `object.__setattr__` is not treated as a supported construction/mutation API.

## B-03: voltage feasibility

Independent `BaselineSettings` constructor probes rejected max 24 or 25 with exit 25 and unknown enter, max 25 with enter 26 and unknown exit, enter 25 with exit 26, and hysteresis 2 with exit 24 / enter 25. The error identified `max_battery_voltage_v`, `enter_battery_v` or `min_hysteresis_v` as appropriate. Feasible partial configurations with unknown exit or unknown enter were accepted, and all-null values remained None. Checks at `:36–52` do not infer missing values. The upper battery safety bound stays separate from the enter/exit switching thresholds in the projected Phase 1A baseline.

## B-04: temporal bounds and date-domain checks

All 13 configured fields ending in `_min`, including retained-only fields, share the software-domain maximum of 10,080 minutes (`:204–219`). An independent grid verified each rejected 10,080.01, negative, NaN, infinity, bool and string values with a configuration error. No clamping was observed. Setting all 13 to the maximum simultaneously succeeded; for example, voltage-sag window and failed-trial lockout both projected to 604,800 seconds.

For the projected baseline the evaluator caps failure lockout at its unchanged four-hour `max_churn_lockout_seconds`; escalation lookback is four hours; detection is at most seven days. The required history is therefore at most seven days plus eight hours, not the sum of all 13 individual maxima. Other separately mapped windows are at most seven days. This calculation is supported by `app/control/adaptive_output_policy.py:865–881` and lockout computation at `:1098–1126`.

Independent public-evaluator probes set every configured duration simultaneously to the maximum and evaluated snapshots at **0002-01-01 00:00 UTC** and **9998-12-31 23:59 UTC**. The early history crossed into year 1; the late lockout expiry crossed into year 9999. Histories included repeated short failed trials and churn. Both completed without overflow and returned BLOCK with `FAST_VOLTAGE_SAG`, `CHURN_LOCKOUT`, `EXCESSIVE_MODE_CHANGES` and `COOLDOWN`; respective active expiries were `0002-01-01 03:53 UTC` and `9999-01-01 03:52 UTC`. Separate short-collapse boundary probes also returned active lockouts at each end. These probes test composed calculations, not merely one maximum duration, and do not claim exhaustive testing of every datetime or input graph.

The loader comment states a software-domain assumption for snapshot years 2–9998; it does not claim Phase 1A enforces that domain. Phase 1A remains unchanged and its known year-1 boundary limitation is outside this acceptance contract. Future integration must enforce the snapshot-year assumption and ensure related timestamps and timezone normalization are representable; no such runtime guard is supplied by this configuration-only phase.

## B-05: timezone purity and resolution

With non-null `Europe/London`, pure `parse_output_policy` and repeated `to_baseline` succeeded while builtins.open, the module's `ZoneInfo`, socket creation and the system clock were patched to raise. The parsed value is retained and `timezone_verified` is false. Path traversal (`../UTC`), empty identifiers, double slash, leading slash, whitespace and a backslash-containing value were rejected by syntax validation at `:228–230`.

At the documented read-only file boundary, a valid `Europe/London` resolved; two loads returned equivalent configurations and unchanged file bytes. A syntactically valid nonexistent `Moon/Caldera` was accepted only as an unverified in-memory identifier but rejected by file loading with a timezone-specific error (`:337–342`). A missing timezone remains None; no installation timezone is guessed. Database resolution establishes availability, not the actual site's timezone; `timezone_verified` remains false even after resolution. File-boundary resolution legitimately depends on installed IANA data, while pure parsing does not.

## Version 1, projection and phase compatibility

Original v1 and the extended v1 remain accepted. Independent checks exercised all 27 missing/null/present combinations of the three optional safety fields; each projected to None or the supplied illustrative value at the corresponding attribute. No Tuya relay threshold or charging-policy value is mapped as an inverter limit. Unknown policy fields, unsupported modes, write permission and versions are rejected. Other top-level devices and charging sections remain outside this loader and were not modified.

The suite separately exercises a full illustrative projected baseline with all independent prerequisites and obtains a shadow CONSIDER, then removes the discharge limit and obtains BLOCK with `UNKNOWN_BATTERY_CURRENT_SAFETY`. Illustrative thresholds are test values, not verified device ratings. Phase 1A source/test hashes are the approved versions, and all 70 Phase 1A tests passed.

## Shadow-only, side effects and tests

The loader imports no DESS, Tuya, database or cloud client, and a repository search found no production runtime wiring. File loading reads the chosen YAML and resolves a timezone when present; it performs no writes. Parsing/projection use in-memory validation and do not consult the filesystem, network, hardware, database or clock in the tested paths. No adaptation overwrites the approved baseline. These are code-path and targeted monkeypatch checks, not a claim about unreviewed future integrations.

Executed `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`: **88 total, 88 passed, 0 failed**, comprising 70 Phase 1A and 18 Phase 1B tests. The new tests exercise the public APIs and often assert intended paths. The existing date-boundary test checks a maximum PV window at a 2026 date and does not itself establish the full declared date-domain claim; the independent all-maximum, repeated-event probes above supply additional evidence at both endpoints. Some constructor tests broadly expect `TypeError` or `ValueError`; independent probes checked that the intended fields actually raise `OutputPolicyConfigError`. Temporary YAML probes were outside the repository. No network or hardware was contacted.

## Limitations and final recommendation

The date-domain guarantee assumes Phase 1A's unchanged defaults for non-configurable windows and the projection described above; changes to the evaluator or projected constants require re-evaluating the arithmetic. Any downstream YAML parser that discards duplicates before calling the in-memory API can bypass duplicate detection. Installation timezone, BMS/inverter limits, device capability and real telemetry remain operational prerequisites for later phases. None is represented as established by the illustrative fixtures.

**APPROVED** for Phase 1B's offline, shadow-only configuration contract. Preserve duplicate-aware loading and the date-domain assumption when integrating; do not enable output writes on the basis of this review.
