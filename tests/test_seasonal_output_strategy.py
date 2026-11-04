import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from app.control.adaptive_output_policy import OutputDecision
from app.control.seasonal_output_strategy import (
    ApprovedBands, DecisionJournal, EnergyInputs, ReportedWrite, Strategy, WriteOutcome, evaluate_seasonal_strategy,
)
from test_adaptive_output_policy import BASE, baseline, established_line_history, sample


class SeasonalTests(unittest.TestCase):
    def setUp(self):
        self.baseline = baseline(max_battery_charge_a=60.0)
        self.snapshot = sample(discharge=0.0, pv=800.0, output=500.0)
        self.history = established_line_history()
        self.bands = ApprovedBands(24.0, 25.0, 25.5, 27.0, 0.4, 0.5)
        self.energy = EnergyInputs(expected_pv_wh=6000.0, forecast_issued_at=BASE-timedelta(minutes=5),
                                   forecast_valid_through=self.snapshot.sunset, forecast_confidence=0.9,
                                   usable_battery_wh=9000.0, battery_energy_observed_at=BASE,
                                   battery_energy_verified=True, reserve_wh=1000.0,
                                   minimum_confidence=0.75, max_energy_age_seconds=180, proposal_ttl_seconds=600)

    def evaluate(self, snapshot=None, config=None, energy='default', bands='default', history=None):
        return evaluate_seasonal_strategy(snapshot or self.snapshot, config or self.baseline,
            self.history if history is None else history, evaluated_at=BASE,
            energy=self.energy if energy == 'default' else energy, bands=self.bands if bands == 'default' else bands)

    def test_sunny_and_cloudy_conditions_adjust_only_within_approved_bands(self):
        sunny = self.evaluate()
        self.assertEqual(sunny.strategy, Strategy.SOLAR_OPPORTUNITY)
        self.assertEqual(sunny.policy_decision, OutputDecision.CONSIDER)
        self.assertEqual((sunny.proposed_utility_v, sunny.proposed_battery_v), (24.5, 25.5))
        self.assertEqual(sunny.required_reserve_wh, 1000.0)
        self.assertEqual(sunny.expires_at, BASE + timedelta(seconds=180))
        winter = self.evaluate(energy=replace(self.energy, expected_pv_wh=100.0, usable_battery_wh=3000.0))
        self.assertEqual(winter.strategy, Strategy.CONSERVE)
        self.assertIn('RESERVE_DEFICIT', winter.reasons)
        self.assertAlmostEqual(winter.proposed_utility_v, 24.9)
        self.assertAlmostEqual(winter.proposed_battery_v, 26.0)
        self.assertGreaterEqual(winter.proposed_battery_v-winter.proposed_utility_v, self.baseline.min_hysteresis_v)
        self.assertFalse(winter.command_executed)

    def test_changing_conditions_and_sunset_reserve_are_conservative(self):
        self.assertNotEqual(self.evaluate().strategy, self.evaluate(energy=replace(self.energy, expected_pv_wh=0, usable_battery_wh=100)).strategy)
        late = replace(self.snapshot, sunset=BASE+timedelta(minutes=30))
        result = self.evaluate(snapshot=late, energy=replace(self.energy, forecast_valid_through=late.sunset))
        self.assertEqual(result.strategy, Strategy.CONSERVE)
        self.assertIn('POLICY_NOT_ELIGIBLE', result.reasons)
        self.assertIsNone(result.proposed_utility_v)

    def test_illustrative_summer_and_winter_are_condition_driven(self):
        for now, remaining, clouds, expected, available, wanted in (
            (datetime(2026, 7, 5, 9, tzinfo=timezone.utc), 10, 10, 6000, 9000, Strategy.SOLAR_OPPORTUNITY),
            (datetime(2026, 12, 15, 12, tzinfo=timezone.utc), 3, 90, 100, 1000, Strategy.CONSERVE),
        ):
            shift = now - BASE
            def move(point):
                forecast = replace(point.forecast, observed_at=point.forecast.observed_at + shift,
                                   valid_at=point.forecast.valid_at + shift, clouds_pct=clouds)
                return replace(point, observed_at=point.observed_at + shift,
                               sunrise=now-timedelta(hours=4), sunset=now+timedelta(hours=remaining), forecast=forecast)
            snapshot = move(self.snapshot)
            history = tuple(move(point) for point in self.history)
            energy = replace(self.energy, expected_pv_wh=expected, usable_battery_wh=available,
                             forecast_issued_at=now-timedelta(minutes=5), forecast_valid_through=snapshot.sunset,
                             battery_energy_observed_at=now)
            decision = evaluate_seasonal_strategy(snapshot, self.baseline, history, evaluated_at=now,
                                                   energy=energy, bands=self.bands)
            self.assertEqual(decision.strategy, wanted)
            self.assertFalse(decision.command_executed)

    def test_unknown_or_stale_evidence_never_proposes_thresholds(self):
        variants = [None, replace(self.energy, battery_energy_verified=False),
                    replace(self.energy, forecast_confidence=0.3),
                    replace(self.energy, forecast_issued_at=BASE-timedelta(days=1)),
                    replace(self.energy, forecast_valid_through=BASE),
                    replace(self.energy, usable_battery_wh=float('nan')),
                    replace(self.energy, battery_energy_observed_at=BASE-timedelta(hours=1)),
                    replace(self.energy, proposal_ttl_seconds=None)]
        for energy in variants:
            with self.subTest(energy=energy):
                result = self.evaluate(energy=energy)
                self.assertEqual(result.strategy, Strategy.CONSERVE)
                self.assertIsNone(result.proposed_utility_v)
        for config in (baseline(max_battery_discharge_a=None, max_battery_charge_a=60),
                       baseline(max_battery_charge_a=None), baseline(max_battery_charge_a=0)):
            result = self.evaluate(config=config)
            self.assertIn('HARDWARE_LIMITS_UNAPPROVED', result.reasons)
            self.assertIsNone(result.proposed_battery_v)
        self.assertIn('PROPOSAL_BOUNDS_UNAPPROVED', self.evaluate(bands=None).reasons)

    def test_weather_does_not_override_voltage_sag_or_incomplete_history(self):
        low = self.evaluate(snapshot=sample(voltage=24.0, discharge=0.0))
        self.assertEqual(low.strategy, Strategy.CONSERVE)
        self.assertEqual(low.policy_decision, OutputDecision.BLOCK)
        self.assertIsNone(low.proposed_battery_v)
        incomplete = self.evaluate(history=())
        self.assertIn('POLICY_NOT_ELIGIBLE', incomplete.reasons)
        self.assertIsNone(incomplete.proposed_battery_v)

    def test_failed_trial_lockout_overrides_sunny_forecast(self):
        history = []
        for minutes in range(482, 0, -1):
            mode = 'Invert Mode' if 2 <= minutes <= 5 else 'Line Mode'
            voltage = {5: 25.9, 4: 25.7, 3: 25.5, 2: 25.4}.get(minutes, 25.9)
            history.append(sample(BASE-timedelta(minutes=minutes), mode=mode, voltage=voltage,
                                  discharge=19 if mode == 'Invert Mode' else 0))
        result = self.evaluate(history=tuple(history))
        self.assertEqual(result.strategy, Strategy.CONSERVE)
        self.assertIn('POLICY_FAST_VOLTAGE_SAG', result.reasons)
        self.assertIn('POLICY_COOLDOWN', result.reasons)
        self.assertIsNone(result.proposed_utility_v)

    def test_journal_bounded_and_no_write_claim(self):
        journal = DecisionJournal(2)
        a = self.evaluate(); b = self.evaluate(energy=replace(self.energy, expected_pv_wh=0, usable_battery_wh=10))
        journal.record(a); journal.record(b); journal.record(a)
        self.assertEqual(tuple(entry.recommendation for entry in journal.entries()), (b, a))
        journal.record_reported_write(ReportedWrite(BASE, 'illustrative_field', WriteOutcome.OUTCOME_UNCERTAIN))
        self.assertEqual([entry.kind for entry in journal.entries()], ['RECOMMENDATION', 'EXTERNAL_WRITE_REPORT'])
        self.assertEqual(journal.entries()[-1].reported_write.outcome, WriteOutcome.OUTCOME_UNCERTAIN)
        with self.assertRaises(ValueError): journal.record(replace(a, command_executed=True))
        with self.assertRaises(ValueError): journal.record(object())


if __name__ == '__main__':
    unittest.main()
