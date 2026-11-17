"""Task 0076: focused tests for the advisory grid-charge / output-source interlock.

All scenarios here are SIMULATED_SCENARIO data built in memory. The 13 November 2026
export (615 records) is not available to these tests; the 08:24 values below are
transcribed from the approved PLAN.txt and are labelled simulated, not export evidence.
Baseline voltages in TEST_LIMITS are test inputs, not hardware or BMS limits.
"""
import ast
import importlib
import subprocess
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.control.adaptive_output_policy import ForecastObservation, OutputPolicyBaseline, OutputTelemetry
from app.control.grid_output_interlock import (
    ChargePolicyProvenance, ChargePolicyStatus, ChargerObservation, ChargerProvenance, DurableInterlockState, InterlockAction,
    InterlockBaseline, InterlockDecision, InterlockReason as R, InterlockSnapshot, InterlockState as S,
    KnownStatus, PvContext, ReplayLabel, ReplayMeasurementValidity, ShadowProposal, StateSource, TransitionOrigin, TrialPhase,
    evaluate_interlock, replay_records,
)

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
DAY = datetime(2026, 11, 13, tzinfo=UTC)
SUNRISE, SUNSET = DAY.replace(hour=7, minute=30), DAY.replace(hour=16, minute=30)
NOON = DAY.replace(hour=12)
# Test-only baseline values (NOT hardware limits): the evaluator never invents them.
TEST_LIMITS = OutputPolicyBaseline(exit_battery_v=24.5, enter_battery_v=25.5, max_battery_voltage_v=29.0,
                                   max_battery_discharge_a=60.0)
BASELINE = InterlockBaseline(output=TEST_LIMITS)
KNOWN_PV = PvContext(curtailment=KnownStatus.ABSENT, external_controller=KnownStatus.ABSENT, timezone_verified=True)
PERSISTED = DurableInterlockState(source=StateSource.PERSISTED, recovery_in_progress=False)


def series(start, segments, step=60):
    """Minute samples. Each segment: (mode, minutes, pv_w, load_w, volts, chg_a, dis_a)."""
    points, at = [], start
    for mode, minutes, pv, load, volts, chg, dis in segments:
        for i in range(minutes):
            v = volts(i) if callable(volts) else volts
            points.append(OutputTelemetry(observed_at=at, working_mode=mode, pv_power_w=pv, output_power_w=load,
                                          battery_voltage=v, battery_current_chg=chg, battery_current_dis=dis))
            at += timedelta(seconds=step)
    return points


def eligible_history(end=NOON):
    """8.5 h: Line, then a 40-min comparable loaded Invert period, then 50 min of Line."""
    start = end - timedelta(minutes=510)
    return series(start, [("Line Mode", 420, 900, 600, 26.2, 0.0, 0.0),
                          ("Invert Mode", 40, 900, 600, 26.0, 0.0, 10.0),
                          ("Line Mode", 50, 900, 600, 26.2, 0.0, 0.0)])


def snapshot(telemetry, *, charger="ok", policy="idle", pv=KNOWN_PV, at=NOON, **extra):
    t = replace(telemetry, sunrise=SUNRISE, sunset=SUNSET,
                forecast=ForecastObservation(observed_at=at - timedelta(minutes=1), valid_at=at + timedelta(hours=1), clouds_pct=10))
    if charger == "ok":
        charger = ChargerObservation(mode="solar_only", observed_at=at - timedelta(seconds=20),
                                     provenance=ChargerProvenance.DEVICE_READBACK)
    elif charger is None:
        charger = ChargerObservation()
    if policy == "idle":
        policy = ChargePolicyStatus(state="idle", emergency_active=False, desired_charger_mode="solar_only",
                                    evaluated_at=at - timedelta(seconds=20),
                                    provenance=ChargePolicyProvenance.RUNTIME_STATUS, verified=True)
    return InterlockSnapshot(telemetry=t, charger=charger, policy=policy, pv_context=pv, **extra)


def run(points, *, durable=PERSISTED, at=NOON, proposal=None, baseline=BASELINE, **kw):
    *history, last = points
    return evaluate_interlock(snapshot(last, at=at, **kw), baseline, tuple(history), durable,
                              evaluated_at=at, proposal=proposal)


def readback(mode, age_s=20, at=NOON, provenance=ChargerProvenance.DEVICE_READBACK):
    return ChargerObservation(mode=mode, observed_at=at - timedelta(seconds=age_s), provenance=provenance)


def policy(state, desired, *, emergency=False, age_s=20, at=NOON):
    return ChargePolicyStatus(state=state, emergency_active=emergency, desired_charger_mode=desired,
                              evaluated_at=at - timedelta(seconds=age_s),
                              provenance=ChargePolicyProvenance.RUNTIME_STATUS, verified=True)


class EligibilityAndCentralInvariant(unittest.TestCase):
    def test_stable_solar_return_with_independent_reserve_evidence_is_advisory_eligible(self):
        d = run(eligible_history())
        self.assertIs(d.state, S.BATTERY_RETURN_ELIGIBLE, d.reason_codes)
        self.assertIs(d.decision, InterlockAction.ADVISE)
        self.assertIn(R.BATTERY_RETURN_ELIGIBLE, d.reason_codes)
        self.assertTrue(any(e.name == "reserve_evidence" for e in d.evidence))
        self.assertFalse(d.command_issued)
        self.assertTrue(d.advisory_only)

    def test_recovery_completion_is_reported_but_never_creates_eligibility(self):
        pts = eligible_history()
        recovering = DurableInterlockState(source=StateSource.PERSISTED, recovery_in_progress=True)
        d = run(pts, durable=recovering)
        self.assertIs(d.state, S.GRID_RECOVERY_COMPLETE)
        self.assertIs(d.decision, InterlockAction.HOLD)
        self.assertIn(R.GRID_RECOVERY_COMPLETE_NOT_ELIGIBILITY, d.reason_codes)
        self.assertFalse(d.updated_state.recovery_in_progress)
        # After the completion report window the loaded Invert evidence predates the recovery.
        later = NOON + timedelta(minutes=10)
        follow = run(eligible_history(later), durable=replace(d.updated_state, source=StateSource.PERSISTED), at=later)
        self.assertIs(follow.state, S.SOLAR_RETURN_CANDIDATE)
        self.assertIs(follow.decision, InterlockAction.HOLD)
        self.assertIn(R.POST_RECOVERY_RESERVE_UNPROVEN, follow.reason_codes)
        self.assertNotIn(R.BATTERY_RETURN_ELIGIBLE, follow.reason_codes)

    def test_newer_failed_invert_run_invalidates_older_reserve_evidence(self):
        start = NOON - timedelta(minutes=510)
        newer_failure = lambda i: 25.0 - 0.015 * i
        pts = series(start, [
            ("Line Mode", 200, 900, 600, 26.2, 0.0, 0.0),
            ("Invert Mode", 40, 900, 600, 26.0, 0.0, 10.0),
            ("Line Mode", 180, 900, 600, 26.2, 0.0, 0.0),
            ("Invert Mode", 40, 900, 600, newer_failure, 0.0, 10.0),
            ("Line Mode", 51, 900, 600, 26.2, 0.0, 0.0),
        ])
        d = run(pts)
        self.assertNotEqual(d.decision, InterlockAction.ADVISE)
        self.assertNotIn(R.BATTERY_RETURN_ELIGIBLE, d.reason_codes)
        self.assertFalse(any(e.name == "reserve_evidence" for e in d.evidence))

    def test_overnight_topup_and_0700_window_end(self):
        start = DAY.replace(hour=6, minute=59) - timedelta(minutes=510)
        night = series(start, [("Line Mode", 511, 0, 400, 25.4, 2.0, 0.0)])
        active_at = DAY.replace(hour=6, minute=59)
        active = run(night, at=active_at, charger=readback("solar_and_mains", at=active_at),
                     policy=policy("long_night_topup", "solar_and_mains", at=active_at))
        self.assertIs(active.state, S.GRID_RECOVERY_ACTIVE)
        self.assertIs(active.decision, InterlockAction.BLOCK)
        self.assertIn(R.GRID_CHARGE_NOT_SOLAR, active.reason_codes)
        self.assertNotIn(R.EMERGENCY_RECOVERY_PRECEDENCE, active.reason_codes)
        ended_at = DAY.replace(hour=7)
        ended_pts = night + series(active_at + timedelta(minutes=1), [("Line Mode", 1, 0, 400, 25.6, 0.0, 0.0)])
        ended = run(ended_pts, at=ended_at, durable=replace(active.updated_state, source=StateSource.PERSISTED),
                    charger=readback("solar_only", at=ended_at), policy=policy("idle", "solar_only", at=ended_at))
        self.assertIs(ended.state, S.GRID_RECOVERY_COMPLETE)
        after_at = DAY.replace(hour=7, minute=10)
        after_pts = ended_pts + series(ended_at + timedelta(minutes=1), [("Line Mode", 10, 0, 400, 25.6, 0.0, 0.0)])
        after = run(after_pts, at=after_at, durable=replace(ended.updated_state, source=StateSource.PERSISTED),
                    charger=readback("solar_only", at=after_at), policy=policy("idle", "solar_only", at=after_at))
        self.assertIs(after.state, S.GRID_OUTPUT_HOLD)
        self.assertIn(R.SOLAR_NOT_PRODUCTIVE, after.reason_codes)
        self.assertIn(R.WEAK_SOLAR_SUPPORT, after.reason_codes)


class SolarDemandAndVoltage(unittest.TestCase):
    def test_weak_pv_against_high_demand_holds(self):
        pts = eligible_history()
        pts[-1] = replace(pts[-1], pv_power_w=20, output_power_w=1284)
        d = run(pts)
        self.assertIs(d.state, S.GRID_OUTPUT_HOLD)
        for reason in (R.WEAK_SOLAR_SUPPORT, R.LOAD_EXCEEDS_PV, R.SOLAR_NOT_PRODUCTIVE):
            self.assertIn(reason, d.reason_codes)

    def test_measured_inverter_demand_is_used_not_the_tuya_managed_total(self):
        pts = eligible_history()
        pts[-1] = replace(pts[-1], output_power_w=2000)
        d = run(pts, managed_load_w_diagnostic=30.0)
        self.assertIs(d.state, S.GRID_OUTPUT_HOLD)
        self.assertIn(R.LOAD_EXCEEDS_PV, d.reason_codes)
        self.assertIn(R.HIGH_MEASURED_LOAD, d.reason_codes)
        self.assertIn("managed_load_w_diagnostic_not_used", [e.name for e in d.evidence])

    def test_line_mode_voltage_rebound_is_not_recovery(self):
        pts = eligible_history()
        pts[-2] = replace(pts[-2], working_mode="Invert Mode", battery_voltage=25.6, battery_current_dis=10.0)
        pts[-1] = replace(pts[-1], battery_voltage=26.0)
        d = run(pts)
        self.assertIn(R.VOLTAGE_REBOUND_NOT_RECOVERY, d.reason_codes)
        self.assertNotEqual(d.decision, InterlockAction.ADVISE)

    def test_charge_current_with_weak_pv_and_unverified_charger_is_not_solar(self):
        pts = eligible_history()
        pts[-1] = replace(pts[-1], pv_power_w=40, battery_current_chg=3.0)
        d = run(pts, charger=None)
        self.assertIn(R.CHARGING_SOURCE_UNVERIFIED, d.reason_codes)
        self.assertNotEqual(d.decision, InterlockAction.ADVISE)

    def test_reported_soc_cannot_authorise_eligibility(self):
        pts = eligible_history()
        pts[-1] = replace(pts[-1], pv_power_w=20, output_power_w=1284, battery_soc=100.0)
        self.assertIs(run(pts).state, S.GRID_OUTPUT_HOLD)

    def test_unknown_pv_context_or_unverified_timezone_blocks_optimism(self):
        for context, reason in ((PvContext(timezone_verified=True), R.PV_CONTEXT_UNKNOWN),
                                (replace(KNOWN_PV, curtailment=KnownStatus.PRESENT), R.PV_CURTAILMENT_OR_EXTERNAL_CONTROLLER),
                                (replace(KNOWN_PV, timezone_verified=False), R.TIMEZONE_UNVERIFIED)):
            with self.subTest(reason=reason):
                d = run(eligible_history(), pv=context)
                self.assertIs(d.state, S.GRID_OUTPUT_HOLD)
                self.assertIn(reason, d.reason_codes)

    def test_missing_reserve_evidence_leaves_only_a_candidate(self):
        start = NOON - timedelta(minutes=510)
        pts = series(start, [("Line Mode", 511, 900, 600, 26.2, 0.0, 0.0)])
        d = run(pts)
        self.assertIs(d.state, S.SOLAR_RETURN_CANDIDATE)
        self.assertIs(d.decision, InterlockAction.HOLD)
        self.assertIn(R.RESERVE_EVIDENCE_MISSING, d.reason_codes)


class ChargerReadback(unittest.TestCase):
    def test_missing_stale_or_cached_charger_state_is_never_proof_grid_charging_stopped(self):
        cases = {"missing": None, "stale": readback("solar_only", age_s=900),
                 "cached_command": readback("solar_only", provenance=ChargerProvenance.LAST_COMMAND_CACHE),
                 "ambiguous_label": readback("solar_priority")}
        believed = DurableInterlockState(source=StateSource.PERSISTED, recovery_in_progress=True)
        for name, charger in cases.items():
            with self.subTest(name):
                d = run(eligible_history(), charger=charger, durable=believed)
                self.assertIs(d.state, S.GRID_OUTPUT_HOLD)
                self.assertIn(R.CHARGER_STATE_UNVERIFIED, d.reason_codes)
                self.assertTrue(d.updated_state.recovery_in_progress, "stale/unknown charger never ends recovery")

    def test_readback_showing_grid_charging_while_policy_idle_is_active_recovery_and_a_mismatch(self):
        d = run(eligible_history(), charger=readback("solar_and_mains"))
        self.assertIs(d.state, S.GRID_RECOVERY_ACTIVE)
        self.assertIn(R.CHARGER_POLICY_MISMATCH, d.reason_codes)

    def test_policy_recovery_with_stale_readback_is_still_recovery(self):
        d = run(eligible_history(), charger=readback("solar_only", age_s=900),
                policy=policy("emergency_recovery", "solar_and_mains", emergency=True))
        self.assertIs(d.state, S.GRID_RECOVERY_ACTIVE)
        self.assertTrue(d.updated_state.recovery_in_progress)

    def test_stale_charge_policy_status_is_unavailable(self):
        d = run(eligible_history(), policy=policy("idle", "solar_only", age_s=900))
        self.assertIn(R.CHARGE_POLICY_UNAVAILABLE, d.reason_codes)
        self.assertNotEqual(d.decision, InterlockAction.ADVISE)

    def test_fresh_but_unverified_charge_policy_status_blocks_advice(self):
        status = replace(policy("idle", "solar_only"),
                         provenance=ChargePolicyProvenance.UNKNOWN, verified=False)
        d = run(eligible_history(), policy=status)
        self.assertIs(d.state, S.GRID_OUTPUT_HOLD)
        self.assertIn(R.CHARGE_POLICY_METADATA_UNVERIFIED, d.reason_codes)
        self.assertNotEqual(d.decision, InterlockAction.ADVISE)

    def test_incomplete_or_contradictory_charge_policy_metadata_blocks_advice(self):
        cases = (
            ("emergency contradiction", policy("idle", "solar_only", emergency=True), R.CHARGE_POLICY_CONTRADICTORY),
            ("missing emergency flag", policy("idle", "solar_only", emergency=None), R.CHARGE_POLICY_METADATA_UNVERIFIED),
            ("missing desired mode", policy("idle", None, emergency=False), R.CHARGE_POLICY_METADATA_UNVERIFIED),
            ("unknown desired mode", policy("idle", "unverified", emergency=False), R.CHARGE_POLICY_METADATA_UNVERIFIED),
        )
        for name, status, reason in cases:
            with self.subTest(name):
                d = run(eligible_history(), policy=status)
                self.assertIs(d.state, S.GRID_OUTPUT_HOLD)
                self.assertNotEqual(d.decision, InterlockAction.ADVISE)
                self.assertIn(reason, d.reason_codes)

    def test_grid_charge_voltage_rebound_is_not_solar_reserve(self):
        pts = eligible_history()
        pts[-2] = replace(pts[-2], working_mode="Invert Mode", battery_voltage=25.6,
                          battery_current_dis=10.0, battery_current_chg=0.0)
        pts[-1] = replace(pts[-1], working_mode="Line Mode", battery_voltage=26.0,
                          battery_current_dis=0.0, battery_current_chg=2.0)
        d = run(pts, charger=readback("solar_and_mains"),
                policy=policy("long_night_topup", "solar_and_mains"))
        self.assertIs(d.state, S.GRID_RECOVERY_ACTIVE)
        self.assertIn(R.GRID_CHARGE_NOT_SOLAR, d.reason_codes)
        self.assertIn(R.VOLTAGE_REBOUND_NOT_RECOVERY, d.reason_codes)
        self.assertNotEqual(d.decision, InterlockAction.ADVISE)


class TelemetryFaults(unittest.TestCase):
    def test_incomplete_stale_future_gapped_contradictory_or_unknown_mode_fail_closed(self):
        def variant(name):
            pts = eligible_history()
            at = NOON
            if name == "missing_discharge_current":
                pts[-1] = replace(pts[-1], battery_current_dis=None)
            elif name == "stale":
                at = NOON + timedelta(minutes=10)
            elif name == "future":
                at = NOON - timedelta(minutes=5)
                pts = [p for p in pts if p.observed_at <= at] + [pts[-1]]
            elif name == "gap":
                pts = [p for p in pts if not NOON - timedelta(minutes=40) < p.observed_at < NOON - timedelta(minutes=20)]
            elif name == "contradictory":
                pts[-1] = replace(pts[-1], battery_current_chg=5.0, battery_current_dis=5.0)
            elif name == "unknown_mode":
                pts[-1] = replace(pts[-1], working_mode="Fault Mode")
            return run(pts, at=at)
        expected = {"missing_discharge_current": R.INCOMPLETE_TELEMETRY, "stale": R.STALE_TELEMETRY,
                    "future": R.FUTURE_TIMESTAMP, "gap": R.STALE_TELEMETRY, "contradictory": R.CONTRADICTORY_TELEMETRY,
                    "unknown_mode": R.WORKING_MODE_UNKNOWN}
        for name, reason in expected.items():
            with self.subTest(name):
                d = variant(name)
                self.assertIs(d.state, S.FAULT_HOLD, d.reason_codes)
                self.assertIs(d.decision, InterlockAction.BLOCK)
                self.assertIn(reason, d.reason_codes)

    def test_future_charger_timestamp_is_a_fault(self):
        d = run(eligible_history(), charger=readback("solar_only", age_s=-60))
        self.assertIs(d.state, S.FAULT_HOLD)
        self.assertIn(R.FUTURE_TIMESTAMP, d.reason_codes)


def churn_history(end=NOON):
    start = end - timedelta(minutes=510)
    flips = [("Line Mode" if i % 2 == 0 else "Invert Mode", 5, 900, 600, 26.1, 0.0, 0.0 if i % 2 == 0 else 10.0)
             for i in range(10)]
    return series(start, [("Line Mode", 460, 900, 600, 26.2, 0.0, 0.0), *flips, ("Line Mode", 1, 900, 600, 26.2, 0.0, 0.0)])


class TrialsLockoutAndRestart(unittest.TestCase):
    def test_repeated_rapid_line_invert_transitions_produce_churn_lockout(self):
        d = run(churn_history())
        self.assertIs(d.state, S.CHURN_LOCKOUT)
        self.assertIs(d.decision, InterlockAction.BLOCK)
        self.assertIn(R.CHURN_LOCKOUT, d.reason_codes)
        self.assertGreater(d.lockout_expires_at, NOON)
        self.assertEqual(d.updated_state.lockout_expires_at, d.lockout_expires_at)
        self.assertIs(d.transition_origin, TransitionOrigin.UNKNOWN_EXTERNAL_OR_DEVICE)

    def test_short_loaded_collapse_in_invert_mode_is_a_failed_trial(self):
        start = NOON - timedelta(minutes=510)
        pts = series(start, [("Line Mode", 505, 900, 600, 26.2, 0.0, 0.0),
                             ("Invert Mode", 6, 900, 600, lambda i: 25.6 - 0.1 * i, 0.0, 20.0)])
        d = run(pts)
        self.assertIs(d.state, S.CHURN_LOCKOUT)
        self.assertIs(d.trial_phase, TrialPhase.FAILED)
        self.assertIn(R.FAILED_BATTERY_TRIAL, d.reason_codes)
        self.assertFalse(d.application_authorized_trial)

    def test_observed_invert_mode_is_an_observation_label_not_an_application_trial(self):
        start = NOON - timedelta(minutes=510)
        pts = series(start, [("Line Mode", 500, 900, 600, 26.2, 0.0, 0.0),
                             ("Invert Mode", 11, 900, 600, 26.1, 0.0, 10.0)])
        d = run(pts)
        self.assertIs(d.state, S.BATTERY_TRIAL)
        self.assertIs(d.decision, InterlockAction.HOLD)
        self.assertIs(d.trial_phase, TrialPhase.IN_PROGRESS)
        self.assertFalse(d.application_authorized_trial)
        self.assertEqual(d.observed_working_mode, "Invert Mode")

    def test_restart_keeps_a_persisted_lockout_even_without_the_triggering_history(self):
        durable = DurableInterlockState(source=StateSource.PERSISTED, recovery_in_progress=False,
                                        lockout_expires_at=NOON + timedelta(minutes=30))
        d = run(eligible_history(), durable=durable)
        self.assertIs(d.state, S.CHURN_LOCKOUT)
        self.assertIn(R.LOCKOUT_PRESERVED_ACROSS_RESTART, d.reason_codes)
        self.assertEqual(d.lockout_expires_at, NOON + timedelta(minutes=30))

    def test_unavailable_restart_state_fails_closed(self):
        d = run(eligible_history(), durable=DurableInterlockState(source=StateSource.UNAVAILABLE))
        self.assertIs(d.state, S.FAULT_HOLD)
        self.assertIn(R.DURABLE_STATE_UNAVAILABLE, d.reason_codes)

    def test_reconstructable_history_rebuilds_the_lockout(self):
        d = run(churn_history(), durable=DurableInterlockState(source=StateSource.RECONSTRUCTED, recovery_in_progress=False))
        self.assertIs(d.state, S.CHURN_LOCKOUT)

    def test_reconstruction_without_enough_history_never_yields_eligibility(self):
        short = eligible_history()[-100:]
        d = run(short, durable=DurableInterlockState(source=StateSource.RECONSTRUCTED, recovery_in_progress=False))
        self.assertNotEqual(d.decision, InterlockAction.ADVISE)
        self.assertNotEqual(d.state, S.BATTERY_RETURN_ELIGIBLE)

    def test_unknown_recovery_history_fails_closed_after_restart(self):
        start = NOON - timedelta(minutes=510)
        pts = series(start, [
            ("Line Mode", 30, 0, 400, 25.0, 0.0, 0.0),
            ("Invert Mode", 40, 600, 600, 25.4, 0.0, 25.0),
            ("Line Mode", 140, 0, 400, 24.2, 2.0, 0.0),
            ("Line Mode", 300, 900, 600, 26.2, 0.0, 0.0),
        ])
        baseline = replace(BASELINE, output=replace(TEST_LIMITS, max_battery_charge_a=30.0))
        d = run(pts, durable=DurableInterlockState(source=StateSource.RECONSTRUCTED),
                baseline=baseline, charger=readback("solar_only"), policy=policy("idle", "solar_only"))
        self.assertIs(d.state, S.FAULT_HOLD)
        self.assertNotEqual(d.decision, InterlockAction.ADVISE)
        self.assertIn(R.DURABLE_STATE_UNRECONSTRUCTABLE, d.reason_codes)
        self.assertFalse(any(e.name == "reserve_evidence" for e in d.evidence))

    def test_expired_lockout_requires_evidence_gathered_after_expiry(self):
        expired = DurableInterlockState(source=StateSource.PERSISTED, recovery_in_progress=False,
                                        last_lockout_expires_at=NOON - timedelta(minutes=20))
        d = run(eligible_history(), durable=expired)
        self.assertIs(d.state, S.SOLAR_RETURN_CANDIDATE)
        self.assertIn(R.POST_LOCKOUT_EVIDENCE_PENDING, d.reason_codes)
        self.assertIn(R.RESERVE_EVIDENCE_MISSING, d.reason_codes)
        self.assertIsNone(d.lockout_expires_at)


class PrecedenceAndInvariants(unittest.TestCase):
    def test_emergency_recovery_has_precedence_over_everything(self):
        emergency = policy("emergency_recovery", "solar_and_mains", emergency=True, at=NOON + timedelta(minutes=10))
        d = run(churn_history(), at=NOON + timedelta(minutes=10), policy=emergency,
                durable=DurableInterlockState(source=StateSource.UNAVAILABLE))
        self.assertIs(d.state, S.GRID_RECOVERY_ACTIVE)
        self.assertIs(d.decision, InterlockAction.BLOCK)
        self.assertIn(R.EMERGENCY_RECOVERY_PRECEDENCE, d.reason_codes)
        self.assertIn(R.CHURN_LOCKOUT, d.reason_codes, "the lockout is still reported and persisted")
        self.assertIsNotNone(d.updated_state.lockout_expires_at)

    def test_fault_precedes_lockout_and_the_lockout_is_still_persisted(self):
        pts = churn_history()
        pts[-1] = replace(pts[-1], battery_current_dis=None)
        d = run(pts)
        self.assertIs(d.state, S.FAULT_HOLD)
        self.assertIn(R.CHURN_LOCKOUT, d.reason_codes)
        self.assertIsNotNone(d.updated_state.lockout_expires_at)

    def test_decision_can_never_represent_a_command_or_an_authorised_trial(self):
        d = run(eligible_history())
        for change in ({"command_issued": True}, {"advisory_only": False}, {"application_authorized_trial": True},
                       {"state": S.GRID_OUTPUT_HOLD}):
            with self.subTest(change):
                with self.assertRaises(ValueError):
                    replace(d, **change)

    def test_proposals_expire_without_changing_the_baseline_or_the_result(self):
        base = run(eligible_history())
        active = run(eligible_history(), proposal=ShadowProposal("test", NOON + timedelta(hours=1)))
        expired = run(eligible_history(), proposal=ShadowProposal("test", NOON - timedelta(minutes=1)))
        self.assertEqual((active.state, active.decision), (base.state, base.decision))
        self.assertIsNotNone(active.active_proposal)
        self.assertIsNone(expired.active_proposal)
        self.assertIn(R.PROPOSAL_EXPIRED_DISCARDED, expired.reason_codes)
        self.assertEqual(expired.state, base.state)

    def test_inputs_are_not_mutated_and_results_are_deterministic(self):
        pts = eligible_history()
        frozen = list(pts)
        first, second = run(pts), run(pts)
        self.assertEqual(first, second)
        self.assertEqual(pts, frozen)


class Replay(unittest.TestCase):
    PLAN_0824 = {"time": "2026-11-13T08:24:00+00:00", "working_mode": "Invert Mode", "battery_voltage": 24.0,
                 "pv_power_w": 20, "output_power_w": 1284}

    def test_plan_documented_0824_observation_is_not_a_successful_return(self):
        """SIMULATED_SCENARIO: values transcribed from PLAN.txt; the export itself is unavailable."""
        rows = [{"time": "2026-11-13T08:%02d:00+00:00" % m, "working_mode": "Line Mode", "battery_voltage": 25.4,
                 "pv_power_w": 20, "output_power_w": 1250} for m in range(0, 24)] + [self.PLAN_0824]
        summary = replay_records(rows, source=ReplayLabel.SIMULATED_SCENARIO)
        last = summary.results[-1]
        self.assertIs(last.label, ReplayLabel.SIMULATED_SCENARIO)
        self.assertNotEqual(last.decision.decision, InterlockAction.ADVISE)
        self.assertNotEqual(last.decision.state, S.BATTERY_RETURN_ELIGIBLE)
        for reason in (R.WEAK_SOLAR_SUPPORT, R.LOAD_EXCEEDS_PV, R.CHARGER_STATE_UNVERIFIED):
            self.assertIn(reason, last.decision.reason_codes)
        # With full context (simulated readback and limits) the same point is a failed trial, not a return.
        start = datetime(2026, 11, 13, 8, 24, tzinfo=UTC) - timedelta(minutes=510)
        pts = series(start, [("Line Mode", 509, 20, 1250, 25.4, 0.0, 0.0), ("Invert Mode", 1, 20, 1284, 25.0, 0.0, 50.0)])
        pts.append(OutputTelemetry(observed_at=start + timedelta(minutes=510), working_mode="Invert Mode", pv_power_w=20,
                                   output_power_w=1284, battery_voltage=24.0, battery_current_chg=0.0, battery_current_dis=53.5))
        at = start + timedelta(minutes=510)
        d = run(pts, at=at, charger=readback("solar_only", at=at), policy=policy("idle", "solar_only", at=at))
        self.assertIn(d.state, (S.CHURN_LOCKOUT, S.FAULT_HOLD))
        self.assertNotEqual(d.decision, InterlockAction.ADVISE)

    def test_replay_preserves_order_nulls_and_counts_mode_changes_independently(self):
        rows = []
        for m in range(30):
            rows.append({"time": "2026-11-13T09:%02d:00+00:00" % m, "working_mode": "Invert Mode" if m % 6 in (3, 4) else "Line Mode",
                         "battery_voltage": 25.5, "pv_power_w": 30, "output_power_w": 900})
        rows[10]["battery_voltage"] = None
        rows[20]["working_mode"] = None
        rows.append({"time": "not-a-time", "working_mode": "Line Mode"})
        summary = replay_records(rows, source=ReplayLabel.SIMULATED_SCENARIO)
        self.assertEqual(summary.records, 31)
        self.assertEqual(summary.evaluated, 30)
        self.assertEqual([r.index for r in summary.results], list(range(31)))
        self.assertIs(summary.results[10].label, ReplayLabel.SIMULATED_SCENARIO)
        self.assertIn("battery_voltage", summary.results[10].missing_fields)
        self.assertIs(summary.results[11].label, ReplayLabel.SIMULATED_SCENARIO)
        self.assertIsNone(summary.results[30].decision)
        # Invert at minutes 3-4, 9-10, 15-16, 21-22, 27-28: ten changes. The Line->Invert change from
        # minute 19 to 21 spans the null mode at minute 20, so it is reported separately as uncertain.
        self.assertEqual(summary.observed_mode_changes, 9)
        self.assertEqual(summary.mode_changes_across_unknown, 1)
        self.assertEqual(summary.advise_count, 0)
        self.assertIn("battery_current_dis", summary.unknown_fields)
        self.assertIn("charger_mode", summary.unknown_fields)
        for result in summary.results[:30]:
            self.assertIn(result.decision.state, (S.FAULT_HOLD, S.CHURN_LOCKOUT, S.GRID_OUTPUT_HOLD))

    def test_replay_requires_explicit_provenance(self):
        row = {"time": "2026-11-13T09:00:00+00:00", "working_mode": "Line Mode",
               "battery_voltage": 25.0, "pv_power_w": 1, "output_power_w": 1}
        self.assertIs(replay_records([row]).results[0].label, ReplayLabel.UNVERIFIED_PROVENANCE)
        self.assertIs(replay_records([row], source=ReplayLabel.IMPORTED_UNVERIFIED).results[0].label,
                      ReplayLabel.IMPORTED_UNVERIFIED)
        self.assertIs(replay_records([row], source=ReplayLabel.INDEPENDENTLY_VALIDATED).results[0].label,
                      ReplayLabel.INDEPENDENTLY_VALIDATED)

    def test_replay_does_not_mark_malformed_voltage_as_verified(self):
        row = {**self.PLAN_0824, "battery_voltage": 0}
        result = replay_records([row], source=ReplayLabel.VERIFIED_FROM_EXPORT).results[0]
        self.assertIs(result.label, ReplayLabel.UNKNOWN_FROM_EXPORT)
        self.assertIs(result.provenance, ReplayLabel.VERIFIED_FROM_EXPORT)
        self.assertIs(result.measurement_validity, ReplayMeasurementValidity.INVALID)
        self.assertIn("battery_voltage", result.invalid_fields)
        self.assertNotEqual(result.decision.decision, InterlockAction.ADVISE)

    def test_replay_does_not_mark_nan_pv_as_verified(self):
        row = {**self.PLAN_0824, "pv_power_w": float("nan")}
        result = replay_records([row], source=ReplayLabel.VERIFIED_FROM_EXPORT).results[0]
        self.assertIs(result.label, ReplayLabel.UNKNOWN_FROM_EXPORT)
        self.assertIs(result.measurement_validity, ReplayMeasurementValidity.INVALID)
        self.assertIn("pv_power_w", result.invalid_fields)

    def test_replay_does_not_mark_unknown_working_mode_as_verified(self):
        row = {**self.PLAN_0824, "working_mode": "Unknown Mode"}
        result = replay_records([row], source=ReplayLabel.VERIFIED_FROM_EXPORT).results[0]
        self.assertIs(result.label, ReplayLabel.UNKNOWN_FROM_EXPORT)
        self.assertIs(result.measurement_validity, ReplayMeasurementValidity.INVALID)
        self.assertIn("working_mode", result.invalid_fields)
        self.assertIs(result.decision.state, S.FAULT_HOLD)


class NoHardwareWrites(unittest.TestCase):
    def test_evaluator_imports_no_device_charger_relay_or_tuya_module(self):
        tree = ast.parse((ROOT / "app/control/grid_output_interlock.py").read_text())
        imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} | {
            alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        self.assertTrue(imported <= {"__future__", "dataclasses", "datetime", "enum", "math", "typing",
                                     "app.control.adaptive_output_policy"}, imported)
        source = (ROOT / "app/control/grid_output_interlock.py").read_text()
        for name in ("ctrl_device", "set_charger_source", "switch_on", "switch_off", "write_setting", "shared_state", "asyncpg"):
            self.assertNotIn(name, source)
        probe = ("import sys, app.control.grid_output_interlock; "
                 "print(sorted(m for m in sys.modules if m.startswith(('app.api', 'app.tuya', 'app.service', 'shared_state', "
                 "'app.control.easun_output_actuator', 'app.device_initializer'))))")
        out = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "[]")

    def test_no_hardware_api_is_called_by_any_scenario_or_replay(self):
        targets = [("app.tuya.relay_tuya_controller", "RelayTuyaController",
                    ("switch_on", "switch_off", "switch_on_device", "switch_off_device", "switch_binary", "switch_device")),
                   ("app.control.easun_output_actuator", "EASUNOutputActuator", None),
                   ("app.control.easun_output_actuator", "FakeDessTransport", ("write_setting",)),
                   ("app.api", "DessAPI", ("ctrl_device", "set_charger_source"))]
        patches, spied = [], []
        for module_name, class_name, methods in targets:
            try:
                cls = getattr(importlib.import_module(module_name), class_name)
            except Exception:  # noqa: BLE001 - optional dependency missing in this interpreter
                continue
            names = methods or [n for n in vars(cls) if not n.startswith("_") and callable(getattr(cls, n))]
            for name in names:
                p = patch.object(cls, name, side_effect=AssertionError(f"{class_name}.{name} called"))
                spied.append((p.start(), f"{class_name}.{name}"))
                patches.append(p)
        self.addCleanup(lambda: [p.stop() for p in patches])
        self.assertTrue(spied, "at least one hardware writer must be spied")
        suite = unittest.TestSuite()
        for case in (EligibilityAndCentralInvariant, SolarDemandAndVoltage, ChargerReadback, TelemetryFaults,
                     TrialsLockoutAndRestart, PrecedenceAndInvariants, Replay):
            suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(case))
        result = unittest.TestResult()
        suite.run(result)
        self.assertEqual(result.errors + result.failures, [])
        self.assertEqual([name for mock, name in spied if mock.called], [])


if __name__ == "__main__":
    unittest.main()
