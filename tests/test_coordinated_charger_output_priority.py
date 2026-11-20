import asyncio
import ast
import inspect
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.control.coordinated_charger_output_priority import (
    CoordinatedControlState,
    evaluate_coordinated_control,
    execute_utility_hold,
)
from app.control.grid_charge_output_coordination import (
    CoordinationDecision,
    CoordinationState,
    SettingObservation,
)
from app.control.grid_output_interlock import ChargerObservation, ChargerProvenance


NOW = datetime(2026, 12, 10, 12, 0, tzinfo=timezone.utc)


def shadow(state=CoordinationState.GRID_HOLD, *, reported_priority="SBU"):
    return CoordinationDecision(
        state=state,
        desired_profile="Utility",
        observed_mode="Line Mode",
        reported_priority=reported_priority,
        observed_charger="solar_and_mains",
        observed_at=NOW,
        priority_evidence=SettingObservation(reported_priority, NOW, True, True),
        reason_codes=(),
        recovery_cycles=0,
        continuity_uncertain=False,
        mode_changes=0,
        lockout_expires_at=None,
        interlock=None,
        winter=None,
        voltage_candidate="NOT_REQUIRED_WHILE_UTILITY",
    )


def charger(mode="solar_and_mains", provenance=ChargerProvenance.DEVICE_READBACK, at=NOW):
    return ChargerObservation(mode=mode, observed_at=at, provenance=provenance)


def priority(value="SBU", *, valid=True, at=NOW):
    return SettingObservation(value, at, valid, valid)


class FakeExecutor:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or {"status": "VERIFIED"}

    async def hold(self, charger, priority, **kwargs):
        self.calls.append((charger, priority, kwargs))
        return self.result


class CoordinatedChargerOutputPriorityTests(unittest.TestCase):
    def decide(self, **kwargs):
        return evaluate_coordinated_control(
            shadow(), charger=charger(), priority=priority(), evaluated_at=NOW, **kwargs)

    def test_verified_grid_recovery_with_sbu_requests_one_way_utility_hold(self):
        result = self.decide()
        self.assertEqual(result.state, CoordinatedControlState.UTILITY_HOLD)
        self.assertTrue(result.utility_hold_requested)
        self.assertTrue(result.return_blocked)
        self.assertTrue(result.shadow_only)
        self.assertFalse(result.command_issued)

    def test_already_utility_is_zero_write_noop(self):
        result = evaluate_coordinated_control(
            shadow(reported_priority="Utility"), charger=charger(), priority=priority("Utility"),
            evaluated_at=NOW)
        self.assertEqual(result.state, CoordinatedControlState.UTILITY_HOLD)
        self.assertFalse(result.utility_hold_requested)
        self.assertIn("ALREADY_UTILITY", result.reason_codes)

    def test_unverified_charger_or_priority_never_authorizes_hold(self):
        result = evaluate_coordinated_control(
            shadow(), charger=charger(provenance=ChargerProvenance.UNKNOWN),
            priority=priority(), evaluated_at=NOW)
        self.assertEqual(result.state, CoordinatedControlState.GRID_RECOVERY_REQUESTED)
        self.assertFalse(result.utility_hold_requested)
        result = evaluate_coordinated_control(
            shadow(), charger=charger(), priority=priority(valid=False), evaluated_at=NOW)
        self.assertEqual(result.state, CoordinatedControlState.GRID_RECOVERY_REQUESTED)
        self.assertFalse(result.utility_hold_requested)

    def test_solar_only_never_restores_sbu(self):
        result = evaluate_coordinated_control(
            shadow(reported_priority="Utility"), charger=charger("solar_only"),
            priority=priority("Utility"), evaluated_at=NOW)
        self.assertEqual(result.state, CoordinatedControlState.SOLAR_RETURN_BLOCKED)
        self.assertIn("AUTOMATIC_SBU_DISABLED", result.reason_codes)

    def test_manual_and_emergency_precedence(self):
        manual = self.decide(manual_utility_hold=True)
        self.assertEqual(manual.state, CoordinatedControlState.MANUAL_OVERRIDE)
        self.assertFalse(manual.utility_hold_requested)
        emergency = self.decide(emergency_recovery=True)
        self.assertEqual(emergency.state, CoordinatedControlState.EMERGENCY_RECOVERY)
        self.assertFalse(emergency.utility_hold_requested)

    def test_expired_evidence_fails_closed(self):
        old = NOW - timedelta(seconds=181)
        result = evaluate_coordinated_control(
            shadow(), charger=charger(at=old), priority=priority(at=old), evaluated_at=NOW)
        self.assertEqual(result.state, CoordinatedControlState.GRID_RECOVERY_REQUESTED)
        self.assertFalse(result.utility_hold_requested)

    def test_unknown_shadow_or_caller_reserve_cannot_promote_return(self):
        result = evaluate_coordinated_control(
            shadow(CoordinationState.FAULT_HOLD), charger=charger(), priority=priority(), evaluated_at=NOW)
        self.assertEqual(result.state, CoordinatedControlState.FAULT_HOLD)
        self.assertNotIn(result.state, (CoordinatedControlState.SOLAR_RETURN_ELIGIBLE,
                                        CoordinatedControlState.SOLAR_OPERATION))

    def test_injected_executor_is_called_once_and_never_for_solar_return(self):
        executor = FakeExecutor()
        result = self.decide()
        returned = asyncio.run(execute_utility_hold(
            result, executor, charger=charger(), priority=priority(), evaluated_at=NOW,
            observation_fingerprint="response-1", working_mode="Line Mode"))
        self.assertEqual(returned, {"status": "VERIFIED"})
        self.assertEqual(len(executor.calls), 1)
        blocked = evaluate_coordinated_control(
            shadow(reported_priority="Utility"), charger=charger("solar_only"),
            priority=priority("Utility"), evaluated_at=NOW)
        self.assertIsNone(asyncio.run(execute_utility_hold(
            blocked, executor, charger=charger("solar_only"), priority=priority("Utility"),
            evaluated_at=NOW, observation_fingerprint="response-2")))
        self.assertEqual(len(executor.calls), 1)

    def test_uncertain_executor_result_is_not_retried_by_adapter(self):
        executor = FakeExecutor({"status": "UNKNOWN"})
        result = self.decide()
        asyncio.run(execute_utility_hold(
            result, executor, charger=charger(), priority=priority(), evaluated_at=NOW,
            observation_fingerprint="response-1"))
        self.assertEqual(len(executor.calls), 1)

    def test_module_has_no_transport_or_polling_path(self):
        from app.control import coordinated_charger_output_priority as module
        tree = ast.parse(inspect.getsource(module))
        imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        self.assertFalse(any(name in ("app.api", "run", "service.inverter_monitor")
                             or "transport" in name or "utility_hold" in name for name in imports))
        calls = {node.func.attr for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        self.assertTrue(calls.isdisjoint({"ctrl_device", "fetch_device_data", "set_charger_source"}))


if __name__ == "__main__":
    unittest.main()
