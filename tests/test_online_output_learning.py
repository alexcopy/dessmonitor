import asyncio
import ast
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.control.online_output_learning import (CYCLING_MIN_SHORT_RUNS, LOCK_ID, MAX_ANALYSIS_OBSERVATIONS,
                                                MIN_CANDIDATE_PAIRS, evaluate_collected_history,
                                                parse_historical_record, run_evaluation, validate_journal_schema,
                                                _observation_signature, _pair_contrast, _fast_context,
                                                _battery_response_model, _sampled_voltage_crossings,
                                                _record_worker_failure)
from app.web_battery_analysis import build_battery_grid
from app.control.sag_learning import SagLearner, SagSample
from test_sag_learning import configuration as sag_configuration
import pg_isolation

try:
    import asyncpg
except ImportError:
    asyncpg = None

NOW = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
# PostgreSQL suites (EASUN_TEST_POSTGRES=1) only run against the disposable database proven by
# tests/pg_isolation.py; there is no default DSN.
DSN = pg_isolation.configured_dsn()


def row(minutes=0, **values):
    return {'time': NOW - timedelta(minutes=minutes), 'working_mode': 'Line Mode',
            'battery_voltage': 25.9, 'battery_soc': 50, 'battery_current_chg': 0.0,
            'battery_current_dis': 0.0, 'pv_power_w': 200.0, 'output_power_w': 800.0, **values}


def matched_rows(responses=(0.5, 0.5, 0.5, 0.5), loads=(500, 700, 900, 1100),
                 pv=(200, 400, 600, 800)):
    """Synthetic collection history with explicitly matched load/PV pairs."""
    rows = []
    for index, (response, load, solar) in enumerate(zip(responses, loads, pv)):
        line_at = NOW - timedelta(minutes=(5 + (len(responses) - 1 - index) * 15))
        rows.append(row(time=line_at, working_mode='Line Mode', battery_voltage=26.0,
                        battery_current_dis=0, battery_current_chg=0, pv_power_w=solar,
                        output_power_w=load))
        rows.append(row(time=line_at + timedelta(minutes=2), working_mode='Invert Mode',
                        battery_voltage=26.0 - response, battery_current_dis=15 + index,
                        battery_current_chg=0, pv_power_w=solar + 25, output_power_w=load + 30))
    return rows


def verified_sag_observations():
    observations = []
    start = NOW - timedelta(hours=2)
    for index, sag in enumerate((0.30, 0.32, 0.31, 0.30)):
        at = start + timedelta(minutes=index * 5)
        for offset, voltage in ((0, 26.0), (3, 26.0 - sag)):
            observed = at + timedelta(minutes=offset)
            sample = SagSample(observed, observed + timedelta(seconds=5), f'synthetic-trial-{index}',
                               'Invert Mode', voltage, 1000.0, 15.0, 0.0, True, 20.0, 70.0)
            observations.append({'sample': sample, 'time_basis': 'SYNTHETIC_VERIFIED_DEVICE_TIME_FIXTURE',
                                 'source_provenance': 'synthetic-easun-fixture-v1'})
    return observations


class DiagnosticEvaluationTests(unittest.TestCase):
    def test_learning_worker_has_no_actuator_or_dess_write_dependencies(self):
        module_path = Path('app/control/online_output_learning.py')
        tree = ast.parse(module_path.read_text())
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or '')
        forbidden = ('app.tuya', 'app.control.easun_output_actuator', 'requests', 'httpx', 'socket', 'subprocess')
        self.assertFalse([name for name in imports if any(name == item or name.startswith(item + '.') for item in forbidden)])
        self.assertNotRegex(module_path.read_text(), r'(?i)(?:write_setting|set_output|set_threshold|dess_request|inverter_client)')

        run_tree = ast.parse(Path('run.py').read_text())
        calls = [node for node in ast.walk(run_tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == 'learning_loop']
        self.assertTrue(calls)
        self.assertTrue(all([arg.id for arg in call.args if isinstance(arg, ast.Name)] ==
                            ['database_url', 'stop_event'] for call in calls))

    def test_verified_synthetic_samples_execute_sag_learner(self):
        learner = SagLearner(sag_configuration())
        result = evaluate_collected_history([], NOW, learner=learner,
                                            verified_observations=verified_sag_observations())
        evidence = result['verified_sag']
        self.assertEqual(evidence['status'], 'VERIFIED')
        self.assertEqual(evidence['evidence_level'], 'VERIFIED')
        self.assertEqual(evidence['observations'].count('OBSERVATION_ACCEPTED'), 4)
        self.assertEqual(evidence['estimate']['observation_count'], 4)
        self.assertTrue(evidence['estimate']['usable'])
        self.assertEqual(evidence['provenance'], [{'time_basis': 'SYNTHETIC_VERIFIED_DEVICE_TIME_FIXTURE',
                                                   'source': 'synthetic-easun-fixture-v1'}])
        # The main decision remains collection-time exploratory even when a separate
        # verified SagLearner result is journaled.
        self.assertEqual(result['evidence_level'], 'EXPLORATORY')

    def test_collection_time_only_never_enters_sag_learner(self):
        learner = SagLearner(sag_configuration())
        sample = verified_sag_observations()[0]['sample']
        with patch.object(learner, 'observe') as observe:
            result = evaluate_collected_history([], NOW, learner=learner, verified_observations=[{
                'sample': sample, 'time_basis': 'COLLECTION_TIME_ONLY', 'source_provenance': 'inverter_metrics.time'}])
        observe.assert_not_called()
        self.assertEqual(result['verified_sag']['status'], 'BLOCKED')
        self.assertIn('DEVICE_TIMESTAMP_PROVENANCE_UNVERIFIED', result['verified_sag']['reason_codes'])

    def test_comparable_load_samples_produce_bounded_recency_weighted_estimate(self):
        result = evaluate_collected_history(matched_rows(responses=(0.35, 0.42, 0.50, 0.62)), NOW)
        contrast = result['exploratory_voltage_contrast']
        self.assertEqual(contrast['comparable_pairs'], 4)
        self.assertGreaterEqual(contrast['contrast_v'], contrast['response_bounds_v'][0])
        self.assertLessEqual(contrast['contrast_v'], contrast['response_bounds_v'][1])
        self.assertGreater(contrast['weighted_load_w'], 800)
        self.assertGreater(contrast['weighted_pv_w'], 500)
        self.assertGreater(contrast['contrast_v'], 0.45)

    def test_passive_battery_model_integrates_elapsed_time_without_claiming_capacity(self):
        rows=[row(time=NOW-timedelta(minutes=10), battery_voltage=25.0, battery_current_chg=4,
                  battery_current_dis=0, battery_soc=None, pv_power_w=900, output_power_w=500),
              row(time=NOW-timedelta(minutes=5), battery_voltage=25.2, battery_current_chg=6,
                  battery_current_dis=0, battery_soc=None, pv_power_w=300, output_power_w=900),
              row(time=NOW, battery_voltage=25.1, battery_current_chg=0,
                  battery_current_dis=10, battery_soc=None, pv_power_w=100, output_power_w=1400)]
        result=evaluate_collected_history(rows,NOW)
        model=result['battery_response']
        self.assertAlmostEqual(model['charged_ah_proxy'],40/60,places=3)
        self.assertAlmostEqual(model['discharged_ah_proxy'],50/120,places=3)
        self.assertEqual(model['capacity_status'],'CAPACITY_NOT_IDENTIFIABLE')
        self.assertIn('BATTERY_CHEMISTRY_UNKNOWN',model['capacity_limitations'])
        self.assertFalse(model['pv_and_output_power_used_for_battery_energy'])
        self.assertEqual(model['impedance_status'],
                         'NOT_IDENTIFIABLE; voltage/current ratio is only a reported-field proxy')
        self.assertEqual(model['freshness'],'COLLECTION_RECENT; DEVICE_FRESHNESS_UNVERIFIED')
        self.assertEqual(model['recent_intervals'][-1]['mode_from'],'Line Mode')
        self.assertEqual(model['capacity_status'],'CAPACITY_NOT_IDENTIFIABLE')
        self.assertFalse(model['pv_and_output_power_used_for_battery_energy'])
        self.assertTrue(model['recent_intervals'][0]['voltage_increase_confounded_by_reported_charging'])
        self.assertFalse(model['recent_intervals'][0]['voltage_recovery_with_reported_discharge_or_load_reduction'])

    def test_passive_model_ignores_cached_payloads_and_long_gaps(self):
        first=row(time=NOW-timedelta(minutes=40),battery_voltage=25.0,battery_current_dis=5)
        repeated={**first,'time':NOW-timedelta(minutes=35)}
        latest=row(time=NOW,battery_voltage=25.5,battery_current_dis=10)
        model=evaluate_collected_history([first,repeated,latest],NOW)['battery_response']
        self.assertEqual(model['intervals_integrated'],0)
        self.assertEqual(model['gaps_over_15m'],1)
        self.assertEqual(model['capacity_status'],'CAPACITY_NOT_IDENTIFIABLE')

    def test_throughput_null_zero_and_insufficient_coverage_are_distinct(self):
        empty = _battery_response_model([], NOW)
        self.assertEqual(empty['throughput_status'], 'NO_USABLE_OBSERVATIONS')
        for key in ('charged_ah_proxy', 'discharged_ah_proxy', 'charged_wh_proxy', 'discharged_wh_proxy'):
            self.assertIsNone(empty[key])

        one = _battery_response_model([row(time=NOW)], NOW)
        self.assertEqual(one['throughput_status'], 'INSUFFICIENT_MEASUREMENT_COVERAGE')
        self.assertIsNone(one['charged_ah_proxy'])
        long_gap = _battery_response_model([
            row(time=NOW-timedelta(minutes=20)), row(time=NOW)], NOW)
        self.assertEqual(long_gap['throughput_status'], 'INSUFFICIENT_MEASUREMENT_COVERAGE')
        self.assertIsNone(long_gap['discharged_wh_proxy'])

        zero = _battery_response_model([
            row(time=NOW-timedelta(minutes=5), battery_current_chg=0, battery_current_dis=0),
            row(time=NOW, battery_voltage=25.8, battery_current_chg=0, battery_current_dis=0)], NOW)
        self.assertEqual(zero['throughput_status'], 'OBSERVED_ZERO_REPORTED_CHANNEL_THROUGHPUT')
        self.assertEqual(zero['charged_ah_proxy'], 0.0)
        self.assertEqual(zero['discharged_wh_proxy'], 0.0)

    def test_fast_context_tracks_cloud_and_load_change_without_threshold_candidate(self):
        rows=[row(time=NOW-timedelta(minutes=15-i*5),working_mode='Invert Mode',
                  pv_power_w=1000-i*300,output_power_w=500+i*400,battery_current_dis=10+i,
                  battery_voltage=25.5-i*.05) for i in range(4)]
        result=evaluate_collected_history(rows,NOW)
        fast=result['decision_diagnostics']['fast_context']
        self.assertEqual(fast['distinct_consecutive_payloads'],4)
        self.assertEqual(fast['pv_range_w'],[100,1000])
        self.assertEqual(fast['load_range_w'],[500,1700])
        self.assertTrue(fast['collection_recent'])
        self.assertIn('DEVICE_FRESHNESS_UNVERIFIED',fast['freshness'])
        self.assertIsNone(result['proposed_voltage'])

    def test_fast_context_unknown_missing_and_conflicting_modes_break_continuity(self):
        cases = [
            (['Line Mode', None, 'Invert Mode'], 0),
            (['Line Mode', 'Invert Mode'], 1),
            (['Line Mode', 'Unknown', 'Invert Mode'], 0),
            (['Line Mode', 'Line Mode / Invert Mode', 'Invert Mode'], 0),
            (['Line Mode', 'MISSING', 'Invert Mode'], 0),
        ]
        for modes, expected in cases:
            with self.subTest(modes=modes):
                rows = []
                for index, mode in enumerate(modes):
                    values = {'working_mode': None if mode == 'MISSING' else mode,
                              'pv_power_w': 200 + index}
                    if mode != 'MISSING':
                        values['working_mode'] = mode
                    rows.append(row(time=NOW-timedelta(minutes=len(modes)-index), **values))
                context = _fast_context(rows, NOW)
                self.assertEqual(context['reported_mode_changes'], expected)

    def test_voltage_rise_with_reported_discharge_is_neutral_in_worker_and_grid(self):
        older = row(time=NOW-timedelta(minutes=5), battery_voltage=25.4,
                    battery_current_chg=0, battery_current_dis=10, output_power_w=800)
        latest = row(time=NOW, battery_voltage=25.6,
                     battery_current_chg=0, battery_current_dis=10, output_power_w=800)
        result = evaluate_collected_history([older, latest], NOW)
        context = result['charging_context']
        self.assertIn('VOLTAGE_RISE_OBSERVED', context['evidence_labels'])
        self.assertNotIn('CHARGING_RESPONSE_OBSERVED', context['evidence_labels'])
        self.assertEqual(context['voltage_rises_without_easun_charge_reported'], 1)
        grid = build_battery_grid([latest, older], limit=1, has_more=True, now=NOW)
        response = grid['battery_behavior'][0]['estimated_voltage_response']
        self.assertIn('VOLTAGE_RISE_OBSERVED', response)
        self.assertIn('EASUN_DISCHARGE_REPORTED', response)
        self.assertIn('EXTERNAL_CHARGING_POSSIBLE', response)
        self.assertNotIn('CHARGING_RESPONSE_OBSERVED', response)

    def test_simultaneous_current_channels_are_consistent_across_worker_and_grid(self):
        older = row(time=NOW-timedelta(minutes=5), battery_voltage=25.4,
                    battery_current_chg=4, battery_current_dis=20)
        latest = row(time=NOW, battery_voltage=25.6,
                     battery_current_chg=5, battery_current_dis=22)
        result = evaluate_collected_history([older, latest], NOW)
        context = result['charging_context']
        # Both-channel collections are a CONFLICTING_CURRENT barrier in the shared
        # interval policy (worker and grid compute no slope/Ah for them), so the
        # multi-source context must not count the rise as accepted trend evidence.
        self.assertEqual(context['voltage_rises_with_both_easun_channels_reported'], 0)
        self.assertEqual(context['simultaneous_nonzero_easun_charge_and_discharge_channel_intervals'], 1)
        self.assertEqual(context['intervals_with_voltage_response'], 0)
        self.assertNotIn('VOLTAGE_RISE_OBSERVED', context['evidence_labels'])
        self.assertNotIn('CHARGING_RESPONSE_OBSERVED', context['evidence_labels'])
        grid = build_battery_grid([latest, older], limit=1, has_more=True, now=NOW)
        behavior = grid['battery_behavior'][0]
        self.assertIn('BOTH_EASUN_CURRENT_CHANNELS_NONZERO', behavior['estimated_voltage_response'])
        self.assertIn('SEMANTICS_UNVERIFIED', behavior['estimated_voltage_response'])
        self.assertNotIn('CHARGING_RESPONSE_OBSERVED', behavior['estimated_voltage_response'])
        self.assertIsNone(behavior['easun_charge_ah_proxy'])
        self.assertIsNone(behavior['easun_discharge_ah_proxy'])

        switched = evaluate_collected_history([
            {**older, 'battery_current_chg': 4, 'battery_current_dis': 0},
            {**latest, 'battery_current_chg': 0, 'battery_current_dis': 22}], NOW)
        switched_context = switched['charging_context']
        self.assertEqual(switched_context['simultaneous_nonzero_easun_charge_and_discharge_channel_intervals'], 0)
        self.assertEqual(switched_context['intervals_by_easun_reported_current_context'].get(
            'EASUN_CURRENT_CONTEXT_CHANGED_ACROSS_INTERVAL'), 1)

    def test_reported_1105_to_1106_confidence_and_candidate_transition_replay(self):
        # Deterministic explanatory reconstruction of the reported transition, not a
        # claim that production telemetry was available. One old outlier is rejected.
        at=datetime(2026,11,7,11,5,tzinfo=timezone.utc)
        def sample(stamp, mode, voltage, charge, discharge, pv, load):
            return {'time':stamp,'working_mode':mode,'battery_voltage':voltage,'battery_soc':50,
                    'battery_current_chg':charge,'battery_current_dis':discharge,
                    'pv_power_w':pv,'output_power_w':load}
        def pair(stamp, response, load, pv):
            return [sample(stamp-timedelta(seconds=78),'Line Mode',26.0,0,0,pv,load),
                    sample(stamp,'Invert Mode',26.0-response,0,10,pv,load)]
        rows=[]
        for age,response,load,pv in ((120,1.2,400,100),(75,.594,900,600),(30.32,.594,1400,1100)):
            rows.extend(pair(at-timedelta(minutes=age),response,load,pv))
        rows.extend([sample(at-timedelta(minutes=5),'Line Mode',25.8,None,0,None,700),
                     sample(at-timedelta(minutes=3),'Invert Mode',None,0,10,None,700),
                     sample(at-timedelta(minutes=1),'Invert Mode',25.7,0,0,200,700)])

        first=evaluate_collected_history(rows,at)
        first_contrast=first['exploratory_voltage_contrast']
        # Only the four Line->Invert changes with short collection intervals are direct;
        # the 40+ minute intervals between pairs are observation gaps.
        self.assertEqual(first['recent_reported_mode_changes'],4)
        self.assertEqual(first['decision_diagnostics']['mode_history_coverage']['observation_gaps'],3)
        self.assertEqual(first_contrast['contrast_v'],.594)
        self.assertEqual(first_contrast['confidence'],.329)
        self.assertEqual(first_contrast['candidate_pairs'],3)
        self.assertEqual(first_contrast['comparable_pairs'],2)
        self.assertEqual(first['decision'],'HOLD')
        self.assertEqual(first['stability'],'NO_CANDIDATE')

        second_at=at+timedelta(minutes=1)
        rows2=[item for item in rows if item['time']!=at-timedelta(minutes=5)]
        rows2.extend(pair(second_at-timedelta(minutes=5),.53,1900,1600))
        second=evaluate_collected_history(rows2,second_at,first)
        second_contrast=second['exploratory_voltage_contrast']
        self.assertEqual(second_contrast['contrast_v'],.551)
        self.assertEqual(second_contrast['confidence'],.887)
        self.assertEqual(second_contrast['candidate_pairs'],4)
        self.assertEqual(second_contrast['comparable_pairs'],3)
        self.assertEqual(second['candidate_voltage'],26.5)
        self.assertEqual(second['stability'],'AWAITING_COMPATIBLE_OBSERVATION')
        self.assertEqual(second['confirmation_progress']['confirmed'],0)

        third_at=at+timedelta(minutes=4)
        rows3=rows2+pair(third_at,.55,2500,2200)
        third=evaluate_collected_history(rows3,third_at,second)
        self.assertEqual(third['candidate_voltage'],26.5)
        self.assertEqual(third['confirmation_progress']['confirmed'],1)
        self.assertEqual(third['confirmation_progress']['required'],2)
        self.assertEqual(third['stability'],'AWAITING_CONFIRMATION')

        fourth_at=at+timedelta(minutes=7)
        repeated=sample(fourth_at,'Invert Mode',rows3[-1]['battery_voltage'],0,10,2200,2500)
        fourth=evaluate_collected_history(rows3+[repeated],fourth_at,third)
        self.assertEqual(fourth['confirmation_progress']['confirmed'],1)
        self.assertEqual(fourth['proposal_state']['stable_count'],1)
        self.assertEqual(fourth['decision_diagnostics']['sample_counts']['rejected_duplicate_cached_payload'],1)

        rolled_state=dict(third)
        rolled_proposal=dict(third['proposal_state'])
        rolled_proposal['confirmation_signatures']=[['older-retained-signature',index] for index in range(32)]
        rolled_proposal['last_sample_signature']=list(_observation_signature(rows3[-2]))
        rolled_state['proposal_state']=rolled_proposal
        after_signature_window=evaluate_collected_history(rows3+[repeated],fourth_at,rolled_state)
        self.assertEqual(after_signature_window['confirmation_progress']['confirmed'],1)
        self.assertEqual(after_signature_window['proposal_state']['stable_count'],1)

    def test_thirty_minute_pair_gap_rejects_stale_conditions_and_cloud_mismatch(self):
        line=row(time=NOW-timedelta(minutes=50),working_mode='Line Mode',battery_voltage=26.0,
                 battery_current_chg=0,battery_current_dis=0,pv_power_w=500,output_power_w=700)
        stale_invert=row(time=NOW-timedelta(minutes=5),working_mode='Invert Mode',battery_voltage=25.4,
                         battery_current_chg=0,battery_current_dis=10,pv_power_w=500,output_power_w=700)
        stale=_pair_contrast([line],[stale_invert],(100,100),NOW)
        self.assertEqual(stale['rejected']['TIME_GAP_EXCEEDED'],1)
        self.assertEqual(stale['pairs'],[])
        comparable_line={**line,'time':NOW-timedelta(minutes=2)}
        cloudy={**stale_invert,'time':NOW,'pv_power_w':100}
        solar_mismatch=_pair_contrast([comparable_line],[cloudy],(100,100),NOW)
        self.assertEqual(solar_mismatch['rejected']['PV_NOT_COMPARABLE'],1)
        self.assertEqual(solar_mismatch['pairs'],[])

    def test_fast_passive_response_appears_after_one_fresh_collection_interval(self):
        rows=[row(time=NOW-timedelta(minutes=5),working_mode='Invert Mode',battery_voltage=25.8,
                  battery_current_dis=5,output_power_w=700,pv_power_w=600),
              row(time=NOW,working_mode='Invert Mode',battery_voltage=25.6,
                  battery_current_dis=9,output_power_w=1100,pv_power_w=200)]
        result=evaluate_collected_history(rows,NOW)
        fast=result['decision_diagnostics']['fast_context']
        model=result['battery_response']
        self.assertEqual(fast['distinct_consecutive_payloads'],2)
        self.assertEqual(fast['battery_voltage_slope_v_per_hour'],-2.4)
        self.assertEqual(model['intervals_integrated'],1)
        self.assertEqual(model['recent_intervals'][0]['load_delta_w_reported'],400)
        # The pre-existing threshold contrast required at least six observations;
        # passive response is already visible after this one five-minute interval.
        self.assertIsNone(result['exploratory_voltage_contrast'])
        self.assertEqual(result['actionability'],'NON_ACTIONABLE')

    def test_response_speed_and_high_current_never_become_capacity_or_health(self):
        rapid=[row(time=NOW-timedelta(minutes=5),battery_voltage=26.0,battery_current_dis=4),
               row(time=NOW,battery_voltage=25.5,battery_current_dis=40)]
        slow=[row(time=NOW-timedelta(minutes=5),battery_voltage=26.0,battery_current_dis=4),
              row(time=NOW,battery_voltage=25.95,battery_current_dis=40)]
        rapid_model=evaluate_collected_history(rapid,NOW)['battery_response']
        slow_model=evaluate_collected_history(slow,NOW)['battery_response']
        self.assertGreater(abs(rapid_model['latest_voltage_slope_v_per_hour']),
                           abs(slow_model['latest_voltage_slope_v_per_hour']))
        for model in (rapid_model,slow_model):
            self.assertEqual(model['capacity_status'],'CAPACITY_NOT_IDENTIFIABLE')
            self.assertNotIn('capacity_ah',model)
            self.assertNotIn('health_percent',model)
        self.assertIn('PROXY',rapid_model['recent_intervals'][0]['current_response_interpretation'])

    def test_pv_or_load_change_does_not_count_as_a_new_battery_measurement(self):
        rows=[row(time=NOW-timedelta(minutes=10),battery_voltage=25.5,battery_current_dis=8,
                  pv_power_w=100,output_power_w=300),
              row(time=NOW-timedelta(minutes=5),battery_voltage=25.5,battery_current_dis=8,
                  pv_power_w=700,output_power_w=900),
              row(time=NOW,battery_voltage=25.4,battery_current_dis=9,
                  pv_power_w=1000,output_power_w=1500)]
        model=evaluate_collected_history(rows,NOW)['battery_response']
        self.assertEqual(model['observations'],2)
        # The repeated battery payload is an unverified cached collection,
        # so neither adjacent interval can establish battery throughput.
        self.assertEqual(model['intervals_integrated'],0)
        self.assertEqual(model['throughput_coverage'],'NONE')
        self.assertEqual(model['rejected_intervals']['CACHED_REPEAT'],2)

    def test_passive_model_records_load_recovery_and_mode_transition_as_association(self):
        rows=[row(time=NOW-timedelta(minutes=5),working_mode='Invert Mode',battery_voltage=25.4,
                  battery_current_dis=10,output_power_w=1200,pv_power_w=200),
              row(time=NOW,working_mode='Line Mode',battery_voltage=25.6,
                  battery_current_dis=2,output_power_w=500,pv_power_w=800)]
        segment=evaluate_collected_history(rows,NOW)['battery_response']['recent_intervals'][0]
        self.assertEqual(segment['mode_from'],'Invert Mode')
        self.assertEqual(segment['mode_to'],'Line Mode')
        self.assertTrue(segment['reported_mode_changed'])
        self.assertTrue(segment['voltage_recovery_with_reported_discharge_or_load_reduction'])
        self.assertIn('NO_CAUSAL',segment['interpretation'])

    def test_external_charging_and_multiple_solar_paths_remain_unobserved(self):
        rows=[row(time=NOW-timedelta(minutes=10),working_mode='Line Mode',battery_voltage=25.3,
                  battery_current_chg=0,battery_current_dis=0,battery_soc=50,pv_power_w=200,output_power_w=900),
              row(time=NOW-timedelta(minutes=5),working_mode='Line Mode',battery_voltage=25.5,
                  battery_current_chg=0,battery_current_dis=0,battery_soc=60,pv_power_w=700,output_power_w=600),
              row(time=NOW,working_mode='Invert Mode',battery_voltage=25.7,
                  battery_current_chg=0,battery_current_dis=0,battery_soc=100,pv_power_w=1100,output_power_w=300)]
        result=evaluate_collected_history(rows,NOW)
        context=result['charging_context']
        self.assertIn('EXTERNAL_CHARGING_POSSIBLE',context['evidence_labels'])
        self.assertIn('TOTAL_BATTERY_CURRENT_UNOBSERVED',context['evidence_labels'])
        self.assertIn('CHARGING_RELATIONSHIP_UNVERIFIED',context['evidence_labels'])
        self.assertIn('VOLTAGE_RISE_OBSERVED',context['evidence_labels'])
        self.assertNotIn('CHARGING_RESPONSE_OBSERVED',context['evidence_labels'])
        self.assertIn('CAPACITY_NOT_IDENTIFIABLE',context['evidence_labels'])
        self.assertEqual(context['external_charging_status'],'POSSIBLE; NOT DETECTED AT A SPECIFIC MOMENT')
        self.assertFalse(context['external_charging_assumed_zero'])
        self.assertFalse(context['easun_current_equals_total_battery_current'])
        self.assertEqual(context['voltage_rises_without_easun_charge_reported'],2)
        self.assertEqual(context['easun_visible_pv_rising_intervals'],2)
        self.assertIn('OTHER_ORIENTATIONS_AND_CONTROLLERS_UNOBSERVED',context['pv_source_coverage'])
        self.assertEqual(context['soc_changes_without_easun_current_reported'],2)
        self.assertIn('SOC_CHANGE_NOT_VALIDATED_BY_EASUN_THROUGHPUT',context['evidence_labels'])
        self.assertEqual(result['battery_response']['capacity_status'],'CAPACITY_NOT_IDENTIFIABLE')
        self.assertFalse(result['battery_response']['total_battery_current_observed'])
        self.assertFalse(result['battery_response']['external_charging_assumed_zero'])

    def test_simultaneous_easun_charge_and_discharge_channels_are_not_combined(self):
        rows=[row(time=NOW-timedelta(minutes=5),working_mode='Invert Mode',battery_voltage=25.5,
                  battery_current_chg=4,battery_current_dis=20),
              row(time=NOW,working_mode='Invert Mode',battery_voltage=25.4,
                  battery_current_chg=5,battery_current_dis=22)]
        result=evaluate_collected_history(rows,NOW)
        context=result['charging_context']
        self.assertEqual(context['simultaneous_nonzero_easun_charge_and_discharge_channel_intervals'],1)
        self.assertFalse(context['total_battery_current_observed'])
        self.assertEqual(result['battery_response']['rejected_intervals']['CONFLICTING_CURRENT'],1)
        self.assertEqual(result['battery_response']['capacity_status'],'CAPACITY_NOT_IDENTIFIABLE')

    def test_november_7_transition_replay_keeps_external_charge_unattributed(self):
        base=datetime(2026,11,7,tzinfo=timezone.utc)
        def at(hour,minute,second=0):
            return base.replace(hour=hour,minute=minute,second=second)
        points=[
            (at(9,46,30),'Line Mode',25.9,0,0,400,580,50),
            (at(9,47),'Invert Mode',25.3,0,25,500,595,50),
            (at(9,51),'Line Mode',26.0,4,0,620,585,51),
            (at(10,1),'Invert Mode',25.4,0,24,900,576,52),
            (at(10,6),'Line Mode',26.0,4,0,1100,615,53),
            (at(10,21),'Invert Mode',25.2,0,29,350,658,55),
            (at(10,25),'Line Mode',25.8,0,0,950,671,100),
            (at(10,45),'Invert Mode',25.4,0,22,1200,634,99),
        ]
        rows=[{'time':stamp,'working_mode':mode,'battery_voltage':voltage,'battery_soc':soc,
            'battery_current_chg':charge,'battery_current_dis':discharge,'pv_power_w':pv,'output_power_w':load}
            for stamp,mode,voltage,charge,discharge,pv,load,soc in points]
        result=evaluate_collected_history(rows,at(10,45))
        # 10:06->10:21 and 10:25->10:45 exceed COLLECTION_GAP_LIMIT: those endpoint
        # differences are uncertain changes across unobserved intervals, not transitions.
        self.assertEqual(result['recent_reported_mode_changes'],5)
        coverage=result['decision_diagnostics']['mode_history_coverage']
        self.assertEqual(coverage['observation_gaps'],2)
        self.assertEqual(coverage['uncertain_mode_changes_across_gaps'],2)
        self.assertEqual(coverage['continuity_status'],'INTERRUPTED')
        self.assertTrue(result['decision_diagnostics']['cycling']['detected'])
        classes=result['decision_diagnostics']['evidence_classes']
        self.assertGreater(classes['LINE_CHARGING']['median_voltage_v'],
                           classes['INVERT_DISCHARGING']['median_voltage_v'])
        context=result['charging_context']
        self.assertIn('VOLTAGE_RISE_OBSERVED',context['evidence_labels'])
        self.assertNotIn('CHARGING_RESPONSE_OBSERVED',context['evidence_labels'])
        self.assertGreaterEqual(context['voltage_rises_without_easun_charge_reported'],1)
        self.assertIn('EXTERNAL_CHARGING_POSSIBLE',context['evidence_labels'])
        self.assertEqual(result['soc_provenance'],'reported; not verified SOC')
        self.assertEqual(result['battery_response']['capacity_status'],'CAPACITY_NOT_IDENTIFIABLE')
        virtual=result['virtual_threshold_experiment']
        self.assertGreater(len(virtual['candidates']),0)
        self.assertIsNone(virtual['recommendation'])
        self.assertIn('TOTAL_BATTERY_CURRENT_UNOBSERVED',virtual['blockers'])
        self.assertEqual(result['transitions_provenance'],'collection order; not measurement time')
        self.assertEqual(result['actionability'],'NON_ACTIONABLE')

    def test_virtual_threshold_pairs_have_no_winner_and_remain_blocked(self):
        rows=[row(time=NOW-timedelta(minutes=20-i*5),working_mode='Invert Mode',
                  battery_voltage=voltage,output_power_w=700+i*30,pv_power_w=300)
              for i,voltage in enumerate((25.0,26.1,25.1,26.2,25.0))]
        result=evaluate_collected_history(rows,NOW)
        replay=result['virtual_threshold_experiment']
        self.assertEqual(replay['status'],'VIRTUAL_REPLAY_ONLY_BLOCKED_FROM_RECOMMENDATION')
        self.assertIsNone(replay['recommendation'])
        self.assertGreater(len(replay['candidates']),0)
        self.assertEqual(replay['comparison_status'], 'ALL_CANDIDATES_EXPLORATORY_NO_WINNER')
        self.assertEqual(replay['comparison_order'], 'NUMERIC_THRESHOLD_PAIR_ORDER; NO PREFERENCE')
        self.assertTrue(all('rank' not in item and 'rank_basis' not in item for item in replay['candidates']))
        for candidate in replay['candidates']:
            self.assertGreaterEqual(candidate['hysteresis_width_v'],0.5)
            self.assertEqual(candidate['actionability'],'NON_ACTIONABLE')
            self.assertIsNone(candidate['potential_switching_frequency'])
            self.assertIsNone(candidate['battery_exposure_under_load'])
            self.assertIsNotNone(candidate['sampled_boundary_crossings_per_hour'])
            self.assertIsNotNone(candidate['easun_visible_pv_range_w'])
            self.assertIn('NOT_IDENTIFIABLE',candidate['oscillation_risk'])
            self.assertIn('OBSERVED_SAMPLE_DIFFERENCE_ONLY',candidate['margin_interpretation'])
            self.assertIn('EXTERNAL CHARGING',candidate['predicted_risk'])
            self.assertIn('SAMPLED_VOLTAGE_BOUNDARIES_ONLY',candidate['reasons'])
            self.assertEqual(candidate['candidate_status'], 'EXPLORATORY_COMPARISON_NO_WINNER')
            self.assertIn('BMS_AND_MANUFACTURER_LIMITS_UNKNOWN',candidate['blockers'])
        self.assertIn('BMS_AND_MANUFACTURER_LIMITS_UNKNOWN',replay['blockers'])
        self.assertEqual(result['adaptive_hysteresis']['current_live_hysteresis_v'],None)

    def test_virtual_boundary_crossings_ignore_touches_and_plateaus(self):
        def crossing(values, gaps=None):
            gaps = gaps or [timedelta(minutes=5)] * (len(values)-1)
            times = [NOW-timedelta(minutes=sum((gap.total_seconds() for gap in gaps[i:]), 0) / 60)
                     for i in range(len(values))]
            samples = [row(time=stamp, battery_voltage=value) for stamp, value in zip(times, values)]
            return _sampled_voltage_crossings(samples, 25.5)[0]

        self.assertEqual(crossing([25.4, 25.5, 25.4]), 0)
        self.assertEqual(crossing([25.4, 25.5, 25.5, 25.4]), 0)
        self.assertEqual(crossing([25.4, 25.5, 25.6]), 1)
        self.assertEqual(crossing([25.4, 25.6, 25.4]), 2)
        self.assertEqual(crossing([25.4, 25.6], [timedelta(minutes=16)]), 0)

    def test_virtual_threshold_radius_is_configurable_and_invalid_radius_fails_closed(self):
        rows=[row(time=NOW-timedelta(minutes=5)),row(time=NOW,battery_voltage=26.0)]
        with patch.dict(os.environ,{'ADAPTIVE_OUTPUT_VIRTUAL_RADIUS_STEPS':'0'}):
            baseline=evaluate_collected_history(rows,NOW)['virtual_threshold_experiment']
        self.assertEqual(len(baseline['candidates']),1)
        with patch.dict(os.environ,{'ADAPTIVE_OUTPUT_VIRTUAL_RADIUS_STEPS':'3'}):
            invalid=evaluate_collected_history(rows,NOW)['virtual_threshold_experiment']
        self.assertEqual(invalid['candidates'],[])
        self.assertIn('VIRTUAL_CANDIDATE_CONFIGURATION_INVALID',invalid['blockers'])

    def test_daily_behavior_review_compares_windows_without_resetting_state(self):
        rows=[]
        for minute in range(24*60-5,-1,-5):
            age=24*60-minute
            rows.append(row(time=NOW-timedelta(minutes=age),working_mode='Invert Mode',
                battery_voltage=25.5-(minute/10000),battery_current_dis=8,
                output_power_w=700,pv_power_w=200))
        previous={'algorithm_version':'online-exploratory-5','proposal_state':{
            'active':'HYPOTHETICAL_RAISE:26.5','last_change_at':(NOW-timedelta(minutes=30)).isoformat()}}
        result=evaluate_collected_history(rows,NOW,previous,daily_review_due=True)
        review=result['daily_behavior_review']
        self.assertEqual(review['status'],'REVIEWED')
        self.assertEqual(review['collection_count'],288)
        self.assertFalse(review['learning_state_reset'])
        self.assertEqual(result['proposal_state']['active'],'HYPOTHETICAL_RAISE:26.5')
        self.assertEqual(review['capacity_status'],'CAPACITY_NOT_IDENTIFIABLE')
        self.assertIn('TOTAL_BATTERY_CURRENT_UNOBSERVED',review['charging_context_review']['evidence_labels'])

    def test_telemetry_audit_does_not_upgrade_labels_to_verified_sources(self):
        audit=evaluate_collected_history([row()],NOW,daily_review_due=True)['telemetry_audit']
        self.assertFalse(audit['device_measurement_timestamp_available_in_inverter_metrics'])
        self.assertFalse(audit['battery_current_chg']['source_semantics_verified'])
        self.assertFalse(audit['battery_soc']['soc_accuracy_verified'])
        self.assertFalse(audit['battery_temperature_available'])
        self.assertFalse(audit['external_battery_charging_current_available'])
        self.assertFalse(audit['external_pv_power_available'])
        self.assertFalse(audit['solar_controller_or_orientation_identity_available'])
        self.assertFalse(audit['pv_ac_load_dc_battery_energy_combined'])

    def test_recent_pair_weighting_adapts_faster_than_legacy_45_minute_average(self):
        line=[]; invert=[]
        for index,(age,response) in enumerate(((65,.35),(50,.40),(35,.45),(5,.55))):
            at=NOW-timedelta(minutes=age)
            load=500+index*100; pv=200+index*80
            line.append(row(time=at-timedelta(minutes=1),working_mode='Line Mode',battery_voltage=26.0,
                battery_current_chg=0,battery_current_dis=0,output_power_w=load,pv_power_w=pv))
            invert.append(row(time=at,working_mode='Invert Mode',battery_voltage=26.0-response,
                battery_current_chg=0,battery_current_dis=10,output_power_w=load,pv_power_w=pv))
        contrast=_pair_contrast(line,invert,(200,300),NOW)
        retained=contrast['retained']
        legacy_weights=[0.5**((NOW-pair['time']).total_seconds()/2700.0) for pair in retained]
        legacy=(sum(pair['response_v']*weight for pair,weight in zip(retained,legacy_weights)) /
                sum(legacy_weights))
        self.assertGreater(contrast['raw'],legacy)
        self.assertLess(abs(contrast['raw']-.55),abs(legacy-.55))
        self.assertEqual(contrast,_pair_contrast(line,invert,(200,300),NOW))

    def test_threshold_display_separates_monitored_unknown_current_and_history(self):
        result=evaluate_collected_history([row()],NOW)
        self.assertIsNone(result['threshold'])
        self.assertEqual(result['monitored_threshold']['label'],'Back to Battery')
        self.assertIsNone(result['current_settings']['battery'])
        self.assertEqual(result['historical_settings']['battery'],26.0)
        self.assertIn('HISTORICAL_BASELINE',result['historical_settings']['provenance'])
        self.assertIn('verified live setting: UNKNOWN',result['explanation'])

    def test_sudden_load_change_rejects_incomparable_response(self):
        rows = []
        for index in range(3):
            at = NOW - timedelta(minutes=35 - index * 15)
            rows.append(row(time=at, working_mode='Line Mode', battery_voltage=26.0,
                            output_power_w=400 + index * 50, pv_power_w=300))
            rows.append(row(time=at + timedelta(minutes=2), working_mode='Invert Mode', battery_voltage=25.4,
                            battery_current_dis=18, output_power_w=1200 + index * 50, pv_power_w=300))
        result = evaluate_collected_history(rows, NOW)
        contrast = result['exploratory_voltage_contrast']
        self.assertIsNone(contrast['contrast_v'])
        self.assertEqual(contrast['comparable_pairs'], 0)
        self.assertIsNone(result['proposed_voltage'])

    def test_line_sample_with_comparable_partner_lost_to_one_to_one_match_is_not_mislabeled(self):
        line_earlier = row(time=NOW-timedelta(minutes=2), working_mode='Line Mode', battery_voltage=26.0,
                           battery_current_chg=0, output_power_w=800, pv_power_w=200)
        line_later = row(time=NOW-timedelta(minutes=1), working_mode='Line Mode', battery_voltage=26.1,
                         battery_current_chg=0, output_power_w=800, pv_power_w=200)
        invert = row(time=NOW-timedelta(seconds=30), working_mode='Invert Mode', battery_voltage=25.5,
                     battery_current_dis=15, output_power_w=800, pv_power_w=200)

        result = _pair_contrast([line_earlier, line_later], [invert], (75, 75), NOW)
        self.assertEqual(len(result['pairs']), 1)
        self.assertEqual(result['rejected']['NOT_SELECTED_ONE_TO_ONE'], 1)
        self.assertEqual(result['line_without_comparable_partner'], 0)
        self.assertEqual(result['line_not_selected_one_to_one'], 1)

    def test_noisy_pairs_keep_a_bounded_robust_estimate(self):
        result = evaluate_collected_history(matched_rows((0.48, 1.6, 0.50, 0.52, 0.51),
                                                          loads=(400, 600, 800, 1000, 1200),
                                                          pv=(100, 300, 500, 700, 900)), NOW)
        contrast = result['exploratory_voltage_contrast']
        self.assertEqual(contrast['candidate_pairs'], 5)
        self.assertEqual(contrast['comparable_pairs'], 4)
        self.assertLess(abs(contrast['contrast_v'] - 0.5), 0.04)
        self.assertLessEqual(abs(contrast['contrast_v']), 2.0)
        self.assertEqual(result['stability'], 'AWAITING_CONFIRMATION')
        self.assertIsNone(result['proposed_voltage'])

    def test_empirical_response_change_reduces_confidence_without_guessing_cause(self):
        initial = evaluate_collected_history(matched_rows((0.5, 0.5, 0.5, 0.5)), NOW)
        changed = evaluate_collected_history(matched_rows((0.9, 0.9, 0.9, 0.9)), NOW + timedelta(minutes=1), initial)
        self.assertIn('EMPIRICAL_RESPONSE_CHANGED_CAUSE_UNKNOWN', changed['reason_codes'])
        self.assertTrue(changed['exploratory_voltage_contrast']['response_changed'])
        self.assertLess(changed['confidence'], initial['confidence'])
        self.assertEqual(changed['decision'], 'HOLD')
        self.assertIsNone(changed['proposed_voltage'])
        self.assertIn('cause is unknown', changed['explanation'])

    def test_outlier_cannot_confirm_or_change_an_existing_target(self):
        rows = matched_rows()
        first = evaluate_collected_history(rows, NOW)
        self.assertEqual(first['stability'], 'AWAITING_CONFIRMATION')
        compatible = row(time=NOW + timedelta(minutes=1), working_mode='Line Mode', battery_voltage=26.0,
                         battery_current_dis=0, pv_power_w=825, output_power_w=1130)
        confirmed = evaluate_collected_history(rows + [compatible], NOW + timedelta(minutes=1), first)
        self.assertEqual(confirmed['decision'], 'HYPOTHETICAL_RAISE')
        old_target = confirmed['proposal_state']['active']
        outlier = row(time=NOW + timedelta(minutes=2), working_mode='Invert Mode', battery_voltage=24.4,
                      battery_current_dis=20, pv_power_w=825, output_power_w=1130)
        held = evaluate_collected_history(rows + [compatible, outlier], NOW + timedelta(minutes=2), confirmed)
        self.assertEqual(held['decision'], 'HOLD')
        self.assertIsNone(held['proposed_voltage'])
        self.assertEqual(held['proposal_state']['active'], old_target)
        self.assertEqual(held['proposal_state']['stable_count'], 0)

    def test_documented_record_timestamp_and_dst(self):
        receipt = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)
        parsed = parse_historical_record(['synthetic-id', '2026-06-01 12:30:00'], 'Europe/London', receipt, set())
        self.assertEqual(parsed['device_observed_at'], datetime(2026, 6, 1, 11, 30, tzinfo=timezone.utc))
        self.assertEqual(parsed['received_at'], receipt)
        self.assertFalse(parsed['field_mapping_verified'])
        with self.assertRaises(ValueError):
            parse_historical_record(['synthetic-id', '2026-06-01 12:30:00'], 'Europe/London', receipt, {'synthetic-id'})
        for stamp in ('2026-10-25 01:30:00', '2026-03-29 01:30:00'):
            with self.assertRaises(ValueError):
                parse_historical_record(['another', stamp], 'Europe/London', datetime(2027, 1, 1, tzinfo=timezone.utc), set())

    def test_exploratory_hypothesis_is_historical_and_never_actionable(self):
        rows = [row(50-i*10, working_mode='Line Mode' if i % 2 == 0 else 'Invert Mode',
                    battery_voltage=(25.9+i*.01 if i % 2 == 0 else 25.4+i*.01),
                    battery_current_dis=(0 if i % 2 == 0 else 15+i), output_power_w=500+i*20)
                for i in range(6)]
        pending = evaluate_collected_history(rows, NOW)
        self.assertEqual(pending['decision'], 'HOLD')
        cached = rows[-1].copy()
        cached['time'] = NOW + timedelta(seconds=30)
        repeated = evaluate_collected_history(rows + [cached], NOW + timedelta(seconds=30), pending)
        self.assertEqual(repeated['stability'], 'AWAITING_CONFIRMATION')
        rows.append(row(time=NOW + timedelta(minutes=1), working_mode='Line Mode', battery_voltage=26.0,
                        battery_current_dis=0, output_power_w=600))
        result = evaluate_collected_history(rows, NOW + timedelta(minutes=1), pending)
        self.assertEqual(result['decision'], 'HYPOTHETICAL_RAISE')
        self.assertEqual(result['proposed_voltage'], 26.5)
        self.assertEqual(result['time_provenance'], 'COLLECTION_TIME_ONLY')
        self.assertIn('HISTORICAL_BASELINE_ONLY', result['reason_codes'])
        self.assertIn('BMS_LIMITS_UNKNOWN', result['safety_blockers'])
        self.assertEqual(result['actionability'], 'NON_ACTIONABLE')
        self.assertFalse(result['hardware_write_attempted'])
        self.assertIsNone(result['observation_time'])

    def test_charging_lower_requires_confirmation_and_respects_cooldown(self):
        rows = [row(30-i*5, battery_voltage=26.8+i*.03, battery_current_chg=5+i,
                    output_power_w=500+i*10, pv_power_w=500+i*20) for i in range(7)]
        first = evaluate_collected_history(rows, NOW, historical_battery_v=27.0)
        self.assertEqual(first['decision'], 'HOLD')
        self.assertEqual(first['stability'], 'AWAITING_CONFIRMATION')
        rows.append(row(time=NOW+timedelta(minutes=1), battery_voltage=27.04, battery_current_chg=13, output_power_w=600, pv_power_w=700))
        second = evaluate_collected_history(rows, NOW+timedelta(minutes=1), first, historical_battery_v=27.0)
        self.assertEqual(second['decision'], 'HYPOTHETICAL_LOWER')
        self.assertEqual(second['proposed_voltage'], 26.5)
        self.assertIn('BMS_LIMITS_UNKNOWN', second['safety_blockers'])
        state = dict(second, proposal_state={**second['proposal_state'], 'active': 'different', 'last_change_at': NOW.isoformat()})
        rows.append(row(time=NOW+timedelta(minutes=2), battery_voltage=27.05, battery_current_chg=14, output_power_w=610, pv_power_w=710))
        blocked = evaluate_collected_history(rows, NOW+timedelta(minutes=2), state, historical_battery_v=27.0)
        self.assertEqual(blocked['decision'], 'HOLD')
        self.assertEqual(blocked['stability'], 'COOLDOWN')

    def test_replayed_identical_cloud_values_cannot_support_hypothesis(self):
        rows = [row(50-i*10, working_mode='Line Mode' if i % 2 == 0 else 'Invert Mode',
                    battery_voltage=26 if i % 2 == 0 else 25.4,
                    battery_current_dis=0 if i % 2 == 0 else 15, pv_power_w=200, output_power_w=800)
                for i in range(6)]
        self.assertEqual(evaluate_collected_history(rows, NOW)['decision'], 'INSUFFICIENT_DATA')

    def test_collection_time_is_not_observation_or_receipt_time(self):
        result = evaluate_collected_history([row(10), row(0)], NOW)
        self.assertEqual(result['collection_count'], 2)
        self.assertIsNone(result['observation_time'])
        self.assertIsNone(result['receipt_time'])
        self.assertIn('DEVICE_TIMESTAMP_PROVENANCE_UNVERIFIED', result['reason_codes'])
        self.assertIn('CURRENT_THRESHOLD_SETTINGS_UNKNOWN', result['reason_codes'])
        self.assertFalse(result['hardware_write_attempted'])
        self.assertIsNone(result['proposed_voltage'])

    def test_unknown_historical_baseline_is_explained_and_never_actionable(self):
        result = evaluate_collected_history(matched_rows(), NOW, historical_battery_v=None)
        self.assertIn('HISTORICAL_BASELINE_UNKNOWN', result['reason_codes'])
        self.assertEqual(result['actionability'], 'NON_ACTIONABLE')
        self.assertIsNone(result['proposed_voltage'])
        malformed = evaluate_collected_history(matched_rows(), NOW, historical_battery_v='not-a-voltage')
        self.assertIsNone(malformed['historical_settings']['battery'])
        self.assertIsNone(malformed['adaptive_hysteresis']['historical_reference_width_v'])
        self.assertIsNone(malformed['virtual_threshold_experiment']['recommendation'])

    def test_recent_changes_are_explicitly_untrusted_and_missing_data_is_visible(self):
        result = evaluate_collected_history([row(200), row(30), row(20, working_mode='Invert Mode'),
                                             row(10, battery_current_dis=None, pv_power_w=None)], NOW)
        self.assertEqual(result['collection_count'], 3)
        self.assertEqual(result['recent_reported_mode_changes'], 2)
        self.assertIn('MISSING_PV_POWER_W', result['reason_codes'])
        self.assertIn('MISSING_BATTERY_CURRENT_DIS', result['reason_codes'])
        self.assertIsNone(result['short_cycles'])
        self.assertIsNone(result['sag_estimate_v'])

    def test_idempotency_and_stale_state_change(self):
        first = evaluate_collected_history([row()], NOW)
        self.assertEqual(first['evaluation_id'], evaluate_collected_history([row()], NOW)['evaluation_id'])
        stale = evaluate_collected_history([row()], NOW + timedelta(minutes=6))
        self.assertIn('LATEST_COLLECTION_STALE', stale['reason_codes'])
        self.assertNotEqual(first['evaluation_id'], stale['evaluation_id'])

    def test_empty_future_and_naive_rows_do_not_become_evidence(self):
        future = row(-1)
        naive = row(time=NOW.replace(tzinfo=None))
        result = evaluate_collected_history([future, naive], NOW)
        self.assertIn('NO_RECENT_COLLECTIONS', result['reason_codes'])
        self.assertEqual(result['decision'], 'INSUFFICIENT_DATA')


@unittest.skipUnless(asyncpg is not None and os.environ.get('EASUN_TEST_POSTGRES') == '1', 'requires disposable local test database')
class DiagnosticJournalPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pool = await pg_isolation.create_pool(DSN, min_size=1, max_size=3)
        async with self.pool.acquire() as conn:
            await conn.execute(Path('migrations/001_adaptive_decision_journal.sql').read_text())
            await conn.execute('''CREATE TABLE IF NOT EXISTS inverter_metrics (
                time TIMESTAMPTZ PRIMARY KEY, working_mode TEXT, battery_voltage DOUBLE PRECISION,
                battery_soc DOUBLE PRECISION, battery_current_chg DOUBLE PRECISION,
                battery_current_dis DOUBLE PRECISION, pv_power_w DOUBLE PRECISION, output_power_w DOUBLE PRECISION)''')
            await conn.execute('TRUNCATE inverter_metrics, adaptive_decision_journal')

    async def asyncTearDown(self):
        await self.pool.close()

    async def test_persistence_replay_and_restart(self):
        async with self.pool.acquire() as conn:
            await conn.execute("INSERT INTO inverter_metrics (time,working_mode,battery_voltage) VALUES ($1,'Line Mode',25.9)", NOW)
        self.assertTrue(await run_evaluation(self.pool, NOW))
        self.assertTrue(await run_evaluation(self.pool, NOW))
        async with self.pool.acquire() as conn:
            self.assertEqual(await conn.fetchval('SELECT count(*) FROM adaptive_decision_journal'), 1)
            entry = json.loads(await conn.fetchval('SELECT entry FROM adaptive_decision_journal'))
            self.assertEqual(entry['decision'], 'INSUFFICIENT_DATA')
            self.assertFalse(entry['hardware_write_attempted'])
            self.assertEqual(entry['worker_health']['status'], 'degraded')
            self.assertEqual(entry['worker_health']['last_evaluation_at'], NOW.isoformat())
            self.assertEqual(entry['worker_health']['blocking_reason'], 'VERIFIED_OBSERVATION_SOURCE_UNAVAILABLE')
        # A fresh pool sees the same journal and replay remains idempotent.
        other = await pg_isolation.create_pool(DSN, min_size=1, max_size=2)
        try:
            await run_evaluation(other, NOW)
            async with other.acquire() as conn:
                self.assertEqual(await conn.fetchval('SELECT count(*) FROM adaptive_decision_journal'), 1)
        finally:
            await other.close()

    async def test_consecutive_worker_failures_preserve_proposal_state_after_restart(self):
        initial, extended = _lower_rows()
        async with self.pool.acquire() as conn:
            await conn.executemany('''INSERT INTO inverter_metrics
                (time,working_mode,battery_voltage,battery_soc,battery_current_chg,battery_current_dis,pv_power_w,output_power_w)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8)''', [
                    (item['time'], item['working_mode'], item['battery_voltage'], item['battery_soc'],
                     item['battery_current_chg'], item['battery_current_dis'], item['pv_power_w'], item['output_power_w'])
                    for item in initial])

        first = evaluate_collected_history(initial, NOW, historical_battery_v=27.0)
        self.assertEqual(first['proposal_state']['pending'], 'HYPOTHETICAL_LOWER:26.5')
        self.assertEqual(first['proposal_state']['stable_count'], 1)
        state = {**first['proposal_state'], 'active': 'HYPOTHETICAL_RAISE:26.5',
                 'last_change_at': (NOW-timedelta(minutes=15)).isoformat()}
        first = {**first, 'proposal_state': state}
        async with self.pool.acquire() as conn:
            await conn.execute('''INSERT INTO adaptive_decision_journal (evaluation_id,evaluated_at,entry)
                VALUES ($1,$2,$3::jsonb)''', first['evaluation_id'], NOW, json.dumps(first))

        for seconds in (10, 20):
            self.assertTrue(await _record_worker_failure(
                self.pool, NOW+timedelta(seconds=seconds), 'RuntimeError'))
        async with self.pool.acquire() as conn:
            failures = [json.loads(record['entry']) for record in await conn.fetch(
                'SELECT entry FROM adaptive_decision_journal ORDER BY evaluated_at DESC LIMIT 2')]
        self.assertEqual([entry['proposal_state'] for entry in failures], [state, state])

        await self.pool.close()
        self.pool = await pg_isolation.create_pool(DSN, min_size=1, max_size=3)
        newest = extended[-1]
        async with self.pool.acquire() as conn:
            await conn.execute('''INSERT INTO inverter_metrics
                (time,working_mode,battery_voltage,battery_soc,battery_current_chg,battery_current_dis,pv_power_w,output_power_w)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8)''', newest['time'], newest['working_mode'],
                newest['battery_voltage'], newest['battery_soc'], newest['battery_current_chg'],
                newest['battery_current_dis'], newest['pv_power_w'], newest['output_power_w'])
        self.assertTrue(await run_evaluation(self.pool, NOW+timedelta(minutes=1), historical_battery_v=27.0))
        async with self.pool.acquire() as conn:
            resumed = json.loads(await conn.fetchval(
                'SELECT entry FROM adaptive_decision_journal ORDER BY evaluated_at DESC LIMIT 1'))
        self.assertEqual(resumed['proposal_state']['pending'], 'HYPOTHETICAL_LOWER:26.5')
        self.assertEqual(resumed['proposal_state']['stable_count'], 2)
        self.assertEqual(resumed['proposal_state']['active'], state['active'])
        self.assertEqual(resumed['proposal_state']['last_change_at'], state['last_change_at'])
        self.assertEqual(resumed['stability'], 'COOLDOWN')
        self.assertGreater(resumed['cooldown_remaining_seconds'], 0)
        self.assertFalse(resumed['hardware_write_attempted'])

    async def test_millisecond_spaced_identical_evaluations_reproduce_journal_burst(self):
        async with self.pool.acquire() as conn:
            await conn.execute("INSERT INTO inverter_metrics (time,working_mode,battery_voltage) VALUES ($1,'Line Mode',25.9)", NOW)
        self.assertTrue(await run_evaluation(self.pool, NOW))
        other_replica_pool = await pg_isolation.create_pool(DSN, min_size=1, max_size=2)
        try:
            self.assertTrue(await run_evaluation(other_replica_pool, NOW + timedelta(milliseconds=10)))
            async with self.pool.acquire() as conn:
                entries = [json.loads(record['entry']) for record in await conn.fetch(
                    'SELECT entry FROM adaptive_decision_journal ORDER BY evaluated_at')]
                metric_count = await conn.fetchval('SELECT count(*) FROM inverter_metrics')
        finally:
            await other_replica_pool.close()
        self.assertEqual(metric_count, 1, 'no new telemetry arrived between local evaluations')
        self.assertEqual(len(entries), 2)
        self.assertNotEqual(entries[0]['evaluation_id'], entries[1]['evaluation_id'])
        self.assertEqual(entries[0]['decision'], entries[1]['decision'])
        self.assertEqual(entries[0]['reason_codes'], entries[1]['reason_codes'])
        self.assertEqual(entries[0]['proposal_state'], entries[1]['proposal_state'])

    async def test_mode_run_query_preserves_older_cycling_outside_raw_720_row_tail(self):
        start = NOW - timedelta(hours=3) + timedelta(seconds=1)
        samples = []
        for index in range(800):
            stamp = start + timedelta(seconds=index * 10)
            if index < 80:
                mode = 'Line Mode' if (index // 10) % 2 == 0 else 'Invert Mode'
                voltage = 25.5 + index / 10000
                discharge = 0.0 if mode == 'Line Mode' else 10.0
                load = 500.0 + index
            else:
                mode, voltage, discharge, load = 'Line Mode', 25.5, 0.0, 600.0
            samples.append((stamp, mode, voltage, 50.0, 0.0, discharge, 200.0, load))
        async with self.pool.acquire() as conn:
            await conn.executemany('''INSERT INTO inverter_metrics
                (time,working_mode,battery_voltage,battery_soc,battery_current_chg,battery_current_dis,pv_power_w,output_power_w)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8)''', samples)
            await conn.execute('''INSERT INTO adaptive_decision_journal (evaluation_id,evaluated_at,entry)
                VALUES ('daily-reviewed',$1,$2::jsonb)''', NOW-timedelta(seconds=1), json.dumps({
                    'algorithm_version':'online-exploratory-6',
                    'proposal_state':{'daily_review_date_utc':NOW.date().isoformat()}}))
        self.assertTrue(await run_evaluation(self.pool, NOW))
        async with self.pool.acquire() as conn:
            entries = [json.loads(record['entry']) for record in await conn.fetch(
                'SELECT entry FROM adaptive_decision_journal ORDER BY evaluated_at DESC LIMIT 1')]
            raw_count = await conn.fetchval('SELECT count(*) FROM inverter_metrics')
        entry = entries[0]
        self.assertEqual(raw_count, 800)
        cycling = entry['decision_diagnostics']['cycling']
        self.assertEqual(cycling['reported_mode_changes'], 8)
        self.assertTrue(cycling['detected'])
        mode_coverage = entry['decision_diagnostics']['mode_history_coverage']
        self.assertEqual(mode_coverage['source'], 'POSTGRES_MODE_RUN_BOUNDARIES')
        self.assertEqual(mode_coverage['selected_mode_run_count'], 9)
        self.assertFalse(mode_coverage['truncated'])

    async def test_replica_lock_and_retention(self):
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.fetchval('SELECT pg_advisory_xact_lock($1)', LOCK_ID)
                self.assertFalse(await run_evaluation(self.pool, NOW))
            await conn.execute("INSERT INTO adaptive_decision_journal (evaluation_id,evaluated_at,entry) VALUES ('old',$1,'{}'::jsonb)", NOW - timedelta(days=31))
        self.assertTrue(await run_evaluation(self.pool, NOW))
        async with self.pool.acquire() as conn:
            self.assertEqual(await conn.fetchval("SELECT count(*) FROM adaptive_decision_journal WHERE evaluation_id='old'"), 0)

    async def test_existing_schema_validation(self):
        await validate_journal_schema(self.pool)
        async with self.pool.acquire() as conn:
            await conn.execute('ALTER TABLE adaptive_decision_journal ALTER COLUMN entry DROP NOT NULL')
        try:
            with self.assertRaises(RuntimeError):
                await validate_journal_schema(self.pool)
        finally:
            async with self.pool.acquire() as conn:
                await conn.execute('ALTER TABLE adaptive_decision_journal ALTER COLUMN entry SET NOT NULL')

    async def test_migration_repeats_on_compatible_schema_and_rejects_incompatible_schema(self):
        migration = Path('migrations/001_adaptive_decision_journal.sql').read_text()
        async with self.pool.acquire() as conn:
            await conn.execute('DROP TABLE adaptive_decision_journal')
            await conn.execute('''CREATE TABLE adaptive_decision_journal (
                evaluation_id TEXT PRIMARY KEY, evaluated_at TIMESTAMPTZ NOT NULL,
                entry JSONB NOT NULL, hardware_write_attempted BOOLEAN NOT NULL DEFAULT FALSE)''')
            await conn.execute(migration)
            await conn.execute(migration)
            await validate_journal_schema(self.pool)
            await conn.execute('DROP TABLE adaptive_decision_journal')
            await conn.execute('''CREATE TABLE adaptive_decision_journal (
                evaluation_id TEXT PRIMARY KEY, evaluated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
                entry JSONB NOT NULL, hardware_write_attempted BOOLEAN NOT NULL DEFAULT FALSE)''')
            with self.assertRaises(asyncpg.PostgresError):
                await conn.execute(migration)
            await conn.execute('ROLLBACK')
            self.assertEqual(await conn.fetchval("SELECT data_type FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='adaptive_decision_journal' AND column_name='evaluated_at'"), 'timestamp without time zone')
            await conn.execute('DROP TABLE adaptive_decision_journal')
            await conn.execute(migration)
            await validate_journal_schema(self.pool)

    async def test_verified_sag_results_and_provenance_are_persisted(self):
        learner = SagLearner(sag_configuration())
        self.assertTrue(await run_evaluation(self.pool, NOW, learner=learner,
                                             verified_observations=verified_sag_observations()))
        async with self.pool.acquire() as conn:
            entry = json.loads(await conn.fetchval('SELECT entry FROM adaptive_decision_journal'))
        self.assertEqual(entry['verified_sag']['status'], 'VERIFIED')
        self.assertTrue(entry['verified_sag']['estimate']['usable'])
        self.assertEqual(entry['verified_sag']['estimate']['observation_count'], 4)
        self.assertEqual(entry['verified_sag']['provenance'][0]['source'], 'synthetic-easun-fixture-v1')
        self.assertEqual(entry['evidence_level'], 'EXPLORATORY')
        self.assertEqual(entry['worker_health']['last_successful_evaluation_at'], NOW.isoformat())

    async def test_concurrent_replicas_cannot_count_one_observation_twice(self):
        rows = matched_rows()
        rows[-1].update(output_power_w=3000, pv_power_w=2000, battery_voltage=24.0)
        async with self.pool.acquire() as conn:
            for item in rows:
                await conn.execute('''INSERT INTO inverter_metrics
                    (time,working_mode,battery_voltage,battery_soc,battery_current_chg,battery_current_dis,pv_power_w,output_power_w)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8)''', item['time'], item['working_mode'], item['battery_voltage'],
                    item['battery_soc'], item['battery_current_chg'], item['battery_current_dis'], item['pv_power_w'], item['output_power_w'])
        self.assertTrue(await run_evaluation(self.pool, NOW))
        async with self.pool.acquire() as conn:
            initial = json.loads(await conn.fetchval('SELECT entry FROM adaptive_decision_journal ORDER BY evaluated_at DESC LIMIT 1'))
        self.assertEqual(initial['proposal_state']['stable_count'], 0)

        other = await pg_isolation.create_pool(DSN, min_size=1, max_size=2)
        try:
            async with self.pool.acquire() as conn:
                await conn.execute('''INSERT INTO inverter_metrics
                    (time,working_mode,battery_voltage,battery_soc,battery_current_chg,battery_current_dis,pv_power_w,output_power_w)
                    VALUES ($1,'Line Mode',26,50,0,0,900,1300)''', NOW+timedelta(minutes=1))
                await conn.execute('''INSERT INTO inverter_metrics
                    (time,working_mode,battery_voltage,battery_soc,battery_current_chg,battery_current_dis,pv_power_w,output_power_w)
                    VALUES ($1,'Invert Mode',25.5,50,0,19,925,1330)''', NOW+timedelta(minutes=2))
            await asyncio.gather(run_evaluation(self.pool, NOW+timedelta(minutes=2)),
                                 run_evaluation(other, NOW+timedelta(minutes=2)))
            async with self.pool.acquire() as conn:
                entries = [json.loads(item['entry']) for item in await conn.fetch('SELECT entry FROM adaptive_decision_journal ORDER BY evaluated_at')]
            latest = entries[-1]
            self.assertEqual(latest['decision'], 'HOLD')
            self.assertEqual(latest['proposal_state']['stable_count'], 1)
            self.assertEqual(len(entries), 2)
        finally:
            await other.close()

    async def test_cooldown_survives_worker_restart(self):
        async with self.pool.acquire() as conn:
            for index in range(7):
                at = NOW - timedelta(minutes=30-index*5)
                await conn.execute('''INSERT INTO inverter_metrics
                    (time,working_mode,battery_voltage,battery_soc,battery_current_chg,battery_current_dis,pv_power_w,output_power_w)
                    VALUES ($1,'Line Mode',$2,50,$3,0,$4,$5)''', at, 26.8+index*.03, 5+index, 500+index*20, 500+index*10)
        await run_evaluation(self.pool, NOW, historical_battery_v=27.0)
        async with self.pool.acquire() as conn:
            await conn.execute('''INSERT INTO inverter_metrics
                (time,working_mode,battery_voltage,battery_soc,battery_current_chg,battery_current_dis,pv_power_w,output_power_w)
                VALUES ($1,'Line Mode',27.04,50,13,0,700,600)''', NOW+timedelta(minutes=1))
        await run_evaluation(self.pool, NOW+timedelta(minutes=1), historical_battery_v=27.0)
        async with self.pool.acquire() as conn:
            confirmed = json.loads(await conn.fetchval('SELECT entry FROM adaptive_decision_journal ORDER BY evaluated_at DESC LIMIT 1'))
        self.assertEqual(confirmed['decision'], 'HYPOTHETICAL_LOWER')
        self.assertEqual(confirmed['proposal_state']['active'], 'HYPOTHETICAL_LOWER:26.5')

        await self.pool.close()
        self.pool = await pg_isolation.create_pool(DSN, min_size=1, max_size=3)
        later = NOW + timedelta(minutes=40)
        raise_rows = matched_rows()
        async with self.pool.acquire() as conn:
            for item in raise_rows:
                at = item['time'] + timedelta(minutes=42)
                await conn.execute('''INSERT INTO inverter_metrics
                    (time,working_mode,battery_voltage,battery_soc,battery_current_chg,battery_current_dis,pv_power_w,output_power_w)
                    VALUES ($1,$2,$3,50,$4,$5,$6,$7)''', at, item['working_mode'], item['battery_voltage'],
                    item['battery_current_chg'], item['battery_current_dis'], item['pv_power_w'], item['output_power_w'])
        await run_evaluation(self.pool, NOW+timedelta(minutes=39), historical_battery_v=27.0)
        async with self.pool.acquire() as conn:
            await conn.execute('''INSERT INTO inverter_metrics
                (time,working_mode,battery_voltage,battery_soc,battery_current_chg,battery_current_dis,pv_power_w,output_power_w)
                VALUES ($1,'Line Mode',26,50,0,0,825,1130)''', later)
        other = await pg_isolation.create_pool(DSN, min_size=1, max_size=2)
        try:
            await asyncio.gather(run_evaluation(self.pool, later, historical_battery_v=27.0),
                                 run_evaluation(other, later, historical_battery_v=27.0))
        finally:
            await other.close()
        async with self.pool.acquire() as conn:
            blocked = json.loads(await conn.fetchval('SELECT entry FROM adaptive_decision_journal ORDER BY evaluated_at DESC LIMIT 1'))
        self.assertEqual(blocked['decision'], 'HOLD')
        self.assertEqual(blocked['stability'], 'COOLDOWN')
        self.assertIn('DRY_RUN_COOLDOWN', blocked['reason_codes'])

    async def test_confirmation_survives_new_pool(self):
        async with self.pool.acquire() as conn:
            for i in range(6):
                await conn.execute('''INSERT INTO inverter_metrics (time,working_mode,battery_voltage,battery_current_chg,battery_current_dis,pv_power_w,output_power_w)
                    VALUES ($1,$2,$3,0,$4,200,$5)''', NOW-timedelta(minutes=50-i*10),
                    'Line Mode' if i % 2 == 0 else 'Invert Mode',
                    (25.9+i*.01 if i % 2 == 0 else 25.4+i*.01), 0 if i % 2 == 0 else 15+i, 500+i*20)
        await run_evaluation(self.pool, NOW)
        other = await pg_isolation.create_pool(DSN, min_size=1, max_size=2)
        try:
            async with other.acquire() as conn:
                await conn.execute("INSERT INTO inverter_metrics (time,working_mode,battery_voltage,battery_current_chg,battery_current_dis,pv_power_w,output_power_w) VALUES ($1,'Line Mode',26,0,0,200,600)", NOW+timedelta(minutes=1))
            await run_evaluation(other, NOW+timedelta(minutes=1))
            async with other.acquire() as conn:
                entries = [json.loads(r['entry']) for r in await conn.fetch('SELECT entry FROM adaptive_decision_journal ORDER BY evaluated_at')]
            self.assertEqual([r['decision'] for r in entries], ['HOLD', 'HYPOTHETICAL_RAISE'])
        finally:
            await other.close()


from easun_replay_fixture import production_like_history  # noqa: E402

REPLAY_NOW = datetime(2026, 11, 6, 15, tzinfo=timezone.utc)


def _class_counts(result):
    return {name: item['count'] for name, item in result['decision_diagnostics']['evidence_classes'].items()}


class ProductionLikeReplayTests(unittest.TestCase):
    """Synthetic three-hour replay: 60 Line (56 charging), 67 Invert discharging, cycling."""

    def setUp(self):
        self.rows = production_like_history(REPLAY_NOW)
        self.result = evaluate_collected_history(self.rows, REPLAY_NOW)

    def test_unknown_modes_break_transition_and_run_continuity(self):
        cases = [
            (['Line Mode', None, 'Invert Mode'], 0),
            (['Line Mode', 'Unknown', 'Line Mode'], 0),
            (['Line Mode', 'Invert Mode'], 1),
            (['Line Mode', 'Invert Mode', None, 'Line Mode'], 1),
            (['Line Mode', None, 'Invert Mode', None, 'Line Mode', None,
              'Invert Mode', None, 'Line Mode'], 0),
            (['Line Mode', 'Invert Mode', 'Line Mode', 'Invert Mode', 'Line Mode'], 4),
        ]
        for modes, expected in cases:
            with self.subTest(modes=modes):
                rows = [row(time=REPLAY_NOW-timedelta(seconds=30*(len(modes)-i)), working_mode=mode)
                        for i, mode in enumerate(modes)]
                result = evaluate_collected_history(rows, REPLAY_NOW)
                cycling = result['decision_diagnostics']['cycling']
                self.assertEqual(cycling['reported_mode_changes'], expected)
                self.assertEqual(cycling['reported_mode_changes'], result['recent_reported_mode_changes'])
                self.assertEqual(cycling['detected'], expected == 4)
                self.assertEqual(cycling['confidence_interpretation'], 'HEURISTIC_NOT_CALIBRATED_PROBABILITY')

    def test_identical_payloads_across_unknown_are_not_consecutive_duplicates(self):
        modes = ['Line Mode', 'Line Mode', None, 'Line Mode', 'Line Mode']
        rows = [row(time=REPLAY_NOW-timedelta(seconds=30*(len(modes)-i)), working_mode=mode)
                for i, mode in enumerate(modes)]
        result = evaluate_collected_history(rows, REPLAY_NOW)
        cycling = result['decision_diagnostics']['cycling']
        self.assertEqual(cycling['reported_mode_changes'], 0)
        self.assertEqual(cycling['collections_considered'], 2)
        self.assertEqual(cycling['duplicate_payloads_ignored'], 2)
        self.assertEqual(cycling['complete_runs'], 0)

    def test_cycling_evidence_crosses_quantitative_analysis_cap(self):
        start = REPLAY_NOW - timedelta(seconds=800*10)
        modes = ['Invert Mode', 'Line Mode', 'Invert Mode', 'Line Mode', 'Invert Mode']
        rows = [row(time=start+timedelta(seconds=10*i), working_mode='Line Mode',
                    output_power_w=500+i) for i in range(800)]
        for i, mode in enumerate(modes):
            rows[77+i]['working_mode'] = mode
        # Raw quantitative rows are bounded and the omission is reported; mode evidence
        # comes from the separate mode-run source and is not displaced by that bound.
        in_memory_only = evaluate_collected_history(rows, REPLAY_NOW)
        self.assertTrue(in_memory_only['quantitative_history_truncated'])
        # Only the transitions inside the retained suffix (rows 80-82) are counted.
        self.assertEqual(in_memory_only['recent_reported_mode_changes'], 2)
        self.assertIn('DISCONTINUITY_BEFORE_RETAINED_FROM',
                      in_memory_only['decision_diagnostics']['mode_history_coverage']['continuity'])
        result = evaluate_collected_history(rows, REPLAY_NOW, mode_observations=rows, mode_run_count=7)
        cycling = result['decision_diagnostics']['cycling']
        self.assertGreater(result['decision_diagnostics']['sample_counts']['rejected_raw_row_bound'], 0)
        self.assertEqual(cycling['reported_mode_changes'], 6)
        self.assertEqual(cycling['reported_mode_changes'], result['recent_reported_mode_changes'])
        self.assertTrue(cycling['detected'])

    def test_fixture_matches_the_operator_observed_mix(self):
        line = [r for r in self.rows if r['working_mode'] == 'Line Mode']
        invert = [r for r in self.rows if r['working_mode'] == 'Invert Mode']
        self.assertEqual((len(self.rows), len(line), len(invert)), (127, 60, 67))
        self.assertEqual(sum(r['battery_current_chg'] > 0 for r in line), 56)
        self.assertTrue(all(r['battery_current_dis'] > 0 and r['battery_current_chg'] == 0 for r in invert))

    def test_cycling_is_detected_despite_line_mode_charging(self):
        cycling = self.result['decision_diagnostics']['cycling']
        self.assertTrue(cycling['detected'])
        self.assertGreaterEqual(cycling['reported_mode_changes'], 20)
        self.assertGreaterEqual(cycling['short_runs'], CYCLING_MIN_SHORT_RUNS)
        self.assertGreater(cycling['transition_states'].get('INVERT_TO_LINE:LINE_CHARGING', 0), 0)
        self.assertIn('CYCLING_DETECTED', self.result['reason_codes'])
        # Collection brackets only; never an exact physical switch time.
        self.assertEqual(cycling['time_provenance'], 'COLLECTION_ORDER_ONLY')
        for item in cycling['recent_transitions']:
            self.assertLess(item['bracket_start'], item['bracket_end'])
            self.assertGreater(item['bracket_seconds'], 0)
        self.assertIsNone(self.result['observation_time'])
        self.assertIsNone(self.result['short_cycles'])
        self.assertGreater(self.result['confidence_breakdown']['cycling_detection'], 0.5)

    def test_cycling_is_detected_even_when_voltage_analysis_is_unavailable(self):
        rows = self.rows[:-1] + [{**self.rows[-1], 'battery_current_dis': None}]
        result = evaluate_collected_history(rows, REPLAY_NOW)
        self.assertIsNone(result['exploratory_voltage_contrast'])
        self.assertEqual(result['decision'], 'INSUFFICIENT_DATA')
        self.assertTrue(result['decision_diagnostics']['cycling']['detected'])
        self.assertIn('LATEST_COLLECTION_INCOMPLETE_STALE_OR_CONFLICTING',
                      result['decision_diagnostics']['voltage_comparison']['blocked_by'])

    def test_steady_operation_is_not_reported_as_cycling(self):
        rows = [r for r in self.rows if r['working_mode'] == 'Invert Mode']
        result = evaluate_collected_history(rows, REPLAY_NOW)
        self.assertFalse(result['decision_diagnostics']['cycling']['detected'])
        self.assertNotIn('CYCLING_DETECTED', result['reason_codes'])

    def test_return_to_identical_payload_after_other_mode_still_counts_as_a_transition(self):
        line = row(working_mode='Line Mode', battery_voltage=25.9, battery_current_chg=8,
                   battery_current_dis=0, pv_power_w=300, output_power_w=800)
        invert = row(working_mode='Invert Mode', battery_voltage=25.5, battery_current_chg=0,
                     battery_current_dis=12, pv_power_w=300, output_power_w=800)
        modes = (line, invert, line, invert, line)
        rows = [{**sample, 'time': REPLAY_NOW - timedelta(minutes=4-index)}
                for index, sample in enumerate(modes)]

        result = evaluate_collected_history(rows, REPLAY_NOW)
        cycling = result['decision_diagnostics']['cycling']
        self.assertTrue(cycling['detected'])
        self.assertEqual(cycling['reported_mode_changes'], 4)
        self.assertEqual(cycling['collections_considered'], 5)
        self.assertEqual(cycling['duplicate_payloads_ignored'], 0)
        # Quantitative evidence still excludes repeated signatures globally, while
        # cycling detection preserves the return transition in collection order.
        self.assertEqual(result['decision_diagnostics']['sample_counts']['analysed_observations'], 2)

    def test_charging_observations_remain_in_diagnostic_context(self):
        counts = _class_counts(self.result)
        funnel = self.result['decision_diagnostics']['sample_counts']
        # Only cached duplicates and incomplete rows leave; charging rows stay as their own class.
        self.assertEqual(funnel['rejected_analysis_cap'], 0)
        self.assertGreaterEqual(counts['LINE_CHARGING'], 48)
        self.assertEqual(counts['LINE_NOT_CHARGING'], 4)
        classes = self.result['decision_diagnostics']['evidence_classes']
        self.assertGreaterEqual(classes['LINE_CHARGING']['voltage_range_v'][0], 25.78)
        self.assertLessEqual(classes['INVERT_DISCHARGING']['voltage_range_v'][1], 25.75)
        contrast = self.result['exploratory_voltage_contrast']
        self.assertEqual(contrast['primary_exclusions']['LINE_MODE_CHARGING_CONFOUNDED'], counts['LINE_CHARGING'])
        confounded = contrast['charging_confounded_contrast']
        self.assertEqual(confounded['line_charging_observations'], counts['LINE_CHARGING'])
        self.assertGreater(confounded['matched_pairs'], 10)
        self.assertGreater(confounded['median_difference_v'], 0.2)

    def test_old_fixed_cap_no_longer_discards_earlier_transition_evidence(self):
        contrast = self.result['exploratory_voltage_contrast']
        self.assertGreater(contrast['samples'], 24)
        self.assertEqual(contrast['primary_eligible']['line_not_charging'], 4)
        self.assertGreater(contrast['effective_window_seconds'], 2.5 * 3600)

    def test_analysis_memory_remains_bounded(self):
        start = REPLAY_NOW - timedelta(hours=2, minutes=59)
        rows = [row(time=start + timedelta(seconds=13 * i), working_mode='Invert Mode',
                    battery_voltage=25.4 + (i % 30) / 100, battery_current_dis=10 + i / 100,
                    output_power_w=600 + i % 50) for i in range(800)]
        result = evaluate_collected_history(rows, start + timedelta(seconds=13 * 799))
        funnel = result['decision_diagnostics']['sample_counts']
        self.assertEqual(funnel['analysed_observations'], MAX_ANALYSIS_OBSERVATIONS)
        # The raw-row bound now applies before duplicate filtering and is reported
        # separately from Python's post-deduplication analysis cap.
        self.assertEqual(funnel['rejected_raw_row_bound'], 800 - MAX_ANALYSIS_OBSERVATIONS)
        self.assertEqual(funnel['rejected_analysis_cap'], 0)
        self.assertTrue(result['quantitative_history_truncated'])
        self.assertEqual(result['exploratory_voltage_contrast']['samples'], MAX_ANALYSIS_OBSERVATIONS)

    def test_pure_sag_is_never_inferred_from_charging_confounded_observations(self):
        rows = production_like_history(REPLAY_NOW, all_line_charging=True)
        state = None
        for minute in range(4):
            now = REPLAY_NOW + timedelta(minutes=minute)
            extra = [{**rows[-1], 'time': now - timedelta(seconds=20), 'battery_voltage': 25.95 + minute / 100,
                      'battery_current_chg': 20.0 + minute}] if minute else []
            state = evaluate_collected_history(rows + extra, now, state)
            rows = rows + extra
            contrast = state['exploratory_voltage_contrast']
            self.assertIsNone(contrast['contrast_v'])
            self.assertEqual(contrast['comparable_pairs'], 0)
            self.assertGreater(contrast['charging_confounded_contrast']['median_difference_v'], 0.2)
            self.assertFalse(contrast['charging_confounded_contrast']['eligible_for_hypothesis'])
            self.assertNotEqual(state['decision'], 'HYPOTHETICAL_RAISE')
            self.assertIsNone(state['sag_estimate_v'])
            self.assertIsNone(state['rebound_estimate_v'])
            self.assertEqual(state['verified_sag']['status'], 'BLOCKED')
            for code in ('LINE_MODE_CHARGING_CONFOUNDS_VOLTAGE', 'INSUFFICIENT_COMPARABLE_PAIRS',
                         'INSUFFICIENT_HYPOTHESIS_EVIDENCE', 'CYCLING_DETECTED'):
                self.assertIn(code, state['reason_codes'])
            # No comparable pairs: no voltage confidence, not a generic 0.45.
            self.assertIsNone(state['confidence'])
            self.assertIsNone(state['confidence_breakdown']['voltage_contrast'])
            self.assertEqual(state['confidence_breakdown']['basis'], 'NO_VOLTAGE_ESTIMATE')
            self.assertFalse(state['decision_diagnostics']['hypothesis_support']['cycling_used_as_threshold_evidence'])

    def test_every_rejected_sample_and_comparison_has_a_counted_reason(self):
        diagnostics = self.result['decision_diagnostics']
        funnel = diagnostics['sample_counts']
        self.assertEqual(funnel['raw_collections'], 127)
        self.assertEqual(funnel['raw_collections'],
                         funnel['rejected_outside_window_or_invalid_time'] + funnel['in_window_collections'])
        self.assertEqual(funnel['in_window_collections'], funnel['rejected_unknown_mode']
                         + funnel['rejected_missing_or_invalid_numeric'] + funnel['valid_mode_and_numeric'])
        self.assertEqual(funnel['valid_mode_and_numeric'],
                         funnel['rejected_duplicate_cached_payload'] + funnel['unique_observations'])
        self.assertEqual(funnel['unique_observations'], funnel['rejected_analysis_cap'] + funnel['analysed_observations'])
        self.assertEqual(funnel['rejected_duplicate_cached_payload'], 8)
        self.assertEqual(funnel['missing_fields'], {'pv_power_w': 2})
        self.assertEqual(sum(_class_counts(self.result).values()), funnel['analysed_observations'])

        contrast = self.result['exploratory_voltage_contrast']
        rejected = contrast['pair_rejections']
        eligible = contrast['primary_eligible']
        self.assertEqual(eligible['combinations'], eligible['line_not_charging'] * eligible['invert_discharging'])
        self.assertEqual(eligible['combinations'], rejected['TIME_GAP_EXCEEDED'] + rejected['LOAD_NOT_COMPARABLE']
                         + rejected['PV_NOT_COMPARABLE'] + rejected['NOT_SELECTED_ONE_TO_ONE']
                         + contrast['candidate_pairs'])
        self.assertEqual(contrast['candidate_pairs'], contrast['comparable_pairs']
                         + rejected['OUTLIER_REJECTED'] + rejected['INSUFFICIENT_PAIRS'])
        excluded = sum(contrast['primary_exclusions'].get(k, 0) for k in contrast['primary_exclusions']
                       if k not in ('LINE_NOT_CHARGING_WITHOUT_COMPARABLE_PARTNER',
                                    'LINE_NOT_CHARGING_NOT_SELECTED_ONE_TO_ONE'))
        self.assertEqual(excluded + eligible['line_not_charging'] + eligible['invert_discharging'],
                         funnel['analysed_observations'])

    def test_conflicting_and_unknown_current_states_are_separated(self):
        conflicting = {**self.rows[60], 'time': self.rows[60]['time'] + timedelta(seconds=5),
                       'battery_current_chg': 3.0, 'battery_current_dis': 4.0, 'battery_voltage': 25.71}
        invert_idle = {**self.rows[61], 'time': self.rows[61]['time'] + timedelta(seconds=5),
                       'working_mode': 'Invert Mode', 'battery_current_chg': 0.0, 'battery_current_dis': 0.0}
        result = evaluate_collected_history(self.rows + [conflicting, invert_idle], REPLAY_NOW)
        exclusions = result['exploratory_voltage_contrast']['primary_exclusions']
        self.assertEqual(exclusions['CONFLICTING_CURRENT_STATE'], 1)
        self.assertEqual(exclusions['INVERT_MODE_NOT_DISCHARGING'], 1)
        self.assertIn('CONFLICTING_CURRENT', _class_counts(result))

    def test_no_candidate_decision_explains_itself(self):
        diagnostics = self.result['decision_diagnostics']
        self.assertEqual(self.result['decision'], 'HOLD')
        self.assertEqual(self.result['stability'], 'NO_CANDIDATE')
        self.assertIn('INSUFFICIENT_HYPOTHESIS_EVIDENCE', diagnostics['hold_reasons'])
        support = diagnostics['hypothesis_support']
        self.assertFalse(support['supported'])
        self.assertTrue(support['unsupported_because'])
        # The charging rule produced a LOWER candidate that the catalogue rule rejected; it is now visible.
        self.assertEqual(support['blocked_candidate'],
                         {'decision': 'HYPOTHETICAL_LOWER', 'voltage': 25.5, 'reason': 'CATALOGUE_OR_HYSTERESIS_BLOCK'})
        self.assertFalse(support['raise_requirements']['contrast_at_least_0_2_v'])
        # Contrast exists here, so its confidence is a voltage-contrast confidence with that basis.
        self.assertEqual(self.result['confidence_breakdown']['basis'], 'VOLTAGE_CONTRAST')
        self.assertEqual(self.result['confidence'], self.result['confidence_breakdown']['voltage_contrast'])
        self.assertIn('Frequent switching alone does not establish', self.result['explanation'])
        self.assertIn('not verified sag', self.result['explanation'])

    def test_legitimate_comparable_evidence_still_yields_a_bounded_hypothesis(self):
        rows = production_like_history(REPLAY_NOW, non_charging_runs=range(11), rest_voltage=25.95)
        first = evaluate_collected_history(rows, REPLAY_NOW)
        contrast = first['exploratory_voltage_contrast']
        self.assertGreaterEqual(contrast['comparable_pairs'], MIN_CANDIDATE_PAIRS)
        self.assertGreater(contrast['contrast_v'], 0.2)
        self.assertLessEqual(abs(contrast['contrast_v']), 2.0)
        self.assertEqual(first['candidate_voltage'], 26.5)
        self.assertIsNone(first['proposed_voltage'])
        self.assertEqual(first['stability'], 'AWAITING_COMPATIBLE_OBSERVATION')
        self.assertEqual(first['confidence_breakdown']['basis'], 'VOLTAGE_CONTRAST')
        self.assertTrue(first['decision_diagnostics']['hypothesis_support']['supported'])
        self.assertEqual(first['actionability'], 'NON_ACTIONABLE')

    def test_previous_version_state_keeps_cooldown_durable_across_upgrade(self):
        rows = [row(30-i*5, battery_voltage=26.8+i*.03, battery_current_chg=5+i,
                    output_power_w=500+i*10, pv_power_w=500+i*20) for i in range(7)]
        first = evaluate_collected_history(rows, NOW, historical_battery_v=27.0)
        rows.append(row(time=NOW+timedelta(minutes=1), battery_voltage=27.04, battery_current_chg=13,
                        output_power_w=600, pv_power_w=700))
        second = evaluate_collected_history(rows, NOW+timedelta(minutes=1), first, historical_battery_v=27.0)
        self.assertEqual(second['decision'], 'HYPOTHETICAL_LOWER')
        legacy = dict(second, algorithm_version='online-exploratory-4',
                      proposal_state={**second['proposal_state'], 'active': 'different'})
        rows.append(row(time=NOW+timedelta(minutes=2), battery_voltage=27.05, battery_current_chg=14,
                        output_power_w=610, pv_power_w=710))
        blocked = evaluate_collected_history(rows, NOW+timedelta(minutes=2), legacy, historical_battery_v=27.0)
        self.assertEqual(blocked['stability'], 'COOLDOWN')
        self.assertGreater(blocked['cooldown_remaining_seconds'], 3000)
        unknown = dict(legacy, algorithm_version='online-exploratory-3')
        reset = evaluate_collected_history(rows, NOW+timedelta(minutes=2), unknown, historical_battery_v=27.0)
        self.assertNotEqual(reset['stability'], 'COOLDOWN')

    def test_collection_replay_never_reaches_verified_sag_learner(self):
        learner = SagLearner(sag_configuration())
        with patch.object(learner, 'observe') as observe:
            result = evaluate_collected_history(self.rows, REPLAY_NOW, learner=learner)
        observe.assert_not_called()
        self.assertEqual(result['verified_sag']['status'], 'BLOCKED')
        self.assertEqual(result['evidence_level'], 'EXPLORATORY')

    def test_every_replay_outcome_is_non_actionable_without_hardware_writes(self):
        variants = (self.rows, production_like_history(REPLAY_NOW, all_line_charging=True),
                    production_like_history(REPLAY_NOW, non_charging_runs=range(11), rest_voltage=25.95))
        for rows in variants:
            result = evaluate_collected_history(rows, REPLAY_NOW)
            self.assertEqual(result['actionability'], 'NON_ACTIONABLE')
            self.assertFalse(result['hardware_write_attempted'])
            self.assertEqual(result['current_settings']['provenance'], 'unknown')
            json.dumps(result, allow_nan=False)


@unittest.skipUnless(asyncpg is not None and os.environ.get('EASUN_TEST_POSTGRES') == '1', 'requires disposable local test database')
class ReplayJournalPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pool = await pg_isolation.create_pool(DSN, min_size=1, max_size=3)
        async with self.pool.acquire() as conn:
            await conn.execute(Path('migrations/001_adaptive_decision_journal.sql').read_text())
            await conn.execute('''CREATE TABLE IF NOT EXISTS inverter_metrics (
                time TIMESTAMPTZ PRIMARY KEY, working_mode TEXT, battery_voltage DOUBLE PRECISION,
                battery_soc DOUBLE PRECISION, battery_current_chg DOUBLE PRECISION,
                battery_current_dis DOUBLE PRECISION, pv_power_w DOUBLE PRECISION, output_power_w DOUBLE PRECISION)''')
            await conn.execute('TRUNCATE inverter_metrics, adaptive_decision_journal')

    async def asyncTearDown(self):
        await self.pool.close()

    async def test_battery_observation_query_uses_bounded_unique_timestamp_keyset(self):
        from app import web_queries
        stamps = [REPLAY_NOW-timedelta(minutes=2), REPLAY_NOW-timedelta(minutes=1), REPLAY_NOW]
        async with self.pool.acquire() as conn:
            await conn.executemany('''INSERT INTO inverter_metrics
                (time,working_mode,battery_voltage,battery_current_chg,battery_current_dis,pv_power_w,output_power_w)
                VALUES ($1,'Line Mode',25.5,0,0,200,600)''', [(stamp,) for stamp in stamps])
        with pg_isolation.app_connections():  # web_queries opens raw asyncpg connections
            first = await web_queries.battery_observations(DSN, REPLAY_NOW-timedelta(hours=1), REPLAY_NOW, 2)
            self.assertEqual([item['time'] for item in first], stamps[:0:-1])
            second = await web_queries.battery_observations(DSN, REPLAY_NOW-timedelta(hours=1), REPLAY_NOW, 2,
                                                             first[-1]['time'])
        self.assertEqual([item['time'] for item in second], [stamps[0]])

    async def test_daily_review_persists_once_per_utc_day(self):
        samples=[]
        for index in range(288):
            at=REPLAY_NOW-timedelta(hours=24)+timedelta(minutes=5*index)
            samples.append((at,'Invert Mode',25.0+index/10000,50.0,0.0,8.0,200.0,700.0))
        async with self.pool.acquire() as conn:
            await conn.executemany('''INSERT INTO inverter_metrics
                (time,working_mode,battery_voltage,battery_soc,battery_current_chg,battery_current_dis,pv_power_w,output_power_w)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8)''',samples)
        self.assertTrue(await run_evaluation(self.pool,REPLAY_NOW))
        async with self.pool.acquire() as conn:
            first=json.loads(await conn.fetchval('SELECT entry FROM adaptive_decision_journal ORDER BY evaluated_at DESC LIMIT 1'))
            extra=row(time=REPLAY_NOW+timedelta(minutes=5),working_mode='Invert Mode',battery_voltage=25.9,
                      battery_current_dis=9)
            await conn.execute('''INSERT INTO inverter_metrics
                (time,working_mode,battery_voltage,battery_soc,battery_current_chg,battery_current_dis,pv_power_w,output_power_w)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8)''',extra['time'],extra['working_mode'],extra['battery_voltage'],
                extra['battery_soc'],extra['battery_current_chg'],extra['battery_current_dis'],extra['pv_power_w'],extra['output_power_w'])
        self.assertEqual(first['daily_behavior_review']['collection_count'],288)
        self.assertEqual(first['daily_behavior_review']['review_date_utc'],REPLAY_NOW.date().isoformat())
        self.assertTrue(await run_evaluation(self.pool,REPLAY_NOW+timedelta(minutes=5)))
        async with self.pool.acquire() as conn:
            second=json.loads(await conn.fetchval('SELECT entry FROM adaptive_decision_journal ORDER BY evaluated_at DESC LIMIT 1'))
        self.assertEqual(second['daily_behavior_review'],first['daily_behavior_review'])
        self.assertEqual(second['proposal_state']['daily_review_date_utc'],REPLAY_NOW.date().isoformat())

    async def test_daily_review_detects_and_reports_row_bound_truncation(self):
        samples=[]
        start=REPLAY_NOW-timedelta(hours=24)+timedelta(seconds=1)
        interval=timedelta(seconds=(24*60*60-2)/2000)
        for index in range(2001):
            at=start+interval*index
            samples.append((at,'Invert Mode',25.0+index/100000,50.0,0.0,8.0,200.0,700.0))
        async with self.pool.acquire() as conn:
            await conn.executemany('''INSERT INTO inverter_metrics
                (time,working_mode,battery_voltage,battery_soc,battery_current_chg,battery_current_dis,pv_power_w,output_power_w)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8)''',samples)
        self.assertTrue(await run_evaluation(self.pool,REPLAY_NOW))
        async with self.pool.acquire() as conn:
            entry=json.loads(await conn.fetchval('SELECT entry FROM adaptive_decision_journal ORDER BY evaluated_at DESC LIMIT 1'))
        review=entry['daily_behavior_review']
        self.assertEqual(review['collection_count'],2000)
        self.assertTrue(review['history_truncated_at_bound'])
        self.assertEqual(review['coverage'],'TRUNCATED_AT_ROW_BOUND')

    async def test_replay_diagnostics_persist_and_upgrade_keeps_legacy_state(self):
        async with self.pool.acquire() as conn:
            await conn.executemany('''INSERT INTO inverter_metrics
                (time,working_mode,battery_voltage,battery_soc,battery_current_chg,battery_current_dis,pv_power_w,output_power_w)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8)''', [
                (r['time'], r['working_mode'], r['battery_voltage'], r['battery_soc'], r['battery_current_chg'],
                 r['battery_current_dis'], r['pv_power_w'], r['output_power_w']) for r in production_like_history(REPLAY_NOW)])
            legacy = {'algorithm_version': 'online-exploratory-4', 'proposal_state': {
                'active': 'HYPOTHETICAL_RAISE:26.5', 'last_change_at': (REPLAY_NOW - timedelta(minutes=10)).isoformat()}}
            await conn.execute('INSERT INTO adaptive_decision_journal (evaluation_id,evaluated_at,entry) VALUES ($1,$2,$3::jsonb)',
                               'legacy', REPLAY_NOW - timedelta(minutes=1), json.dumps(legacy))
        self.assertTrue(await run_evaluation(self.pool, REPLAY_NOW))
        async with self.pool.acquire() as conn:
            entry = json.loads(await conn.fetchval('SELECT entry FROM adaptive_decision_journal ORDER BY evaluated_at DESC LIMIT 1'))
            self.assertEqual(await conn.fetchval('SELECT count(*) FROM adaptive_decision_journal WHERE hardware_write_attempted'), 0)
        self.assertEqual(entry['algorithm_version'], 'online-exploratory-6')
        self.assertEqual(entry['daily_behavior_review']['review_date_utc'],REPLAY_NOW.date().isoformat())
        self.assertEqual(entry['decision'], 'HOLD')
        self.assertTrue(entry['decision_diagnostics']['cycling']['detected'])
        self.assertEqual(entry['decision_diagnostics']['sample_counts']['raw_collections'], 127)
        self.assertIn('CYCLING_DETECTED', entry['reason_codes'])
        self.assertEqual(entry['proposal_state']['active'], 'HYPOTHETICAL_RAISE:26.5')
        self.assertGreater(entry['cooldown_remaining_seconds'], 2900)
        self.assertEqual(entry['actionability'], 'NON_ACTIONABLE')
        self.assertFalse(entry['hardware_write_attempted'])


def _quant_rows(count, *, spacing_s=14, duplicate=False, oldest_override=None):
    """``count`` in-window rows ending at NOW; optionally identical cached payloads."""
    rows = []
    for index in range(count):
        at = NOW - timedelta(seconds=spacing_s * (count - 1 - index))
        rows.append(row(time=at, battery_voltage=25.9 if duplicate else 25.0 + index / 10000,
                        output_power_w=800.0 if duplicate else 500.0 + index))
    if oldest_override:
        rows[0].update(oldest_override)
    return rows


class QuantitativeHistoryTruncationTests(unittest.TestCase):
    def _coverage(self, result):
        coverage = result['quantitative_history_coverage']
        self.assertEqual(coverage, result['decision_diagnostics']['quantitative_history_coverage'])
        return coverage

    def test_fewer_than_bound_is_not_truncated(self):
        result = evaluate_collected_history(_quant_rows(100), NOW)
        coverage = self._coverage(result)
        self.assertFalse(result['quantitative_history_truncated'])
        self.assertFalse(coverage['older_observations_omitted'])
        self.assertEqual(coverage['coverage'], 'ALL_STORED_ROWS_IN_WINDOW')
        self.assertNotIn('QUANTITATIVE_HISTORY_TRUNCATED', result['reason_codes'])

    def test_exactly_bound_is_not_truncated(self):
        result = evaluate_collected_history(_quant_rows(MAX_ANALYSIS_OBSERVATIONS), NOW)
        coverage = self._coverage(result)
        self.assertFalse(coverage['raw_row_truncated'])
        self.assertEqual(coverage['raw_rows_used'], MAX_ANALYSIS_OBSERVATIONS)
        self.assertEqual(result['decision_diagnostics']['sample_counts']['rejected_raw_row_bound'], 0)

    def test_one_over_bound_is_truncated_and_keeps_newest_in_order(self):
        rows = _quant_rows(MAX_ANALYSIS_OBSERVATIONS + 1)
        result = evaluate_collected_history(list(reversed(rows)), NOW)
        coverage = self._coverage(result)
        self.assertTrue(result['quantitative_history_truncated'])
        self.assertTrue(coverage['older_observations_omitted'])
        self.assertTrue(coverage['in_window_rows_seen_is_lower_bound'])
        self.assertEqual(coverage['raw_rows_used'], MAX_ANALYSIS_OBSERVATIONS)
        self.assertEqual(coverage['covered_from'], rows[1]['time'].isoformat())
        self.assertEqual(coverage['coverage'], 'PARTIAL_WINDOW_RAW_ROW_BOUND')
        self.assertGreater(coverage['uncovered_window_seconds'], 0)
        self.assertEqual(result['decision_diagnostics']['sample_counts']['rejected_raw_row_bound'], 1)
        self.assertEqual(result['collection_count'], MAX_ANALYSIS_OBSERVATIONS)
        self.assertIn('QUANTITATIVE_HISTORY_TRUNCATED', result['reason_codes'])
        self.assertIn('Quantitative history is truncated', result['explanation'])
        self.assertTrue(result['decision_diagnostics']['long_context']['history_truncated'])
        self.assertNotEqual(result['decision_diagnostics']['long_context']['freshness_basis'],
                            'THREE_HOUR_SUPPORTING_HISTORY')

    def test_many_cached_duplicates_over_bound_are_still_truncated(self):
        result = evaluate_collected_history(_quant_rows(1000, spacing_s=10, duplicate=True), NOW)
        coverage = self._coverage(result)
        samples = result['decision_diagnostics']['sample_counts']
        self.assertTrue(coverage['raw_row_truncated'])
        self.assertEqual(samples['rejected_raw_row_bound'], 1000 - MAX_ANALYSIS_OBSERVATIONS)
        self.assertEqual(samples['unique_observations'], 1)
        # Python's post-deduplication cap is reported separately from the raw-row bound.
        self.assertEqual(coverage['analysis_cap_rejected'], 0)
        self.assertEqual(coverage['coverage'], 'PARTIAL_WINDOW_RAW_ROW_BOUND')

    def test_omitted_older_meaningful_observation_is_reported(self):
        rows = _quant_rows(MAX_ANALYSIS_OBSERVATIONS + 1, oldest_override={
            'working_mode': 'Invert Mode', 'battery_current_dis': 20.0, 'battery_voltage': 24.1})
        result = evaluate_collected_history(rows, NOW)
        self.assertNotIn('INVERT_DISCHARGING', result['decision_diagnostics']['evidence_classes'])
        self.assertTrue(self._coverage(result)['older_observations_omitted'])
        self.assertIn('older observations', self._coverage(result)['interpretation'].lower())

    def test_empty_history_is_not_truncated(self):
        result = evaluate_collected_history([], NOW)
        coverage = self._coverage(result)
        self.assertFalse(coverage['raw_row_truncated'])
        self.assertEqual(coverage['coverage'], 'NO_ROWS')
        self.assertIsNone(coverage['covered_from'])
        self.assertEqual(result['decision'], 'INSUFFICIENT_DATA')
        json.dumps(result, allow_nan=False)

    def test_unavailable_database_raises_instead_of_reporting_coverage(self):
        class BrokenPool:
            def acquire(self):
                raise OSError('database unavailable')
        with self.assertRaises(OSError):
            asyncio.run(run_evaluation(BrokenPool(), NOW))

    def test_api_export_includes_quantitative_coverage(self):
        from app.web_routes import _decision_export
        exported = _decision_export(evaluate_collected_history(_quant_rows(MAX_ANALYSIS_OBSERVATIONS + 1), NOW))
        self.assertTrue(exported['quantitative_history_truncated'])
        self.assertTrue(exported['quantitative_history_coverage']['older_observations_omitted'])


class ModeContinuityEvaluationTests(unittest.TestCase):
    def test_suffix_mode_rows_never_count_transition_into_oldest_retained_run(self):
        suffix = [row(30, working_mode='Invert Mode', battery_current_dis=10.0),
                  row(20, working_mode='Invert Mode', battery_current_dis=11.0),
                  row(10, working_mode='Line Mode'), row(5, working_mode='Line Mode', battery_voltage=26.0)]
        result = evaluate_collected_history(suffix, NOW, mode_observations=suffix,
                                            mode_history_truncated=True, mode_run_count=4)
        self.assertEqual(result['recent_reported_mode_changes'], 1)
        self.assertEqual(result['decision_diagnostics']['cycling']['reported_mode_changes'], 1)
        coverage = result['decision_diagnostics']['mode_history_coverage']
        self.assertEqual(coverage['selection'], 'CONTIGUOUS_RECENT_SUFFIX')
        self.assertTrue(coverage['truncated'])
        self.assertIn('DISCONTINUITY_BEFORE_RETAINED_FROM', coverage['continuity'])
        self.assertEqual(coverage['retained_from'], suffix[0]['time'].isoformat())
        self.assertIn('MODE_HISTORY_TRUNCATED', result['reason_codes'])


@unittest.skipUnless(asyncpg is not None and os.environ.get('EASUN_TEST_POSTGRES') == '1', 'requires disposable local test database')
class HistoryTruncationPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pool = await pg_isolation.create_pool(DSN, min_size=1, max_size=3)
        async with self.pool.acquire() as conn:
            await conn.execute(Path('migrations/001_adaptive_decision_journal.sql').read_text())
            await conn.execute('''CREATE TABLE IF NOT EXISTS inverter_metrics (
                time TIMESTAMPTZ PRIMARY KEY, working_mode TEXT, battery_voltage DOUBLE PRECISION,
                battery_soc DOUBLE PRECISION, battery_current_chg DOUBLE PRECISION,
                battery_current_dis DOUBLE PRECISION, pv_power_w DOUBLE PRECISION, output_power_w DOUBLE PRECISION)''')
            await conn.execute('TRUNCATE inverter_metrics, adaptive_decision_journal')

    async def asyncTearDown(self):
        await self.pool.close()

    async def _insert_runs(self, runs, *, start_minutes=170, step_minutes=1, samples_per_run=3, cached=False):
        """``runs``: list of modes (None = unknown). Optional per-run (mode, gap_minutes) tuples."""
        rows, minute = [], start_minutes
        for run_index, spec in enumerate(runs):
            mode, gap = spec if isinstance(spec, tuple) else (spec, 0)
            minute -= gap
            for sample in range(samples_per_run):
                voltage = 25.5 if cached else 25.0 + run_index / 100 + sample / 1000
                rows.append((NOW - timedelta(minutes=minute), mode, voltage, 50.0, 0.0,
                             0.0 if mode == 'Line Mode' else 10.0, 200.0, 600.0))
                minute -= step_minutes
        async with self.pool.acquire() as conn:
            await conn.executemany('''INSERT INTO inverter_metrics
                (time,working_mode,battery_voltage,battery_soc,battery_current_chg,battery_current_dis,pv_power_w,output_power_w)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8)''', rows)

    async def _changes(self, max_runs):
        from app.control.online_output_learning import WINDOW, _fetch_mode_run_boundaries
        async with self.pool.acquire() as conn:
            mode_rows, total, truncated = await _fetch_mode_run_boundaries(conn, NOW - WINDOW, NOW, max_runs)
        result = evaluate_collected_history([], NOW, mode_observations=mode_rows,
                                            mode_history_truncated=truncated, mode_run_count=total)
        self.assertEqual(result['recent_reported_mode_changes'],
                         result['decision_diagnostics']['cycling']['reported_mode_changes'])
        return result['recent_reported_mode_changes'], total, truncated, mode_rows

    async def test_unknown_between_line_and_invert_is_not_bridged_by_truncation(self):
        await self._insert_runs(['Line Mode', None, 'Invert Mode', 'Line Mode'])
        changes, total, truncated, mode_rows = await self._changes(2)
        self.assertEqual((changes, total, truncated), (1, 4, True))
        self.assertEqual([r['working_mode'] for r in mode_rows][0], 'Invert Mode')

    async def test_line_invert_line_without_truncation_counts_both(self):
        await self._insert_runs(['Line Mode', 'Invert Mode', 'Line Mode'])
        self.assertEqual((await self._changes(720))[:3], (2, 3, False))

    async def test_several_omitted_runs_between_oldest_and_newest(self):
        await self._insert_runs(['Line Mode', 'Invert Mode'] * 3)
        changes, total, truncated, mode_rows = await self._changes(3)
        self.assertEqual((changes, total, truncated), (2, 6, True))
        self.assertEqual(len(mode_rows), 6)

    async def test_unknown_at_truncation_boundary_remains_barrier(self):
        await self._insert_runs(['Line Mode', 'Invert Mode', None, 'Invert Mode', 'Line Mode'])
        changes, _, truncated, mode_rows = await self._changes(3)
        self.assertEqual((changes, truncated), (1, True))
        self.assertIsNone(mode_rows[0]['working_mode'])

    async def test_repeated_cached_payloads(self):
        await self._insert_runs(['Line Mode', None, 'Invert Mode', 'Line Mode'], samples_per_run=8, cached=True)
        self.assertEqual((await self._changes(2))[:3], (1, 4, True))

    async def test_large_time_gaps_do_not_cross_truncation(self):
        await self._insert_runs([('Line Mode', 0), (None, 5), ('Invert Mode', 120), ('Line Mode', 15)])
        # Invert -> Line spans a 16-minute unobserved interval: no supported transition.
        self.assertEqual((await self._changes(2))[:3], (0, 4, True))

    async def test_multiple_replicas_see_identical_bounded_input(self):
        from app.control.online_output_learning import WINDOW, _fetch_mode_run_boundaries
        await self._insert_runs(['Line Mode', None, 'Invert Mode', 'Line Mode'])
        other = await pg_isolation.create_pool(DSN, min_size=1, max_size=2)
        try:
            async def fetch(pool):
                async with pool.acquire() as conn:
                    return await _fetch_mode_run_boundaries(conn, NOW - WINDOW, NOW, 2)
            first, second = await asyncio.gather(fetch(self.pool), fetch(other))
        finally:
            await other.close()
        self.assertEqual([dict(r) for r in first[0]], [dict(r) for r in second[0]])
        self.assertEqual(first[1:], second[1:])

    async def test_run_evaluation_reports_mode_truncation_without_bridging(self):
        from app.control import online_output_learning as learner_module
        await self._insert_runs(['Line Mode', None, 'Invert Mode', 'Line Mode'])
        original = learner_module._fetch_mode_run_boundaries

        async def reduced(conn, lower, upper, max_runs=2):
            return await original(conn, lower, upper, 2)
        with patch.object(learner_module, '_fetch_mode_run_boundaries', reduced):
            self.assertTrue(await run_evaluation(self.pool, NOW))
        async with self.pool.acquire() as conn:
            entry = json.loads(await conn.fetchval(
                'SELECT entry FROM adaptive_decision_journal ORDER BY evaluated_at DESC LIMIT 1'))
        self.assertEqual(entry['recent_reported_mode_changes'], 1)
        self.assertTrue(entry['decision_diagnostics']['mode_history_coverage']['truncated'])
        self.assertIn('MODE_HISTORY_TRUNCATED', entry['reason_codes'])
        self.assertEqual(entry['actionability'], 'NON_ACTIONABLE')
        self.assertFalse(entry['hardware_write_attempted'])

    async def _quantitative_entry(self, count, *, daily_due=False, duplicate=False):
        rows = _quant_rows(count, spacing_s=14, duplicate=duplicate)
        async with self.pool.acquire() as conn:
            await conn.executemany('''INSERT INTO inverter_metrics
                (time,working_mode,battery_voltage,battery_soc,battery_current_chg,battery_current_dis,pv_power_w,output_power_w)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8)''', [
                (r['time'], r['working_mode'], r['battery_voltage'], r['battery_soc'], r['battery_current_chg'],
                 r['battery_current_dis'], r['pv_power_w'], r['output_power_w']) for r in rows])
            if not daily_due:
                await conn.execute('''INSERT INTO adaptive_decision_journal (evaluation_id,evaluated_at,entry)
                    VALUES ('daily-reviewed',$1,$2::jsonb)''', NOW - timedelta(days=1), json.dumps({
                        'algorithm_version': 'online-exploratory-6',
                        'proposal_state': {'daily_review_date_utc': NOW.date().isoformat()}}))
        self.assertTrue(await run_evaluation(self.pool, NOW))
        async with self.pool.acquire() as conn:
            return json.loads(await conn.fetchval(
                'SELECT entry FROM adaptive_decision_journal ORDER BY evaluated_at DESC LIMIT 1'))

    async def test_postgres_fewer_than_bound_not_truncated(self):
        entry = await self._quantitative_entry(300)
        self.assertFalse(entry['quantitative_history_truncated'])
        self.assertEqual(entry['collection_count'], 300)

    async def test_postgres_exactly_bound_not_truncated(self):
        entry = await self._quantitative_entry(MAX_ANALYSIS_OBSERVATIONS)
        self.assertFalse(entry['quantitative_history_truncated'])
        self.assertEqual(entry['decision_diagnostics']['sample_counts']['raw_collections'], MAX_ANALYSIS_OBSERVATIONS)

    async def test_postgres_one_over_bound_truncated(self):
        entry = await self._quantitative_entry(MAX_ANALYSIS_OBSERVATIONS + 1)
        coverage = entry['quantitative_history_coverage']
        self.assertTrue(entry['quantitative_history_truncated'])
        self.assertEqual(entry['decision_diagnostics']['sample_counts']['raw_collections'], MAX_ANALYSIS_OBSERVATIONS + 1)
        self.assertEqual(coverage['raw_rows_used'], MAX_ANALYSIS_OBSERVATIONS)
        self.assertEqual(coverage['covered_from'], (NOW - timedelta(seconds=14 * (MAX_ANALYSIS_OBSERVATIONS - 1))).isoformat())
        self.assertIn('QUANTITATIVE_HISTORY_TRUNCATED', entry['reason_codes'])

    async def test_postgres_cached_duplicates_over_bound_truncated(self):
        entry = await self._quantitative_entry(900, duplicate=True)
        self.assertTrue(entry['quantitative_history_truncated'])
        self.assertEqual(entry['decision_diagnostics']['sample_counts']['unique_observations'], 1)

    async def test_postgres_daily_path_applies_same_raw_bound(self):
        entry = await self._quantitative_entry(MAX_ANALYSIS_OBSERVATIONS + 1, daily_due=True)
        self.assertTrue(entry['quantitative_history_truncated'])
        self.assertEqual(entry['daily_behavior_review']['collection_count'], MAX_ANALYSIS_OBSERVATIONS + 1)
        self.assertFalse(entry['daily_behavior_review']['history_truncated_at_bound'])

    async def test_postgres_empty_history(self):
        entry = await self._quantitative_entry(0)
        self.assertFalse(entry['quantitative_history_truncated'])
        self.assertEqual(entry['quantitative_history_coverage']['coverage'], 'NO_ROWS')


def _clock(hour, minute, second=0):
    return NOW.replace(hour=hour, minute=minute, second=second)


def _mode_point(stamp, mode, payload):
    """Deterministic collection; equal ``payload`` ids produce identical cached payloads."""
    return {'time': stamp, 'working_mode': mode, 'battery_voltage': 25.0 + payload / 100,
            'battery_soc': 50.0, 'battery_current_chg': 0.0,
            'battery_current_dis': 0.0 if mode != 'Invert Mode' else 10.0,
            'pv_power_w': 200.0, 'output_power_w': 600.0 + payload}


def _cadence(start, end, step_minutes, mode, payload_from):
    points, stamp, payload = [], start, payload_from
    while stamp <= end:
        points.append(_mode_point(stamp, mode, payload))
        stamp += timedelta(minutes=step_minutes)
        payload += 1
    return points


class ModeGapContinuityTests(unittest.TestCase):
    def evaluate(self, points, now=NOW):
        result = evaluate_collected_history(points, now)
        coverage = result['decision_diagnostics']['mode_history_coverage']
        self.assertEqual(result['recent_reported_mode_changes'],
                         result['decision_diagnostics']['cycling']['reported_mode_changes'])
        return result, coverage

    def test_two_hour_gap_is_not_a_direct_transition(self):
        result, coverage = self.evaluate([_mode_point(_clock(10, 0), 'Line Mode', 1),
                                          _mode_point(_clock(12, 0), 'Invert Mode', 2)])
        self.assertEqual(result['recent_reported_mode_changes'], 0)
        self.assertEqual(coverage['continuity_status'], 'INTERRUPTED')
        self.assertIn('INTERRUPTED_BY_OBSERVATION_GAP', coverage['continuity_breaks'])
        self.assertEqual(coverage['observation_gaps'], 1)
        self.assertEqual(coverage['max_observation_gap_s'], 7200)
        self.assertEqual(coverage['uncertain_mode_changes_across_gaps'], 1)
        uncertain = coverage['recent_uncertain_mode_changes'][0]
        self.assertEqual((uncertain['from_mode'], uncertain['to_mode'], uncertain['unobserved_seconds']),
                         ('Line Mode', 'Invert Mode', 7200))
        self.assertIn('not a directly observed transition', uncertain['interpretation'])
        self.assertEqual(result['decision_diagnostics']['cycling']['uncertain_mode_changes_across_gaps'], 1)
        self.assertIn('MODE_HISTORY_OBSERVATION_GAP', result['reason_codes'])

    def test_fast_context_does_not_bridge_a_gap(self):
        context = _fast_context([_mode_point(_clock(11, 41), 'Line Mode', 1),
                                 _mode_point(_clock(11, 59), 'Invert Mode', 2)], NOW)
        self.assertEqual(context['reported_mode_changes'], 0)
        self.assertEqual(context['observation_gaps'], 1)
        self.assertEqual(context['uncertain_mode_changes_across_gaps'], 1)
        context = _fast_context([_mode_point(_clock(11, 55), 'Line Mode', 1),
                                 _mode_point(_clock(11, 58), 'Invert Mode', 2)], NOW)
        self.assertEqual((context['reported_mode_changes'], context['observation_gaps']), (1, 0))

    def test_short_interval_transition_is_counted(self):
        result, coverage = self.evaluate([_mode_point(_clock(11, 55), 'Line Mode', 1),
                                          _mode_point(_clock(11, 58), 'Invert Mode', 2)])
        self.assertEqual(result['recent_reported_mode_changes'], 1)
        self.assertEqual(coverage['continuity_status'], 'CONTINUOUS_WITHIN_WINDOW')

    def test_cached_payload_bracket_separates_collection_and_payload_times(self):
        points = [_mode_point(stamp, 'Line Mode', 1) for stamp in
                  (_clock(11, 40), _clock(11, 45), _clock(11, 50), _clock(11, 55), _clock(11, 59))]
        points.append(_mode_point(_clock(12, 0), 'Invert Mode', 2))
        result, _ = self.evaluate(points)
        cycling = result['decision_diagnostics']['cycling']
        transition = cycling['recent_transitions'][-1]
        self.assertEqual(cycling['reported_mode_changes'], 1)
        self.assertEqual(cycling['duplicate_payloads_ignored'], 4)
        self.assertEqual((transition['bracket_start'], transition['bracket_end']),
                         (_clock(11, 59).isoformat(), _clock(12, 0).isoformat()))
        self.assertEqual(transition['last_distinct_payload_before'], _clock(11, 40).isoformat())
        self.assertEqual(transition['payload_unchanged_seconds_before'], 19 * 60)
        self.assertEqual(transition['observation_time_confidence'], 'LOW_CACHED_PAYLOAD_REPEATED_BEFORE_SWITCH')
        self.assertTrue(transition['physical_switch_time'].startswith('UNKNOWN'))
        # Repeats are never independent quantitative measurements.
        self.assertEqual(result['decision_diagnostics']['sample_counts']['unique_observations'], 2)

    def test_reviewer_cached_case_with_collection_gap(self):
        result, coverage = self.evaluate([_mode_point(_clock(11, 40), 'Line Mode', 1),
                                          _mode_point(_clock(11, 59), 'Line Mode', 1),
                                          _mode_point(_clock(12, 0), 'Invert Mode', 2)])
        transition = result['decision_diagnostics']['cycling']['recent_transitions'][-1]
        self.assertEqual(result['recent_reported_mode_changes'], 1)
        self.assertEqual(coverage['observation_gaps'], 1)
        self.assertEqual(coverage['uncertain_mode_changes_across_gaps'], 0)
        self.assertEqual(transition['bracket_start'], _clock(11, 59).isoformat())
        self.assertEqual(transition['last_distinct_payload_before'], _clock(11, 40).isoformat())
        self.assertEqual(transition['observation_time_confidence'], 'LOW_CACHED_PAYLOAD_REPEATED_BEFORE_SWITCH')

    def test_five_minute_cadence_tolerates_one_missed_collection(self):
        one_missed = (_cadence(_clock(11, 20), _clock(11, 30), 5, 'Line Mode', 1)
                      + [_mode_point(_clock(11, 35), 'Invert Mode', 10), _mode_point(_clock(11, 40), 'Invert Mode', 11)]
                      + _cadence(_clock(11, 50), _clock(11, 55), 5, 'Line Mode', 20))
        result, coverage = self.evaluate(one_missed)
        self.assertEqual((result['recent_reported_mode_changes'], coverage['observation_gaps']), (2, 0))
        two_missed = (_cadence(_clock(11, 20), _clock(11, 30), 5, 'Line Mode', 1)
                      + [_mode_point(_clock(11, 35), 'Invert Mode', 10)]
                      + _cadence(_clock(11, 50), _clock(11, 55), 5, 'Line Mode', 20))
        result, coverage = self.evaluate(two_missed)
        self.assertEqual((result['recent_reported_mode_changes'], coverage['observation_gaps'],
                          coverage['uncertain_mode_changes_across_gaps']), (1, 1, 1))


# (name, points, max_runs, changes, gaps, uncertain, total_runs, selected_runs, truncated)
def _gap_scenarios():
    c = _clock
    return [
        ('long_gap', [_mode_point(c(10, 0), 'Line Mode', 1), _mode_point(c(12, 0), 'Invert Mode', 2)],
         720, 0, 1, 1, 2, 2, False),
        ('short_interval', [_mode_point(c(11, 55), 'Line Mode', 1), _mode_point(c(11, 58), 'Invert Mode', 2)],
         720, 1, 0, 0, 2, 2, False),
        ('unknown_between', [_mode_point(c(11, 50), 'Line Mode', 1), _mode_point(c(11, 52), None, 2),
                             _mode_point(c(11, 54), 'Invert Mode', 3)], 720, 0, 0, 0, 3, 3, False),
        ('conflicting_mode_between', [_mode_point(c(11, 50), 'Line Mode', 1),
                                      _mode_point(c(11, 52), 'Fault Mode', 2),
                                      _mode_point(c(11, 54), 'Invert Mode', 3)], 720, 0, 0, 0, 3, 3, False),
        ('gap_then_recent_transition', [_mode_point(c(10, 0), 'Line Mode', 1), _mode_point(c(11, 50), 'Line Mode', 2),
                                        _mode_point(c(11, 55), 'Invert Mode', 3)], 720, 1, 1, 0, 3, 3, False),
        ('line_invert_line', [_mode_point(c(11, 40), 'Line Mode', 1), _mode_point(c(11, 45), 'Invert Mode', 2),
                              _mode_point(c(11, 50), 'Line Mode', 3)], 720, 2, 0, 0, 3, 3, False),
        ('gap_same_mode', [_mode_point(c(10, 0), 'Line Mode', 1), _mode_point(c(12, 0), 'Line Mode', 2)],
         720, 0, 1, 0, 2, 2, False),
        ('cached_before_invert', [_mode_point(c(11, 40), 'Line Mode', 1), _mode_point(c(11, 59), 'Line Mode', 1),
                                  _mode_point(c(12, 0), 'Invert Mode', 2)], 720, 1, 1, 0, 3, 3, False),
        ('null_at_boundaries', [_mode_point(c(11, 0), None, 1), _mode_point(c(11, 2), 'Line Mode', 2),
                                _mode_point(c(11, 4), 'Invert Mode', 3), _mode_point(c(11, 6), None, 4)],
         720, 1, 0, 0, 4, 4, False),
        ('null_after_gap', [_mode_point(c(10, 0), 'Line Mode', 1), _mode_point(c(11, 0), None, 2),
                            _mode_point(c(11, 2), 'Invert Mode', 3)], 720, 0, 1, 0, 3, 3, False),
        ('window_edge_excluded', [_mode_point(NOW - timedelta(hours=3, minutes=1), 'Line Mode', 1),
                                  _mode_point(NOW - timedelta(hours=2, minutes=59), 'Invert Mode', 2)],
         720, 0, 0, 0, 1, 1, False),
        ('window_edge_included', [_mode_point(NOW - timedelta(hours=3), 'Line Mode', 1),
                                  _mode_point(NOW - timedelta(hours=2, minutes=58), 'Invert Mode', 2)],
         720, 1, 0, 0, 2, 2, False),
        ('five_minute_one_missed', _cadence(c(11, 20), c(11, 30), 5, 'Line Mode', 1)
         + [_mode_point(c(11, 35), 'Invert Mode', 10), _mode_point(c(11, 40), 'Invert Mode', 11)]
         + _cadence(c(11, 50), c(11, 55), 5, 'Line Mode', 20), 720, 2, 0, 0, 3, 3, False),
        ('five_minute_two_missed', _cadence(c(11, 20), c(11, 30), 5, 'Line Mode', 1)
         + [_mode_point(c(11, 35), 'Invert Mode', 10)]
         + _cadence(c(11, 50), c(11, 55), 5, 'Line Mode', 20), 720, 1, 1, 1, 3, 3, False),
        ('truncated_multiple_omitted', [_mode_point(c(11, 30) + timedelta(minutes=i), mode, i)
                                        for i, mode in enumerate(['Line Mode', 'Invert Mode'] * 3)],
         3, 2, 0, 0, 6, 3, True),
        ('exactly_max_runs', [_mode_point(c(11, 30) + timedelta(minutes=i), mode, i)
                              for i, mode in enumerate(['Line Mode', 'Invert Mode', 'Line Mode'])],
         3, 2, 0, 0, 3, 3, False),
        ('max_runs_plus_one', [_mode_point(c(11, 30) + timedelta(minutes=i), mode, i)
                               for i, mode in enumerate(['Invert Mode', 'Line Mode', 'Invert Mode', 'Line Mode'])],
         3, 2, 0, 0, 4, 3, True),
        ('gap_created_runs_truncated', [_mode_point(c(10, 0), 'Line Mode', 1), _mode_point(c(11, 0), 'Line Mode', 2),
                                        _mode_point(c(11, 2), 'Invert Mode', 3)], 2, 1, 1, 0, 3, 2, True),
        ('zero_runs', [], 3, 0, 0, 0, 0, 0, False),
        ('one_run', [_mode_point(c(11, 50), 'Line Mode', 1), _mode_point(c(11, 55), 'Line Mode', 2)],
         3, 0, 0, 0, 1, 1, False),
    ]


@unittest.skipUnless(asyncpg is not None and os.environ.get('EASUN_TEST_POSTGRES') == '1', 'requires disposable local test database')
class ModeGapContinuityPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pool = await pg_isolation.create_pool(DSN, min_size=1, max_size=3)
        async with self.pool.acquire() as conn:
            await conn.execute(Path('migrations/001_adaptive_decision_journal.sql').read_text())
            await conn.execute('''CREATE TABLE IF NOT EXISTS inverter_metrics (
                time TIMESTAMPTZ PRIMARY KEY, working_mode TEXT, battery_voltage DOUBLE PRECISION,
                battery_soc DOUBLE PRECISION, battery_current_chg DOUBLE PRECISION,
                battery_current_dis DOUBLE PRECISION, pv_power_w DOUBLE PRECISION, output_power_w DOUBLE PRECISION)''')

    async def asyncTearDown(self):
        await self.pool.close()

    async def test_sql_and_python_continuity_agree(self):
        from app.control.online_output_learning import WINDOW, _fetch_mode_run_boundaries
        for name, points, max_runs, changes, gaps, uncertain, total, selected, truncated in _gap_scenarios():
            with self.subTest(name):
                async with self.pool.acquire() as conn:
                    await conn.execute('TRUNCATE inverter_metrics, adaptive_decision_journal')
                    await conn.executemany('''INSERT INTO inverter_metrics
                        (time,working_mode,battery_voltage,battery_soc,battery_current_chg,battery_current_dis,
                         pv_power_w,output_power_w) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)''', [
                        (p['time'], p['working_mode'], p['battery_voltage'], p['battery_soc'],
                         p['battery_current_chg'], p['battery_current_dis'], p['pv_power_w'],
                         p['output_power_w']) for p in points])
                    mode_rows, total_runs, is_truncated = await _fetch_mode_run_boundaries(
                        conn, NOW - WINDOW, NOW, max_runs)
                result = evaluate_collected_history([], NOW, mode_observations=mode_rows,
                                                    mode_history_truncated=is_truncated, mode_run_count=total_runs)
                coverage = result['decision_diagnostics']['mode_history_coverage']
                cycling = result['decision_diagnostics']['cycling']
                self.assertEqual(result['recent_reported_mode_changes'], changes)
                self.assertEqual(cycling['reported_mode_changes'], changes)
                self.assertEqual(coverage['observation_gaps'], gaps)
                self.assertEqual(coverage['uncertain_mode_changes_across_gaps'], uncertain)
                self.assertEqual((coverage['total_mode_run_count'], coverage['selected_mode_run_count'],
                                  coverage['truncated']), (total, selected, truncated))
                self.assertEqual(coverage['selected_mode_run_count'] + coverage['omitted_older_runs'],
                                 coverage['total_mode_run_count'])
                self.assertLessEqual(coverage['selected_mode_run_count'], max_runs)
                expected_status = 'INTERRUPTED' if (gaps or truncated or any(
                    p['working_mode'] not in MODES_FOR_TEST for p in points
                    if NOW - WINDOW <= p['time'])) else 'CONTINUOUS_WITHIN_WINDOW'
                self.assertEqual(coverage['continuity_status'], expected_status)
                if not truncated:
                    # The raw in-memory path reaches the same conclusions.
                    in_memory = evaluate_collected_history(points, NOW)
                    memory_coverage = in_memory['decision_diagnostics']['mode_history_coverage']
                    self.assertEqual(in_memory['recent_reported_mode_changes'], changes)
                    self.assertEqual(memory_coverage['observation_gaps'], gaps)
                    self.assertEqual(memory_coverage['uncertain_mode_changes_across_gaps'], uncertain)
                    self.assertEqual(memory_coverage['continuity_status'], expected_status)


MODES_FOR_TEST = ('Line Mode', 'Invert Mode')


class ModeGapContinuityInMemoryScenarioTests(unittest.TestCase):
    def test_in_memory_scenarios(self):
        for name, points, _max_runs, changes, gaps, uncertain, *_rest in _gap_scenarios():
            if _rest[-1]:
                continue
            with self.subTest(name):
                result = evaluate_collected_history(points, NOW)
                coverage = result['decision_diagnostics']['mode_history_coverage']
                self.assertEqual(result['recent_reported_mode_changes'], changes)
                self.assertEqual(result['decision_diagnostics']['cycling']['reported_mode_changes'], changes)
                self.assertEqual((coverage['observation_gaps'], coverage['uncertain_mode_changes_across_gaps']),
                                 (gaps, uncertain))


def _lower_rows():
    """In-memory LOWER:26.5 fixture (historical 27.0 V) mirroring the cooldown restart test."""
    def charging(at, voltage, charge, pv, load):
        return row(time=at, working_mode='Line Mode', battery_voltage=voltage, battery_current_chg=charge,
                   battery_current_dis=0, pv_power_w=pv, output_power_w=load)
    initial = [charging(NOW - timedelta(minutes=30 - i * 5), 26.8 + i * .03, 5 + i, 500 + i * 20, 500 + i * 10)
               for i in range(7)]
    return initial, initial + [charging(NOW + timedelta(minutes=1), 27.04, 13, 700, 600)]


def _raise_replay():
    """RAISE:26.5 fixture (historical 26.0 V): returns rows, evaluation time, state count 1."""
    at = datetime(2026, 11, 7, 11, 5, tzinfo=timezone.utc)
    def sample(stamp, mode, voltage, charge, discharge, pv, load):
        return {'time': stamp, 'working_mode': mode, 'battery_voltage': voltage, 'battery_soc': 50,
                'battery_current_chg': charge, 'battery_current_dis': discharge,
                'pv_power_w': pv, 'output_power_w': load}
    def pair(stamp, response, load, pv):
        return [sample(stamp - timedelta(seconds=78), 'Line Mode', 26.0, 0, 0, pv, load),
                sample(stamp, 'Invert Mode', 26.0 - response, 0, 10, pv, load)]
    rows = []
    for age, response, load, pv in ((120, 1.2, 400, 100), (75, .594, 900, 600), (30.32, .594, 1400, 1100)):
        rows.extend(pair(at - timedelta(minutes=age), response, load, pv))
    rows.extend([sample(at - timedelta(minutes=5), 'Line Mode', 25.8, None, 0, None, 700),
                 sample(at - timedelta(minutes=3), 'Invert Mode', None, 0, 10, None, 700),
                 sample(at - timedelta(minutes=1), 'Invert Mode', 25.7, 0, 0, 200, 700)])
    first = evaluate_collected_history(rows, at)
    rows = [item for item in rows if item['time'] != at - timedelta(minutes=5)]
    rows.extend(pair(at + timedelta(minutes=1) - timedelta(minutes=5), .53, 1900, 1600))
    second = evaluate_collected_history(rows, at + timedelta(minutes=1), first)
    rows = rows + pair(at + timedelta(minutes=4), .55, 2500, 2200)
    third = evaluate_collected_history(rows, at + timedelta(minutes=4), second)
    return rows, at + timedelta(minutes=4), third


def _with_pending(previous, pending, count):
    state = dict(previous)
    proposal = dict(previous['proposal_state'], pending=pending, stable_count=count)
    proposal.pop('active', None)
    proposal.pop('last_change_at', None)
    state['proposal_state'] = proposal
    return state


def _journal_roundtrip(entry):
    return json.loads(json.dumps(entry, default=str))


class CandidateConfirmationIsolationTests(unittest.TestCase):
    """Confirmation progress belongs to one exact candidate and one new observation."""

    def assertNotConfirmed(self, result):
        self.assertNotEqual(result['stability'], 'CONFIRMED')
        self.assertEqual(result['decision'], 'HOLD')
        self.assertIsNone(result['proposal_state'].get('active'))

    def test_lower_confirmations_never_confirm_raise_without_new_sample(self):
        rows, at, third = _raise_replay()
        self.assertEqual(third['proposal_state']['pending'], 'HYPOTHETICAL_RAISE:26.5')
        result = evaluate_collected_history(rows, at, _with_pending(third, 'HYPOTHETICAL_LOWER:26.5', 2))
        self.assertEqual(result['candidate_voltage'], 26.5)
        self.assertNotConfirmed(result)
        self.assertEqual(result['stability'], 'AWAITING_COMPATIBLE_OBSERVATION')
        self.assertEqual(result['proposal_state']['stable_count'], 0)
        self.assertIsNone(result['proposal_state']['pending'])
        self.assertEqual(result['confirmation_progress']['confirmed'], 0)

    def test_raise_confirmations_never_confirm_lower_with_or_without_new_sample(self):
        initial, extended = _lower_rows()
        first = evaluate_collected_history(initial, NOW, historical_battery_v=27.0)
        tampered = _with_pending(first, 'HYPOTHETICAL_RAISE:26.5', 2)
        replay = evaluate_collected_history(initial, NOW, tampered, historical_battery_v=27.0)
        self.assertNotConfirmed(replay)
        self.assertEqual(replay['proposal_state']['stable_count'], 0)
        # A genuinely new compatible sample starts the LOWER count afresh at one.
        fresh = evaluate_collected_history(extended, NOW + timedelta(minutes=1), tampered,
                                           historical_battery_v=27.0)
        self.assertNotConfirmed(fresh)
        self.assertEqual(fresh['proposal_state']['pending'], 'HYPOTHETICAL_LOWER:26.5')
        self.assertEqual(fresh['proposal_state']['stable_count'], 1)

    def test_same_candidate_repeated_without_new_telemetry_earns_nothing(self):
        initial, _ = _lower_rows()
        first = evaluate_collected_history(initial, NOW, historical_battery_v=27.0)
        self.assertEqual(first['proposal_state']['stable_count'], 1)
        state = first
        for minutes in (0, 1, 2):
            state = evaluate_collected_history(initial, NOW + timedelta(minutes=minutes), state,
                                               historical_battery_v=27.0)
            self.assertNotConfirmed(state)
            self.assertEqual(state['proposal_state']['stable_count'], 1)

    def test_same_candidate_confirmed_by_independent_compatible_sample(self):
        initial, extended = _lower_rows()
        first = evaluate_collected_history(initial, NOW, historical_battery_v=27.0)
        confirmed = evaluate_collected_history(extended, NOW + timedelta(minutes=1), first,
                                               historical_battery_v=27.0)
        self.assertEqual(confirmed['decision'], 'HYPOTHETICAL_LOWER')
        self.assertEqual(confirmed['stability'], 'CONFIRMED')
        self.assertEqual(confirmed['proposal_state']['active'], 'HYPOTHETICAL_LOWER:26.5')
        self.assertEqual(confirmed['actionability'], 'NON_ACTIONABLE')

    def test_restart_with_pending_confirmation_preserves_exact_progress(self):
        initial, extended = _lower_rows()
        restored = _journal_roundtrip(evaluate_collected_history(initial, NOW, historical_battery_v=27.0))
        replay = evaluate_collected_history(initial, NOW, restored, historical_battery_v=27.0)
        self.assertEqual(replay['proposal_state']['stable_count'], 1)
        self.assertNotConfirmed(replay)
        resumed = evaluate_collected_history(extended, NOW + timedelta(minutes=1), restored,
                                             historical_battery_v=27.0)
        self.assertEqual(resumed['stability'], 'CONFIRMED')

    def test_restart_with_confirmed_cooldown_keeps_active_target_and_cooldown(self):
        initial, extended = _lower_rows()
        first = evaluate_collected_history(initial, NOW, historical_battery_v=27.0)
        confirmed = _journal_roundtrip(evaluate_collected_history(
            extended, NOW + timedelta(minutes=1), first, historical_battery_v=27.0))
        later = NOW + timedelta(minutes=11)
        after = evaluate_collected_history(extended, later, confirmed, historical_battery_v=27.0)
        self.assertEqual(after['proposal_state']['active'], 'HYPOTHETICAL_LOWER:26.5')
        self.assertEqual(after['proposal_state']['last_change_at'], confirmed['proposal_state']['last_change_at'])
        self.assertEqual(after['cooldown_remaining_seconds'], 3000)
        self.assertNotEqual(after['stability'], 'CONFIRMED')
        # A different candidate inheriting the count would bypass cooldown semantics.
        rows, at, third = _raise_replay()
        other = _with_pending(third, 'HYPOTHETICAL_LOWER:26.5', 2)
        other['proposal_state'].update(active='HYPOTHETICAL_LOWER:26.5',
                                       last_change_at=(at - timedelta(minutes=10)).isoformat())
        blocked = evaluate_collected_history(rows, at, other)
        self.assertEqual(blocked['proposal_state']['active'], 'HYPOTHETICAL_LOWER:26.5')
        self.assertNotIn('DRY_RUN_COOLDOWN', blocked['reason_codes'])
        self.assertEqual(blocked['stability'], 'AWAITING_COMPATIBLE_OBSERVATION')
        self.assertEqual(blocked['cooldown_remaining_seconds'], 3000)

    def test_missing_or_malformed_pending_state_never_counts(self):
        initial, _ = _lower_rows()
        first = evaluate_collected_history(initial, NOW, historical_battery_v=27.0)
        base = {key: value for key, value in first['proposal_state'].items()
                if key not in ('pending', 'stable_count', 'pending_response_v')}
        malformed = [{'stable_count': 2}, {'pending': 'garbage', 'stable_count': 2},
                     {'pending': 'HYPOTHETICAL_LOWER:26.5', 'stable_count': 'two'},
                     {'pending': 'HYPOTHETICAL_LOWER:26.5', 'stable_count': True},
                     {'pending': 'HYPOTHETICAL_LOWER:26.5', 'stable_count': 5},
                     {'pending': 'HYPOTHETICAL_LOWER:26.5', 'stable_count': -1},
                     {'pending': ['HYPOTHETICAL_LOWER:26.5'], 'stable_count': 2},
                     {'pending': None, 'stable_count': 2, 'pending_response_v': 'NaN'}]
        for fields in malformed:
            with self.subTest(fields=fields):
                # Without a new sample: nothing may be earned or carried.
                state = dict(first, proposal_state={**base, **fields})
                replay = evaluate_collected_history(initial, NOW, state, historical_battery_v=27.0)
                self.assertNotConfirmed(replay)
                self.assertEqual(replay['proposal_state']['stable_count'], 0)
                self.assertEqual(replay['confirmation_progress']['confirmed'], 0)
                # With a new sample: a fresh count of exactly one.
                fresh = evaluate_collected_history(initial, NOW, {'proposal_state': fields},
                                                   historical_battery_v=27.0)
                self.assertNotConfirmed(fresh)
                self.assertEqual(fresh['proposal_state']['stable_count'], 1)
        not_a_dict = evaluate_collected_history(initial, NOW, {'proposal_state': 'corrupt'},
                                                historical_battery_v=27.0)
        self.assertEqual(not_a_dict['proposal_state']['stable_count'], 1)


class BatteryModelContinuityTests(unittest.TestCase):
    """Reported-channel integration never bridges missing measurements."""

    def model(self, *rows):
        return _battery_response_model(list(rows), NOW)

    def test_complete_coverage(self):
        model = self.model(row(10, battery_voltage=25.0, battery_current_dis=15),
                           row(5, battery_voltage=24.9, battery_current_dis=15),
                           row(0, battery_voltage=24.8, battery_current_dis=15))
        self.assertAlmostEqual(model['discharged_ah_proxy'], 2.5, places=4)
        self.assertEqual(model['throughput_status'], 'OBSERVED_REPORTED_CHANNEL_THROUGHPUT')
        self.assertEqual(model['throughput_coverage'], 'COMPLETE')
        self.assertEqual(model['integration_coverage_ratio'], 1.0)
        self.assertIsNone(model['total_battery_discharged_ah'])

    def test_missing_middle_current_is_not_bridged(self):
        # Audit reproduction: previously reported 2.5 Ah over an unsupported 10 min.
        model = self.model(row(10, battery_voltage=25.0, battery_current_dis=15),
                           row(5, battery_voltage=24.9, battery_current_dis=None),
                           row(0, battery_voltage=24.8, battery_current_dis=15))
        self.assertIsNone(model['discharged_ah_proxy'])
        self.assertIsNone(model['easun_reported_discharged_ah_proxy'])
        self.assertEqual(model['intervals_integrated'], 0)
        self.assertEqual(model['throughput_status'], 'INSUFFICIENT_MEASUREMENT_COVERAGE')
        self.assertEqual(model['excluded_observations']['MISSING_CURRENT'], 1)
        self.assertEqual(model['excluded_interval_seconds'], {'MISSING_CURRENT': 600})
        self.assertEqual(model['unintegrated_seconds'], 600)

    def test_partial_coverage_is_distinct_from_complete(self):
        model = self.model(row(15, battery_voltage=25.1, battery_current_dis=15),
                           row(10, battery_voltage=25.0, battery_current_dis=15),
                           row(5, battery_voltage=24.9, battery_current_dis=None),
                           row(0, battery_voltage=24.8, battery_current_dis=15))
        self.assertEqual(model['throughput_status'], 'PARTIAL_COVERAGE_REPORTED_CHANNEL_THROUGHPUT')
        self.assertEqual(model['throughput_coverage'], 'PARTIAL')
        self.assertAlmostEqual(model['discharged_ah_proxy'], 1.25, places=4)
        self.assertEqual(model['integrated_seconds'], 300)
        self.assertEqual(model['elapsed_collection_seconds'], 900)
        self.assertAlmostEqual(model['integration_coverage_ratio'], 1 / 3, places=3)

    def test_zero_current_is_observed_zero(self):
        model = self.model(row(5, battery_voltage=25.0), row(0, battery_voltage=25.1))
        self.assertEqual(model['throughput_status'], 'OBSERVED_ZERO_REPORTED_CHANNEL_THROUGHPUT')
        self.assertEqual(model['discharged_ah_proxy'], 0.0)
        partial_zero = self.model(row(10, battery_voltage=25.0), row(5, battery_voltage=25.05, battery_current_chg=None),
                                  row(0, battery_voltage=25.1), row(-5, battery_voltage=25.2))
        self.assertEqual(partial_zero['throughput_status'], 'PARTIAL_COVERAGE_ZERO_REPORTED_CHANNEL_THROUGHPUT')

    def test_conflicting_current_rejected_even_when_mode_unknown(self):
        model = self.model(row(5, working_mode=None, battery_voltage=25.0, battery_current_chg=5, battery_current_dis=5),
                           row(0, battery_voltage=25.1, battery_current_dis=10))
        self.assertIsNone(model['discharged_ah_proxy'])
        self.assertEqual(model['rejected_intervals']['CONFLICTING_CURRENT'], 1)

    def test_long_gap(self):
        model = self.model(row(20, battery_voltage=25.0, battery_current_dis=10),
                           row(0, battery_voltage=25.1, battery_current_dis=10))
        self.assertEqual(model['gaps_over_15m'], 1)
        self.assertIsNone(model['discharged_ah_proxy'])

    def test_duplicate_timestamp_with_differing_values_is_ambiguous(self):
        model = self.model(row(10, battery_voltage=25.0, battery_current_dis=10),
                           row(5, battery_voltage=24.9, battery_current_dis=10),
                           row(5, battery_voltage=24.7, battery_current_dis=20),
                           row(0, battery_voltage=24.6, battery_current_dis=20))
        self.assertEqual(model['rejected_intervals']['NON_POSITIVE_INTERVAL'], 1)
        self.assertEqual(model['rejected_intervals']['AMBIGUOUS_DUPLICATE_TIMESTAMP'], 2)
        self.assertIsNone(model['discharged_ah_proxy'])

    def test_out_of_order_input_is_sorted_before_repeat_suppression(self):
        rows = [row(0, battery_voltage=24.8, battery_current_dis=15),
                row(10, battery_voltage=25.0, battery_current_dis=15),
                row(5, battery_voltage=24.9, battery_current_dis=15)]
        self.assertAlmostEqual(self.model(*rows)['discharged_ah_proxy'], 2.5, places=4)

    def test_subsecond_interval_integrates_without_unbounded_slope(self):
        model = self.model(row(time=NOW - timedelta(milliseconds=500), battery_voltage=25.0, battery_current_dis=10),
                           row(time=NOW, battery_voltage=25.1, battery_current_dis=10))
        self.assertEqual(model['intervals_integrated'], 1)
        self.assertEqual(model['recent_intervals'][0]['seconds'], 0.5)
        self.assertIsNone(model['recent_intervals'][0]['voltage_slope_v_per_hour'])
        self.assertEqual(model['recent_intervals'][0]['slope_status'], 'INTERVAL_TOO_SHORT_FOR_SLOPE')
        self.assertIsNone(model['latest_voltage_slope_v_per_hour'])
        self.assertIsNone(model['median_voltage_slope_v_per_hour'])

    def test_fast_context_slope_ignores_near_simultaneous_collections(self):
        rows = [row(time=NOW - timedelta(minutes=5), battery_voltage=25.0, battery_current_dis=10),
                row(time=NOW - timedelta(seconds=1), battery_voltage=25.1, battery_current_dis=11),
                row(time=NOW, battery_voltage=25.3, battery_current_dis=12)]
        # The 1 s pair would read 720 V/h; the latest admissible pair spans 299 s.
        self.assertEqual(_fast_context(rows, NOW)['battery_voltage_slope_v_per_hour'], 1.204)

    def test_fast_context_rejects_negative_and_non_finite_voltage_slopes(self):
        for invalid in (-25.0, float('nan'), float('inf'), float('-inf')):
            with self.subTest(invalid=invalid):
                rows = [row(time=NOW-timedelta(minutes=5), battery_voltage=25.0),
                        row(time=NOW, battery_voltage=invalid)]
                self.assertIsNone(_fast_context(rows, NOW)['battery_voltage_slope_v_per_hour'])

    def test_fast_context_does_not_use_slope_adjacent_to_cached_battery_payload(self):
        rows = [row(time=NOW-timedelta(minutes=5), battery_voltage=25.0, battery_current_dis=10),
                row(time=NOW-timedelta(minutes=3), battery_voltage=25.1, battery_current_dis=10),
                row(time=NOW-timedelta(minutes=1), battery_voltage=25.1, battery_current_dis=10,
                    pv_power_w=900),
                row(time=NOW, battery_voltage=25.3, battery_current_dis=10, pv_power_w=1100)]
        self.assertEqual(_fast_context(rows, NOW)['battery_voltage_slope_v_per_hour'], 3.0)
