"""Offline Task 0080 shadow coordination tests; no hardware access."""
import ast
import inspect
import unittest
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

from app.control.grid_charge_output_coordination import (
    CoordinationState, ReceiptObservation, SettingObservation, evaluate_coordination,
)
from app.control.adaptive_output_policy import OutputPolicyReason as OPR
from app.control.grid_output_interlock import InterlockBaseline, InterlockState as S, StateSource, evaluate_interlock
from app.control.winter_energy_intelligence import InterlockEvaluationInputs
from test_grid_output_interlock import (
    BASELINE, NOON, PERSISTED, TEST_LIMITS, eligible_history, policy, readback, series, snapshot,
)


class CoordinationTests(unittest.TestCase):
    def setUp(self):
        points = eligible_history()
        self.snap = snapshot(points[-1])
        self.inputs = InterlockEvaluationInputs(self.snap, BASELINE, tuple(points[:-1]), PERSISTED)
        self.setting = SettingObservation('SBU', NOON, True, True)

    def run_decision(self, inputs=None, **kwargs):
        inputs = inputs or self.inputs
        options = dict(evaluated_at=NOON, priority=self.setting, persistence_available=True)
        options.update(kwargs)
        return evaluate_coordination(inputs.snapshot.telemetry, inputs, **options)

    def recovery(self, *, emergency=False, voltage=26.0):
        t = replace(self.snap.telemetry, battery_voltage=voltage, battery_current_chg=15.0)
        state = 'emergency_recovery' if emergency else 'long_night_topup'
        s = replace(self.snap, telemetry=t, charger=readback('solar_and_mains'),
                    policy=policy(state, 'solar_and_mains', emergency=emergency))
        return replace(self.inputs, snapshot=s)

    def observation(self, seconds, mode, *, recovery=False, fingerprint=None, received=None):
        at = NOON + timedelta(seconds=seconds)
        t = replace(self.snap.telemetry, observed_at=at, working_mode=mode)
        charger = readback('solar_and_mains' if recovery else 'solar_only', age_s=0, at=at)
        return ReceiptObservation(replace(self.snap, telemetry=t, charger=charger),
                                  at if received is None else received, fingerprint or str(seconds))

    def test_charging_voltage_and_sbu_never_authorize_return(self):
        for voltage in (25.5, 26.0, 27.0, 28.0):
            with self.subTest(voltage=voltage):
                d = self.run_decision(self.recovery(voltage=voltage))
                self.assertEqual(d.state, CoordinationState.GRID_HOLD)
                self.assertEqual(d.desired_profile, 'Utility')
                self.assertIn('GRID_CHARGING_ACTIVE_WITH_SBU', d.reason_codes)
                self.assertFalse(d.command_issued)
                self.assertFalse(d.physical_transition_proven)

    def test_emergency_has_precedence(self):
        d = self.run_decision(self.recovery(emergency=True), persistence_available=False)
        self.assertEqual(d.state, CoordinationState.EMERGENCY_RECOVERY)

    def test_improving_solar_and_recovery_completion_do_not_prove_reserve(self):
        for pv in (0.0, 100.0, 1000.0):
            s = replace(self.snap, telemetry=replace(self.snap.telemetry, pv_power_w=pv))
            i = replace(self.inputs, snapshot=s, durable_state=replace(PERSISTED, recovery_in_progress=True))
            d = self.run_decision(i)
            self.assertEqual(d.desired_profile, 'Utility')
            self.assertNotIn(d.state.value, ('SOLAR_OPERATION', 'SOLAR_RETURN_CANDIDATE'))
            self.assertIsNotNone(d.winter)

    def test_mismatched_snapshot_and_untrusted_reported_decisions(self):
        other = replace(self.snap.telemetry)
        d = evaluate_coordination(other, self.inputs, evaluated_at=NOON, priority=self.setting,
                                  persistence_available=True, reported_interlock=object(), reported_winter=object())
        self.assertEqual(d.state, CoordinationState.FAULT_HOLD)
        self.assertIn('INTERLOCK_SNAPSHOT_MISMATCH', d.reason_codes)
        self.assertIsNone(d.interlock)

    def test_setting_missing_stale_future_and_storage_fail_closed(self):
        for setting in (SettingObservation(), replace(self.setting, observed_at=NOON-timedelta(hours=1)),
                        replace(self.setting, observed_at=NOON+timedelta(seconds=1)),
                        replace(self.setting, catalogue_verified=False),
                        replace(self.setting, readback_verified=False), replace(self.setting, value='unknown')):
            with self.subTest(setting=setting):
                self.assertEqual(self.run_decision(priority=setting).state, CoordinationState.FAULT_HOLD)
        self.assertEqual(self.run_decision(persistence_available=False).state, CoordinationState.FAULT_HOLD)

    def test_utility_needs_no_voltage_candidate(self):
        d = self.run_decision(priority=replace(self.setting, value='Utility'))
        self.assertEqual(d.voltage_candidate, 'NOT_REQUIRED_WHILE_UTILITY')
        self.assertEqual(self.run_decision().voltage_candidate, 'RESEARCH_ONLY_28.0V_NOT_EXECUTABLE')

    def test_preserved_lockout_and_manual_hold(self):
        i = replace(self.inputs, durable_state=replace(PERSISTED, lockout_expires_at=NOON+timedelta(minutes=30)))
        d = self.run_decision(i, manual_utility_hold=True)
        self.assertEqual(d.state, CoordinationState.CHURN_LOCKOUT)
        self.assertEqual(d.lockout_expires_at, NOON+timedelta(minutes=30))
        self.assertIn('MANUAL_UTILITY_HOLD', d.reason_codes)

    def test_unavailable_restart_and_unknown_telemetry(self):
        i = replace(self.inputs, durable_state=replace(PERSISTED, source=StateSource.UNAVAILABLE))
        self.assertEqual(self.run_decision(i).state, CoordinationState.FAULT_HOLD)
        s = replace(self.snap, telemetry=replace(self.snap.telemetry, observed_at=None))
        self.assertEqual(self.run_decision(replace(self.inputs, snapshot=s)).state, CoordinationState.FAULT_HOLD)

    def test_bound_winter_call_recomputes_raw_inputs(self):
        from app.control import grid_charge_output_coordination as module
        original = module.evaluate_winter_intelligence
        with patch.object(module, 'evaluate_winter_intelligence', wraps=original) as spy:
            self.run_decision(reported_interlock=object(), reported_winter=object())
            self.assertIsNone(spy.call_args.kwargs['interlock'])
            self.assertIs(spy.call_args.kwargs['interlock_inputs'], self.inputs)
            self.assertIs(spy.call_args.args[0], self.inputs.snapshot.telemetry)

    def test_observed_recovery_invert_line_cycle_and_duplicates(self):
        trace = (self.observation(-180, 'Line Mode', recovery=True),
                 self.observation(-120, 'Invert Mode'), self.observation(-60, 'Line Mode'))
        d = self.run_decision(receipt_history=trace)
        self.assertEqual(d.recovery_cycles, 1)
        self.assertFalse(d.continuity_uncertain)
        duplicated = (trace[0], replace(trace[1], fingerprint=trace[0].fingerprint), trace[2])
        self.assertEqual(self.run_decision(receipt_history=duplicated).recovery_cycles, 0)

    def test_unknown_and_out_of_order_break_cycle(self):
        for trace in ((self.observation(-180, 'Line Mode', recovery=True), self.observation(-120, None),
                       self.observation(-90, 'Invert Mode'), self.observation(-60, 'Line Mode')),
                      (self.observation(-180, 'Line Mode', recovery=True), self.observation(-60, 'Invert Mode'),
                       self.observation(-90, 'Line Mode'))):
            d = self.run_decision(receipt_history=trace)
            self.assertEqual(d.recovery_cycles, 0)
            self.assertTrue(d.continuity_uncertain)
            self.assertEqual(d.state, CoordinationState.FAULT_HOLD)

    def test_measurement_and_receipt_gaps_bound_continuity(self):
        base = replace(BASELINE.output, mode_continuity_max_gap_seconds=600)
        i = replace(self.inputs, baseline=InterlockBaseline(output=base))
        for gap, count in ((599, 1), (600, 1), (601, 0)):
            trace = (self.observation(-gap-60, 'Line Mode', recovery=True),
                     self.observation(-60, 'Invert Mode'), self.observation(0, 'Line Mode'))
            self.assertEqual(self.run_decision(i, receipt_history=trace).recovery_cycles, count)
        trace = (self.observation(-420, 'Line Mode', recovery=True),
                 self.observation(-60, 'Invert Mode'), self.observation(0, 'Line Mode'))
        self.assertEqual(self.run_decision(receipt_history=trace).recovery_cycles, 0)

    def test_measurement_gap_and_window_inclusive_boundaries(self):
        for gap, expected in ((299, 1), (300, 1), (301, 0)):
            trace = (self.observation(-gap-60, 'Line Mode', recovery=True),
                     self.observation(-60, 'Invert Mode'), self.observation(0, 'Line Mode'))
            self.assertEqual(self.run_decision(receipt_history=trace).recovery_cycles, expected)
        for oldest, expected in ((-3599, 1), (-3600, 1), (-3601, 0)):
            trace = (self.observation(oldest, 'Line Mode', recovery=True),
                     self.observation(oldest+60, 'Invert Mode'), self.observation(oldest+120, 'Line Mode'))
            self.assertEqual(self.run_decision(receipt_history=trace).recovery_cycles, expected)
        # Receipt age of the first sample is gap-600 (300/301 s); raise the freshness
        # limit explicitly so only max_telemetry_gap_seconds decides this case.
        base = replace(BASELINE.output, mode_continuity_max_gap_seconds=1000, telemetry_max_age_seconds=301)
        i = replace(self.inputs, baseline=InterlockBaseline(output=base))
        # Keep receipt gaps within 600 while measurement gap reaches its own limit.
        for gap, expected in ((899, 1), (900, 1), (901, 0)):
            first = self.observation(-gap-60, 'Line Mode', recovery=True,
                                     received=NOON-timedelta(seconds=660))
            first = replace(first, snapshot=replace(first.snapshot, charger=replace(
                first.snapshot.charger, observed_at=first.received_at)))
            middle = self.observation(-60, 'Invert Mode', received=NOON-timedelta(seconds=60))
            self.assertEqual(self.run_decision(i, receipt_history=(first, middle,
                self.observation(0, 'Line Mode'))).recovery_cycles, expected)

    def test_immutable_safety_markers_cannot_be_overridden(self):
        d = self.run_decision()
        with self.assertRaises(ValueError):
            replace(d, command_issued=True)
        with self.assertRaises(ValueError):
            replace(d, physical_transition_proven=True)

    def test_synthetic_false_reserve_fixture_is_not_authority(self):
        from test_winter_energy_intelligence import WinterEnergyIntelligenceTests
        fixture = WinterEnergyIntelligenceTests()
        fixture.setUp()
        context = replace(fixture.context, independent_reserve_evidence=fixture.reserve_contract(synthetic=False))
        d = self.run_decision(winter_context=context, energy=fixture.energy, bands=fixture.bands)
        self.assertNotEqual(d.winter.state.value, 'BATTERY_USE_ELIGIBLE')
        self.assertEqual(d.desired_profile, 'Utility')

    def test_repeated_cycles_use_existing_lockout(self):
        from test_grid_output_interlock import series
        points = series(NOON - timedelta(minutes=12), [
            ('Line Mode', 2, 100, 700, 26, 10, 0), ('Invert Mode', 2, 100, 700, 25.5, 0, 15),
            ('Line Mode', 2, 100, 700, 26, 10, 0), ('Invert Mode', 2, 100, 700, 25.5, 0, 15),
            ('Line Mode', 4, 100, 700, 26, 10, 0)])
        s = snapshot(points[-1])
        inputs = replace(self.inputs, snapshot=s, recent_history=tuple(points[:-1]))
        trace = tuple(ReceiptObservation(replace(s, telemetry=p, charger=readback(
            'solar_and_mains' if p.working_mode == 'Line Mode' else 'solar_only', age_s=0,
            at=p.observed_at)), p.observed_at, str(n)) for n, p in enumerate(points))
        d = self.run_decision(inputs, receipt_history=trace)
        self.assertEqual(d.recovery_cycles, 2)
        self.assertEqual(d.state, CoordinationState.CHURN_LOCKOUT)
        self.assertIsNotNone(d.lockout_expires_at)

    def test_invalid_history_times_and_unverified_charger_cannot_create_cycle(self):
        from app.control.grid_output_interlock import ChargerProvenance
        first = self.observation(-180, 'Line Mode', recovery=True)
        invert = self.observation(-120, 'Invert Mode')
        line = self.observation(-60, 'Line Mode')
        bad_charger = replace(first.snapshot.charger, provenance=ChargerProvenance.LAST_COMMAND_CACHE)
        cases = ((replace(first, snapshot=replace(first.snapshot, charger=bad_charger)), invert, line),
                 (first, replace(invert, received_at=first.received_at), line),
                 (first, replace(invert, snapshot=replace(invert.snapshot,
                     telemetry=replace(invert.snapshot.telemetry, observed_at=None))), line))
        for trace in cases:
            self.assertEqual(self.run_decision(receipt_history=trace).recovery_cycles, 0)

    def test_conflicting_setting_and_duplicate_payload_are_uncertain(self):
        s = replace(self.snap, output_priority_raw='Utility')
        self.assertEqual(self.run_decision(replace(self.inputs, snapshot=s)).state, CoordinationState.FAULT_HOLD)
        first = self.observation(-180, 'Line Mode', recovery=True)
        invert = self.observation(-120, 'Invert Mode', fingerprint=first.fingerprint)
        d = self.run_decision(receipt_history=(first, invert, self.observation(-60, 'Line Mode')))
        self.assertTrue(d.continuity_uncertain)
        self.assertEqual(d.recovery_cycles, 0)

    def test_no_hardware_or_runtime_imports(self):
        from app.control import grid_charge_output_coordination as module
        tree = ast.parse(inspect.getsource(module))
        imports = [node.module or '' for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        self.assertFalse(any(name in ('app.api', 'run', 'service.inverter_monitor')
                             or 'tuya' in name or 'actuator' in name for name in imports))
        calls = {node.func.attr for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        self.assertTrue(calls.isdisjoint({'ctrl_device', 'set_charger_source', 'switch_on_device', 'switch_off_device'}))


    # ── Precommit blocker 1: freshness at receipt, not ordering/spacing ──
    def test_stale_arrivals_cannot_create_a_recovery_cycle(self):
        # Reviewer reproduction: measured 30/29/28 min ago, received 2/1/0 min ago.
        trace = tuple(self.observation(-60 * measured, mode, recovery=mode == 'Line Mode' and n == 0,
                                       received=NOON - timedelta(minutes=received))
                      for n, (measured, received, mode) in enumerate(
                          ((30, 2, 'Line Mode'), (29, 1, 'Invert Mode'), (28, 0, 'Line Mode'))))
        trace = (replace(trace[0], snapshot=replace(trace[0].snapshot, charger=replace(
            trace[0].snapshot.charger, observed_at=trace[0].received_at))),) + trace[1:]
        d = self.run_decision(receipt_history=trace)
        self.assertEqual(d.recovery_cycles, 0)
        self.assertTrue(d.continuity_uncertain)
        self.assertIn('OBSERVATION_CONTINUITY_UNCERTAIN', d.reason_codes)
        self.assertEqual(d.state, CoordinationState.FAULT_HOLD)

    def test_receipt_age_boundary_uses_telemetry_max_age(self):
        # Measurement times stay fixed and ordered; one sample at a time arrives `age` late.
        limit = int(BASELINE.output.telemetry_max_age_seconds)  # 180, inclusive
        for position in range(3):
            for age, count in ((limit - 1, 1), (limit, 1), (limit + 1, 0)):
                with self.subTest(position=position, age=age):
                    trace = [self.observation(-900, 'Line Mode', recovery=True),
                             self.observation(-600, 'Invert Mode'), self.observation(-300, 'Line Mode')]
                    item = trace[position]
                    late = item.received_at + timedelta(seconds=age)
                    # Keep readback age at receipt at zero so only telemetry receipt age varies.
                    trace[position] = replace(item, received_at=late, snapshot=replace(
                        item.snapshot, charger=replace(item.snapshot.charger, observed_at=late)))
                    d = self.run_decision(receipt_history=tuple(trace))
                    self.assertEqual(d.recovery_cycles, count)
                    self.assertEqual(d.continuity_uncertain, count == 0)

    def test_stale_arrival_breaks_a_partial_sequence(self):
        # A fresh recovery and Invert followed by a stale Line must not close the cycle,
        # and a later fresh Line cannot complete the broken sequence either.
        # Measurement and receipt order and gaps are all valid; only the receipt age (200 s) is not.
        stale_line = self.observation(-480, 'Line Mode', received=NOON - timedelta(seconds=280))
        trace = (self.observation(-600, 'Line Mode', recovery=True), self.observation(-540, 'Invert Mode'),
                 stale_line, self.observation(-240, 'Line Mode'))
        d = self.run_decision(receipt_history=trace)
        self.assertEqual(d.recovery_cycles, 0)
        self.assertTrue(d.continuity_uncertain)

    def test_recovery_readback_age_boundary_at_cycle_start(self):
        limit = int(BASELINE.output.telemetry_max_age_seconds)
        for age, count in ((limit - 1, 1), (limit, 1), (limit + 1, 0)):
            with self.subTest(age=age):
                first = self.observation(-180, 'Line Mode', recovery=True)
                first = replace(first, snapshot=replace(first.snapshot, charger=replace(
                    first.snapshot.charger, observed_at=first.received_at - timedelta(seconds=age))))
                trace = (first, self.observation(-120, 'Invert Mode'), self.observation(-60, 'Line Mode'))
                self.assertEqual(self.run_decision(receipt_history=trace).recovery_cycles, count)

    # ── Precommit blocker 2: required winter authority failure is FAULT_HOLD ──
    def test_winter_evaluator_failure_is_fault_hold(self):
        from app.control import grid_charge_output_coordination as module
        for effect in (ValueError('winter'), RuntimeError('winter'), KeyError('winter')):
            with self.subTest(effect=type(effect).__name__), \
                    patch.object(module, 'evaluate_winter_intelligence', side_effect=effect):
                d = self.run_decision()
                self.assertEqual(d.state, CoordinationState.FAULT_HOLD)
                self.assertIsNone(d.winter)
                self.assertIsNotNone(d.interlock)
                self.assertIn('WINTER_EVALUATION_UNAVAILABLE', d.reason_codes)
                self.assertIn('AUTHORITY_EVALUATION_UNAVAILABLE', d.reason_codes)
                self.assertEqual(d.desired_profile, 'Utility')
                self.assertFalse(d.command_issued)
        with patch.object(module, 'evaluate_winter_intelligence', return_value=object()):
            d = self.run_decision()
            self.assertEqual(d.state, CoordinationState.FAULT_HOLD)
            self.assertIsNone(d.winter)
        self.assertEqual(self.run_decision().state, CoordinationState.GRID_HOLD)

    def test_winter_failure_keeps_emergency_and_active_recovery(self):
        from app.control import grid_charge_output_coordination as module
        emergency, recovery = self.recovery(emergency=True), self.recovery()
        snapshots = (emergency.snapshot, recovery.snapshot)
        with patch.object(module, 'evaluate_winter_intelligence', side_effect=ValueError('winter')):
            d = self.run_decision(emergency)
            self.assertEqual(d.state, CoordinationState.EMERGENCY_RECOVERY)
            self.assertEqual(d.interlock.state.value, 'GRID_RECOVERY_ACTIVE')
            self.assertIn('WINTER_EVALUATION_UNAVAILABLE', d.reason_codes)
            r = self.run_decision(recovery)
            self.assertEqual(r.state, CoordinationState.FAULT_HOLD)
            self.assertEqual(r.interlock.state.value, 'GRID_RECOVERY_ACTIVE')
            self.assertEqual(r.observed_charger, 'solar_and_mains')
        # Inputs that carry the charging controller status are untouched.
        self.assertEqual((emergency.snapshot, recovery.snapshot), snapshots)
        self.assertEqual(emergency.snapshot.policy.state, 'emergency_recovery')


class BoundaryMatrixTests(unittest.TestCase):
    """Below / at / above each delegated Task 0076 boundary, through the coordinator.

    The fixture data stay fixed and the supplied baseline limit is moved by one
    second (or one count), so the measured value is exactly limit+1, limit and
    limit-1. The coordinator must report what the unchanged interlock decides.
    """

    def decide(self, points, limits=TEST_LIMITS, *, at=NOON, durable=PERSISTED):
        s = snapshot(points[-1], at=at)
        inputs = InterlockEvaluationInputs(s, InterlockBaseline(output=limits), tuple(points[:-1]), durable)
        d = evaluate_coordination(s.telemetry, inputs, evaluated_at=at, persistence_available=True,
                                  priority=SettingObservation('SBU', at, True, True))
        direct = evaluate_interlock(s, InterlockBaseline(output=limits), tuple(points[:-1]), durable, evaluated_at=at)
        self.assertEqual((d.interlock.state, d.lockout_expires_at), (direct.state, direct.lockout_expires_at))
        self.assertEqual(d.desired_profile, 'Utility')
        self.assertNotIn(d.state, (CoordinationState.SOLAR_RETURN_CANDIDATE, CoordinationState.SOLAR_OPERATION))
        return d

    @staticmethod
    def churn():  # mode changes at 11:50, 11:52, 11:54, 11:56; last sample 11:59
        return series(NOON - timedelta(minutes=12), [
            ('Line Mode', 2, 100, 700, 26, 10, 0), ('Invert Mode', 2, 100, 700, 25.5, 0, 15),
            ('Line Mode', 2, 100, 700, 26, 10, 0), ('Invert Mode', 2, 100, 700, 25.5, 0, 15),
            ('Line Mode', 4, 100, 700, 26, 10, 0)])

    @staticmethod
    def failed_trials(count):
        """Line, then `count` 300 s Invert trials that sag 0.4 V in the first minute."""
        sag = lambda i: 26.0 if i == 0 else 25.6
        segments = [('Line Mode', 20, 900, 600, 26.2, 0.0, 0.0)]
        for _ in range(count):
            segments += [('Invert Mode', 6, 900, 600, sag, 0.0, 10.0), ('Line Mode', 10, 900, 600, 26.2, 0.0, 0.0)]
        segments[-1] = ('Line Mode', 14 if count == 1 else 18, 900, 600, 26.2, 0.0, 0.0)
        minutes = sum(segment[1] for segment in segments)
        return series(NOON - timedelta(minutes=minutes), segments)

    def test_min_battery_dwell_seconds(self):
        points = eligible_history()  # completed loaded Invert segment lasts 2340 s
        for limit, eligible in ((2339, True), (2340, True), (2341, False)):
            with self.subTest(limit=limit):
                d = self.decide(points, replace(TEST_LIMITS, min_battery_dwell_seconds=limit))
                self.assertEqual(d.interlock.state is S.BATTERY_RETURN_ELIGIBLE, eligible)
                self.assertEqual(any(e.name == 'reserve_evidence' for e in d.interlock.evidence), eligible)
                self.assertEqual(d.state, CoordinationState.GRID_HOLD)

    def test_min_mains_dwell_seconds(self):
        points = eligible_history()  # current Line segment has dwelt 2940 s
        for limit, proven in ((2939, True), (2940, True), (2941, False)):
            with self.subTest(limit=limit):
                d = self.decide(points, replace(TEST_LIMITS, min_mains_dwell_seconds=limit))
                self.assertEqual(OPR.MAINS_DWELL_UNPROVEN not in d.interlock.base_reason_codes, proven)
                self.assertEqual(d.interlock.state is S.BATTERY_RETURN_ELIGIBLE, proven)
                self.assertEqual(d.state, CoordinationState.GRID_HOLD)

    def test_post_transition_settle_seconds(self):
        # Current 1140 s Invert segment; the last three samples (from +1020 s) sag 0.2 V/min.
        late = lambda i: 26.0 if i < 17 else 26.0 - 0.2 * (i - 16)
        points = series(NOON - timedelta(minutes=60), [('Line Mode', 40, 900, 600, 26.2, 0.0, 0.0),
                                                       ('Invert Mode', 20, 900, 600, late, 0.0, 10.0)])
        for limit, sag_seen in ((1019, True), (1020, True), (1021, False)):
            with self.subTest(limit=limit):
                d = self.decide(points, replace(TEST_LIMITS, post_transition_settle_seconds=limit))
                self.assertEqual(OPR.FAST_VOLTAGE_SAG in d.interlock.base_reason_codes, sag_seen)
                self.assertEqual(d.interlock.state, S.BATTERY_TRIAL)

    def test_failed_trial_max_seconds(self):
        points = self.failed_trials(1)  # 300 s trial ends 11:45
        for limit, failed in ((299, False), (300, True), (301, True)):
            with self.subTest(limit=limit):
                d = self.decide(points, replace(TEST_LIMITS, failed_trial_max_seconds=limit))
                self.assertEqual(d.state is CoordinationState.CHURN_LOCKOUT, failed)
                self.assertEqual(d.lockout_expires_at,
                                 NOON + timedelta(minutes=45) if failed else None)

    def test_failed_trial_lockout_duration_and_expiry(self):
        points = self.failed_trials(1)  # failed trial ended 900 s before NOON
        for duration, locked in ((899, False), (900, False), (901, True)):
            with self.subTest(duration=duration):
                d = self.decide(points, replace(TEST_LIMITS, failed_trial_lockout_seconds=duration))
                self.assertEqual(d.state is CoordinationState.CHURN_LOCKOUT, locked)
                self.assertEqual(d.lockout_expires_at, NOON + timedelta(seconds=1) if locked else None)

    def test_max_failed_trial_escalation(self):
        points = self.failed_trials(2)  # two failed trials; the latest ends 11:41
        limits = replace(TEST_LIMITS, max_mode_changes=10)  # isolate escalation from churn
        for escalation, hours in ((1, 1), (2, 2), (3, 2)):
            with self.subTest(escalation=escalation):
                d = self.decide(points, replace(limits, max_failed_trial_escalation=escalation))
                self.assertEqual(d.state, CoordinationState.CHURN_LOCKOUT)
                self.assertEqual(d.lockout_expires_at, NOON - timedelta(minutes=19) + timedelta(hours=hours))

    def test_max_mode_changes(self):
        for limit, locked in ((3, True), (4, True), (5, False)):
            with self.subTest(limit=limit):
                d = self.decide(self.churn(), replace(TEST_LIMITS, max_mode_changes=limit))
                self.assertEqual(d.mode_changes, 4)
                self.assertEqual(d.state is CoordinationState.CHURN_LOCKOUT, locked)

    def test_mode_change_window_seconds_for_churn(self):
        # The 4th change (11:56) counts the 1st (11:50) only inside a 360 s window.
        for window, locked in ((359, False), (360, True), (361, True)):
            with self.subTest(window=window):
                d = self.decide(self.churn(), replace(TEST_LIMITS, mode_change_window_seconds=window))
                self.assertEqual(d.state is CoordinationState.CHURN_LOCKOUT, locked)

    def test_churn_lockout_duration_and_expiry(self):
        # Lockout starts at the 4th change, 240 s before NOON; expiry is exclusive.
        for duration, locked in ((239, False), (240, False), (241, True)):
            with self.subTest(duration=duration):
                d = self.decide(self.churn(), replace(TEST_LIMITS, churn_lockout_seconds=duration))
                self.assertEqual(d.state is CoordinationState.CHURN_LOCKOUT, locked)
                self.assertEqual(d.lockout_expires_at, NOON + timedelta(seconds=1) if locked else None)

    def test_preserved_lockout_expiry(self):
        for offset, locked in ((-1, False), (0, False), (1, True)):
            with self.subTest(offset=offset):
                expires = NOON + timedelta(seconds=offset)
                d = self.decide(eligible_history(), durable=replace(PERSISTED, lockout_expires_at=expires))
                self.assertEqual(d.state is CoordinationState.CHURN_LOCKOUT, locked)
                self.assertEqual(d.lockout_expires_at, expires if locked else None)

    def test_current_telemetry_age_limit(self):
        points = eligible_history()  # last sample 11:59
        for age, fresh in ((179, True), (180, True), (181, False)):
            with self.subTest(age=age):
                d = self.decide(points, at=points[-1].observed_at + timedelta(seconds=age))
                self.assertEqual(d.state, CoordinationState.GRID_HOLD if fresh else CoordinationState.FAULT_HOLD)
                self.assertEqual('STALE_TELEMETRY' in d.reason_codes, not fresh)

    def test_output_setting_age_limit(self):
        s = snapshot(eligible_history()[-1])
        inputs = InterlockEvaluationInputs(s, BASELINE, tuple(eligible_history()[:-1]), PERSISTED)
        for age, valid in ((179, True), (180, True), (181, False)):
            with self.subTest(age=age):
                setting = SettingObservation('SBU', NOON - timedelta(seconds=age), True, True)
                d = evaluate_coordination(s.telemetry, inputs, evaluated_at=NOON, priority=setting,
                                          persistence_available=True)
                self.assertEqual(d.state, CoordinationState.GRID_HOLD if valid else CoordinationState.FAULT_HOLD)
                self.assertEqual('OUTPUT_SETTING_UNVERIFIED' in d.reason_codes, not valid)


if __name__ == '__main__':
    unittest.main()
