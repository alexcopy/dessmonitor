"""Deterministic synthetic three-hour EASUN collection history (no production data).

Shaped after the operator-reported read-only diagnostics: 127 collections, 60 Line
Mode (56 charging, 4 not charging), 67 Invert Mode discharging, frequent reported
Line/Invert alternation, changing load and PV, cached duplicate payloads and
missing fields. Voltages: ~25.4-25.7 V while discharging, ~25.8-26.0 V while Line
Mode charging, and a lower resting value in the first Line sample before the
charger ramps.
"""
import random
from datetime import timedelta

# Eleven Invert runs and eleven Line runs: 67 Invert + 60 Line = 127 collections.
INVERT_RUNS = (6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 7)
LINE_RUNS = (6, 5, 6, 5, 6, 5, 6, 5, 6, 5, 5)
# Line runs whose first collection shows zero charging current (charger not ramped yet).
NON_CHARGING_LINE_RUNS = (1, 4, 7, 9)
# Indices (in collection order) that repeat the previous payload with a new collection time.
CACHED_DUPLICATE_INDICES = (9, 25, 43, 60, 77, 96, 112, 120)
# Indices whose PV reading is missing.
MISSING_PV_INDICES = (30, 88)


def production_like_history(now, *, seed=20261106, non_charging_runs=NON_CHARGING_LINE_RUNS,
                            rest_voltage=25.62, all_line_charging=False):
    rng = random.Random(seed)
    rows = []
    sequence = []
    for invert_len, line_len, run in zip(INVERT_RUNS, LINE_RUNS, range(len(INVERT_RUNS))):
        sequence.extend(('Invert Mode', run, step, invert_len) for step in range(invert_len))
        sequence.extend(('Line Mode', run, step, line_len) for step in range(line_len))
    assert len(sequence) == 127
    # ~85 s collection cadence across three hours, with jitter and two short gaps.
    at = now - timedelta(seconds=127 * 81)
    for index, (mode, run, step, length) in enumerate(sequence):
        at += timedelta(seconds=81 + rng.randint(-6, 6) + (240 if index in (50, 101) else 0))
        minute = index * 1.4
        # Slowly changing household load and a PV ramp with cloud noise.
        load = 650 + 350 * ((index // 9) % 3) + rng.randint(-60, 60)
        pv = max(0.0, 120 + 2.2 * minute + rng.randint(-40, 40))
        if mode == 'Invert Mode':
            # Voltage sags through the discharge run from ~25.7 V towards ~25.4 V.
            voltage = 25.70 - 0.30 * (step / max(1, length - 1)) + rng.uniform(-0.03, 0.03)
            discharge = max(1.0, (load - pv) / voltage * 1.05)
            charge = 0.0
        elif run in non_charging_runs and step == 0 and not all_line_charging:
            # First Line sample, charger not yet ramped: battery relaxing, no charge rise.
            voltage = rest_voltage + rng.uniform(-0.04, 0.04)
            discharge = 0.0
            charge = 0.0
        else:
            # Line Mode charging lifts the terminal voltage to ~25.8-26.0 V.
            voltage = 25.82 + 0.17 * (step / max(1, length - 1)) + rng.uniform(-0.02, 0.02)
            discharge = 0.0
            charge = 8.0 + 3.0 * step + rng.uniform(-1.0, 1.0)
        rows.append({'time': at, 'working_mode': mode, 'battery_voltage': round(voltage, 2),
                     'battery_soc': 55 if mode == 'Line Mode' else 52,
                     'battery_current_chg': round(charge, 1), 'battery_current_dis': round(discharge, 1),
                     'pv_power_w': float(round(pv)), 'output_power_w': float(load)})
    for index in CACHED_DUPLICATE_INDICES:
        previous = rows[index - 1]
        # A cached cloud payload keeps the earlier values, including mode.
        rows[index] = {**previous, 'time': rows[index]['time']}
    for index in MISSING_PV_INDICES:
        rows[index] = {**rows[index], 'pv_power_w': None}
    # Make the newest collection fresh for the evaluator.
    shift = now - timedelta(seconds=40) - rows[-1]['time']
    return [{**item, 'time': item['time'] + shift} for item in rows]
