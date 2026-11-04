import json
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from app.control.adaptive_output_policy import OutputDecision, OutputPolicyReason, evaluate_output_policy
from app.control.sag_learning import LearningConfig, SagLearner, SagSample, policy_fingerprint
from test_adaptive_output_policy import BASE
import test_seasonal_output_strategy as seasonal_fixture


def configuration(**overrides):
    seasonal = seasonal_fixture.SeasonalTests(); seasonal.setUp()
    values = dict(context_id='test-installation-r1', policy_fingerprint=policy_fingerprint(seasonal.baseline, seasonal.bands),
                  min_pairs=3, stable_updates=2, max_pairs=10,
                  max_age_seconds=21600, max_pair_gap_seconds=600, max_duration_delta_seconds=60,
                  max_load_delta_w=100, max_discharge_delta_a=2, max_temperature_delta_c=2,
                  max_soc_delta_pct=5, max_uncertainty_v=.15, min_confidence=.6,
                  change_threshold_v=.3, max_adjustment_step_v=.05)
    values.update(overrides)
    return LearningConfig(**values)


def point(at, episode, voltage, *, load=1000, amps=15, mode='Invert Mode', charge=0,
          verified=True, temp=20, soc=70, received=None):
    return SagSample(at, received or at, episode, mode, voltage, load, amps, charge, verified, temp, soc)


def pair(learner, index, sag, *, start=BASE-timedelta(hours=2), load=1000, amps=15, temp=20, soc=70):
    at = start + timedelta(minutes=index*5)
    first = point(at, f'trial-{index}', 26.0, load=load, amps=amps, temp=temp, soc=soc)
    last = point(at+timedelta(minutes=3), f'trial-{index}', 26.0-sag, load=load, amps=amps, temp=temp, soc=soc)
    learner.observe(first, evaluated_at=at)
    return learner.observe(last, evaluated_at=last.observed_at)


class SagLearningTests(unittest.TestCase):
    def setUp(self):
        self.cfg = configuration()
        self.model = SagLearner(self.cfg)

    def warm(self):
        for i, drop in enumerate((.30, .32, .31, .30)):
            pair(self.model, i, drop)
        return self.model.estimate(evaluated_at=BASE)

    def test_confidence_range_and_gradual_convergence(self):
        for i, drop in enumerate((.30, .32, .31)):
            pair(self.model, i, drop)
        first = self.model.estimate(evaluated_at=BASE)
        self.assertFalse(first.usable)
        self.assertEqual(first.observation_count, 3)
        pair(self.model, 3, .30)
        stable = self.model.estimate(evaluated_at=BASE)
        self.assertTrue(stable.usable)
        self.assertAlmostEqual(stable.sag_v, .305)
        self.assertLess(stable.uncertainty_v, .02)
        self.assertEqual(stable.load_range_w, (1000, 1000))
        self.assertEqual(stable.temperature_range_c, (20, 20))
        self.assertEqual(stable.soc_range_pct, (70, 70))
        seasonal = seasonal_fixture.SeasonalTests(); seasonal.setUp()
        recommendation = seasonal.evaluate()
        proposal = self.model.propose(seasonal.snapshot, seasonal.baseline, seasonal.history, seasonal.energy,
                                     seasonal.bands, evaluated_at=BASE)
        self.assertEqual(proposal.status, 'SHADOW_PROPOSAL')
        self.assertAlmostEqual(proposal.battery_threshold_v, recommendation.proposed_battery_v + .05)
        self.assertLessEqual(proposal.battery_threshold_v, seasonal.bands.battery_max_v)
        pair(self.model, 4, .31)
        proposal2 = self.model.propose(seasonal.snapshot, seasonal.baseline, seasonal.history, seasonal.energy,
                                      seasonal.bands, evaluated_at=BASE)
        self.assertAlmostEqual(proposal2.battery_threshold_v - proposal.battery_threshold_v, .05)
        self.assertTrue(proposal2.shadow_only)
        self.assertFalse(proposal2.command_executed)
        changed_baseline = replace(seasonal.baseline, high_measured_output_w=1400)
        changed = self.model.propose(seasonal.snapshot, changed_baseline, seasonal.history, seasonal.energy,
                                     seasonal.bands, evaluated_at=BASE)
        self.assertEqual(changed.reason, 'POLICY_CONTEXT_CHANGED')

    def test_battery_removal_and_addition_signal_change_without_guessing_cause(self):
        self.assertTrue(self.warm().usable)
        self.assertEqual(pair(self.model, 4, .85), 'BEHAVIOR_CHANGED_CAUSE_UNKNOWN')
        self.assertFalse(self.model.estimate(evaluated_at=BASE).usable)
        for i, drop in enumerate((.84, .86, .85), 5): pair(self.model, i, drop)
        self.assertTrue(self.model.estimate(evaluated_at=BASE).usable)
        self.assertEqual(pair(self.model, 8, .20), 'BEHAVIOR_CHANGED_CAUSE_UNKNOWN')
        self.assertFalse(self.model.estimate(evaluated_at=BASE).usable)
        self.assertEqual(self.model.estimate(evaluated_at=BASE).observation_count, 1)

    def test_load_temperature_and_soc_changes_reset_context(self):
        self.assertTrue(self.warm().usable)
        self.assertEqual(pair(self.model, 4, .31, load=1300), 'CONTEXT_CHANGED')
        self.assertFalse(self.model.estimate(evaluated_at=BASE).usable)
        self.assertEqual(pair(self.model, 5, .31, load=1300, temp=26), 'CONTEXT_CHANGED')
        self.assertEqual(pair(self.model, 6, .31, load=1300, temp=26, soc=50), 'CONTEXT_CHANGED')
        at = BASE-timedelta(minutes=45)
        model = SagLearner(self.cfg)
        model.observe(point(at, 'ramp', 26), evaluated_at=at)
        result = model.observe(point(at+timedelta(minutes=3), 'ramp', 25.5, load=1300), evaluated_at=at+timedelta(minutes=3))
        self.assertEqual(result, 'INCOMPARABLE_LOAD_OR_TIME')
        self.assertEqual(model.estimate(evaluated_at=BASE).observation_count, 0)

    def test_reported_ranges_cover_both_endpoints(self):
        for i in range(4):
            at = BASE-timedelta(hours=2)+timedelta(minutes=i*5)
            self.model.observe(point(at, f'pair-{i}', 26, load=1000, amps=15, temp=20, soc=70), evaluated_at=at)
            last = point(at+timedelta(minutes=3), f'pair-{i}', 25.7, load=1050, amps=16, temp=21, soc=72)
            self.model.observe(last, evaluated_at=last.observed_at)
        result = self.model.estimate(evaluated_at=BASE)
        self.assertTrue(result.usable)
        self.assertEqual(result.load_range_w, (1000, 1050))
        self.assertEqual(result.temperature_range_c, (20, 21))
        self.assertEqual(result.soc_range_pct, (70, 72))

    def test_noise_and_untrusted_samples_cannot_inflate_confidence(self):
        for i, sag in enumerate((.1, .34, .1, .34, .1)):
            pair(self.model, i, sag)
        self.assertFalse(self.model.estimate(evaluated_at=BASE).usable)
        self.model = SagLearner(self.cfg)
        self.assertTrue(self.warm().usable)
        at = BASE-timedelta(minutes=90)
        bad = point(at, 'bad', 26, verified=False)
        self.assertEqual(self.model.observe(bad, evaluated_at=BASE), 'UNTRUSTED_OR_OUT_OF_ORDER')
        self.assertFalse(self.model.estimate(evaluated_at=BASE).usable)
        for bad in (point(BASE-timedelta(days=2), 'old', 26), point(BASE+timedelta(seconds=1), 'future', 26),
                    point(BASE-timedelta(minutes=1), 'conflict', 26, charge=1)):
            self.model.observe(bad, evaluated_at=BASE)
            self.assertFalse(self.model.estimate(evaluated_at=BASE).usable)

    def test_rebound_and_charging_are_not_learning_recharge(self):
        at = BASE-timedelta(minutes=20)
        self.model.observe(point(at, 'a', 26), evaluated_at=at)
        rising = point(at+timedelta(minutes=2), 'a', 26.2)
        self.assertEqual(self.model.observe(rising, evaluated_at=rising.observed_at), 'VOLTAGE_RISE_UNCLASSIFIED')
        line = point(at+timedelta(minutes=3), 'b', 26.4, mode='Line Mode', amps=0)
        self.assertEqual(self.model.observe(line, evaluated_at=line.observed_at), 'MODE_CHANGE_REBOUND_NOT_PROOF_OF_RECHARGE')
        charge = point(at+timedelta(minutes=4), 'c', 26.5, mode='Line Mode', amps=0, charge=3)
        self.assertEqual(self.model.observe(charge, evaluated_at=charge.observed_at), 'CHARGE_CURRENT_REPORTED_NOT_LEARNING_RECHARGE')
        self.assertEqual(self.model.estimate(evaluated_at=BASE).observation_count, 0)

    def test_policy_cooldown_always_overrides_learning(self):
        self.warm()
        seasonal = seasonal_fixture.SeasonalTests(); seasonal.setUp()
        history = []
        for minutes in range(482, 0, -1):
            mode = 'Invert Mode' if 2 <= minutes <= 5 else 'Line Mode'
            voltage = {5: 25.9, 4: 25.7, 3: 25.5, 2: 25.4}.get(minutes, 25.9)
            history.append(seasonal_fixture.sample(BASE-timedelta(minutes=minutes), mode=mode,
                           voltage=voltage, discharge=19 if mode == 'Invert Mode' else 0))
        policy = evaluate_output_policy(seasonal.snapshot, seasonal.baseline, tuple(history), evaluated_at=BASE)
        self.assertIn(OutputPolicyReason.COOLDOWN, policy.reason_codes)
        proposal = self.model.propose(seasonal.snapshot, seasonal.baseline, tuple(history), seasonal.energy,
                                      seasonal.bands, evaluated_at=BASE)
        self.assertEqual(proposal.status, 'COOLDOWN')
        self.assertIsNone(proposal.battery_threshold_v)

    def test_duplicate_episode_cannot_increase_observation_count(self):
        pair(self.model, 0, .3)
        at = BASE-timedelta(hours=1)
        result = self.model.observe(point(at, 'trial-0', 26), evaluated_at=at)
        self.assertEqual(result, 'EPISODE_ALREADY_COUNTED')
        self.assertEqual(self.model.estimate(evaluated_at=BASE).observation_count, 1)

    def test_online_estimation_and_proposal_need_no_io_or_clock(self):
        seasonal = seasonal_fixture.SeasonalTests(); seasonal.setUp()
        with patch('builtins.open', side_effect=AssertionError('filesystem')), \
                patch('socket.socket', side_effect=AssertionError('network')), \
                patch('subprocess.Popen', side_effect=AssertionError('process')), \
                patch('time.time', side_effect=AssertionError('clock')):
            self.warm()
            result = self.model.propose(seasonal.snapshot, seasonal.baseline, seasonal.history,
                                        seasonal.energy, seasonal.bands, evaluated_at=BASE)
        self.assertEqual(result.status, 'SHADOW_PROPOSAL')

    def test_persistence_is_versioned_validated_and_requires_fresh_revalidation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'learned.json'
            with self.assertRaises(ValueError): self.model.save(path, evaluated_at=BASE)
            self.warm()
            self.model.save(path, evaluated_at=BASE)
            restored = SagLearner.load(path, self.cfg, evaluated_at=BASE)
            self.assertFalse(restored.estimate(evaluated_at=BASE).usable)
            for i in (4, 5, 6): pair(restored, i, .31)
            self.assertTrue(restored.estimate(evaluated_at=BASE).usable)
            with self.assertRaises(ValueError):
                SagLearner.load(path, replace(self.cfg, context_id='changed-hardware'), evaluated_at=BASE)
            with self.assertRaises(ValueError):
                SagLearner.load(path, self.cfg, evaluated_at=BASE+timedelta(days=2))
            payload = json.loads(path.read_text())
            payload['version'] = 2
            path.write_text(json.dumps(payload))
            with self.assertRaises(ValueError): SagLearner.load(path, self.cfg, evaluated_at=BASE)
            path.write_text('{"version": 1, "version": 1}')
            with self.assertRaises(ValueError): SagLearner.load(path, self.cfg, evaluated_at=BASE)


if __name__ == '__main__':
    unittest.main()
