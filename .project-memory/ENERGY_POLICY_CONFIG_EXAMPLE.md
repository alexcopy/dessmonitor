# Energy Policy Config Example — Documentation

## File

`examples/energy_policy.example.yaml`

## Purpose

This is a **static, no-secret, documentation-only example** of what an energy-aware
control policy configuration might look like for dessmonitor. It follows the policy
requirements documented in PR 0011 and the passive domain types introduced in PR 0012.

## Runtime Isolation

The example is **not runtime-loaded**. Specifically:

1. No runtime code reads the example in PR 0013.
2. No config loader is added.
3. No evaluator consumes this file.
4. No scheduler consumes this file.
5. No Tuya call consumes this file.
6. No ML code consumes this file.
7. No environment variable points to this file.
8. No automation is enabled.
9. No device switching behavior changes.

The file exists solely as a human-readable illustration of the policy shape.
Runtime config loading is deferred to a future PR.

## Fake Load IDs

All `load_id` values use the `example-` prefix:

- `example-light`
- `example-filter`
- `example-fan`

No real Tuya device identifiers, production load names, or values from
`devices.yaml`, `devices_prod.yaml`, or `config.json` are included.

## No Secrets

The file contains zero secrets. It does not include:

- `api_key`, `token`, `secret`, `password`, or `credential`
- `tuya_device_id`, `device_id`, or `local_ip`
- `kubeconfig`, `bearer`, or `private_key`
- Any real production device identifiers or credentials

## Schema Reference

The example illustrates these top-level sections (all non-runtime, example-only):

| Section | Purpose |
|---|---|
| `metadata` | Declares `runtime_loaded: false`, `example_only: true` |
| `global_policy` | Default power-source behavior and voltage thresholds |
| `battery_reserve` | Evening reserve target (26.5V) |
| `schedule_profiles` | Time-of-day and seasonal check intervals |
| `weather_adjustment` | Forecast → threshold multipliers (sunny/cloudy/rainy) |
| `readiness_defaults` | Default check interval and cooldown |
| `health_defaults` | Default check interval, failure limits, stale threshold |
| `manual_override` | Human operator override levers |
| `ml_advisory` | ML advisory explicitly disabled (`ml_advisory_enabled: false`, `ml_control_enabled: false`) |
| `loads` | Per-device policy for fake example loads |

## ML Control Status

ML advisory and ML control remain **disabled and deferred**:

- `ml_advisory_enabled: false` — ML advisory is not active.
- `ml_control_enabled: false` — ML control is not active.
- `deterministic_fallback: true` — Deterministic policy fallback is required.
- ML control remains deferred behind safety-reviewed gates per ADR-0003.

Real production config remains out of scope for PR 0013.
