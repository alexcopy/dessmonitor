"""Shadow-only planner: produces no hardware calls or executable authorization."""
from dataclasses import dataclass
from decimal import Decimal
from math import isfinite

from app.control.easun_output_mapping import OutputField, parse_reported, validate_catalogue_value


@dataclass(frozen=True)
class Plan:
    status: str
    reason: str
    field: OutputField | None = None
    key: str | None = None
    reported_previous: Decimal | None = None
    proposed_value: Decimal | None = None
    executable: bool = False


def plan_battery_threshold(learner, snapshot, baseline, history, energy, bands, catalogue, *,
                           evaluated_at, reported_value, installation_voltage_class,
                           approved_min_v, approved_max_v, manual_override=False, unknown_write=False):
    """Re-evaluate guards; map only an exact, operator-bounded catalogue value."""
    if manual_override is not False or unknown_write is not False:
        return Plan('REFUSED', 'MANUAL_OVERRIDE_OR_UNKNOWN_WRITE')
    if type(approved_min_v) is not Decimal or type(approved_max_v) is not Decimal:
        return Plan('REFUSED', 'APPROVED_LIMITS_MISSING')
    if not approved_min_v.is_finite() or not approved_max_v.is_finite() or approved_min_v <= 0 or approved_min_v > approved_max_v:
        return Plan('REFUSED', 'APPROVED_LIMITS_INVALID')
    proposal = learner.propose(snapshot, baseline, history, energy, bands, evaluated_at=evaluated_at)
    if proposal.status != 'SHADOW_PROPOSAL':
        return Plan('REFUSED', proposal.reason)
    if proposal.expires_at is None or proposal.expires_at <= evaluated_at:
        return Plan('REFUSED', 'PROPOSAL_EXPIRED')
    if type(proposal.battery_threshold_v) not in (int, float) or not isfinite(proposal.battery_threshold_v):
        return Plan('REFUSED', 'INVALID_PROPOSAL')
    value = Decimal(str(proposal.battery_threshold_v))
    if not approved_min_v <= value <= approved_max_v:
        return Plan('REFUSED', 'OUTSIDE_APPROVED_LIMITS')
    try:
        previous = parse_reported(OutputField.BATTERY, reported_value)
        key = validate_catalogue_value(OutputField.BATTERY, str(value), catalogue,
                                       installation_voltage_class=installation_voltage_class)
    except ValueError:
        return Plan('REFUSED', 'CATALOGUE_OR_READBACK_INVALID')
    if previous == value:
        return Plan('REFUSED', 'ALREADY_REPORTED')
    return Plan('PROPOSED', 'SHADOW_ONLY', OutputField.BATTERY, key, previous, value)
