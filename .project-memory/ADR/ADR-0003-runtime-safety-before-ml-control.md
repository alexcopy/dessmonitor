# ADR-0003: Runtime Safety Before ML Control

## Status
Accepted (PR 0001)

## Context
dessmonitor controls physical relays (pond pumps, heaters, etc.) through the
Tuya IoT platform. The repository contains ML data collectors and analysis code
that could eventually be used for adaptive control. However, ML or AI control
of physical relays carries significant risk if not properly safeguarded.

## Decision
ML or AI control must NOT directly operate relays without a safety policy and
fallback mechanism. Specifically:

1. **Safety policy required:** Before any ML model can make control decisions,
   a safety policy must be defined that constrains allowed actions, sets
   operational bounds, and defines emergency stop conditions.

2. **Shadow/advisory mode first:** ML control must start in shadow mode
   (recommendations only, no direct action) or advisory mode (operator must
   approve actions) before production control is enabled.

3. **Fallback mechanism:** If the ML system fails, becomes unavailable, or
   produces out-of-bounds recommendations, the system must fall back to
   deterministic control rules.

4. **TimescaleDB production readiness:** TimescaleDB must not be enabled in
   production until database connectivity, schema migrations, and monitoring
   are verified. The current `init-db.sql` and `timescale_data_collector.py`
   are documented but not production-enabled.

## Consequences
- ML control enablement is deferred until safety infrastructure is in place.
- The roadmap (ROADMAP.md) reflects this priority: safety first, then ML.
- Future PRs that enable ML control must reference this ADR and demonstrate
  compliance with its requirements.
- This ADR may be superseded by a more detailed safety ADR in a later PR.

## Compliance
- AGENT_STANDARD.txt includes runtime safety requirements.
- ROADMAP.md prioritizes safety-first adaptive control before ML control.
- Task prompts for ML-related PRs must reference this ADR.
