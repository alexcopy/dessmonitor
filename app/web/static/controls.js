/* Direct charger-source observations; no reconstructed sessions or energy. */
(function () {
    'use strict';
    var requestVersion = 0;
    var chart = null;

    function fmtDT(value) {
        if (value === null || value === undefined) return '—';
        return new Date(value).toLocaleString('en-GB', {
            year: 'numeric', month: 'short', day: 'numeric',
            hour: '2-digit', minute: '2-digit', second: '2-digit'
        });
    }

    function fmtNumber(value, unit) {
        if (value === null || value === undefined) return '—';
        return value.toFixed(1) + ' ' + unit;
    }

    function cell(row, value) {
        var td = document.createElement('td');
        td.className = 'dm-mono';
        td.textContent = value === null || value === undefined ? '—' : value;
        row.appendChild(td);
        return td;
    }

    function message(body, text) {
        body.replaceChildren();
        var row = document.createElement('tr');
        cell(row, text).colSpan = 12;
        body.appendChild(row);
    }

    function renderObservations(body, observations) {
        body.replaceChildren();
        if (!observations.length) {
            message(body, 'No charger-source observations in this time range.');
            return;
        }
        var fragment = document.createDocumentFragment();
        observations.forEach(function (observation) {
            var row = document.createElement('tr');
            cell(row, fmtDT(observation.time));
            var source = cell(row, observation.charger_priority);
            if (observation.charger_priority_raw !== null && observation.charger_priority_raw !== undefined) {
                var raw = document.createElement('small');
                raw.className = 'is-block';
                raw.textContent = observation.charger_priority_raw;
                source.appendChild(raw);
            }
            cell(row, observation.working_mode);
            cell(row, fmtNumber(observation.battery_voltage, 'V'));
            cell(row, fmtNumber(observation.battery_soc, '%'));
            cell(row, fmtNumber(observation.battery_current_chg, 'A'));
            cell(row, fmtNumber(observation.battery_current_dis, 'A'));
            cell(row, fmtNumber(observation.pv_power_w, 'W'));
            cell(row, fmtNumber(observation.output_power_w, 'W'));
            cell(row, fmtNumber(observation.ac_input_voltage, 'V'));
            cell(row, fmtNumber(observation.output_voltage, 'V'));
            cell(row, fmtNumber(observation.ac_output_load_pct, '%'));
            fragment.appendChild(row);
        });
        body.appendChild(fragment);
    }

    function renderChart(observations) {
        var rows = observations.slice().sort(function (a, b) {
            return Date.parse(a.time) - Date.parse(b.time);
        });
        var states = {solar_only: 0, solar_priority: 1, solar_and_mains: 2};
        var sourceLabels = ['Solar only', 'Solar priority', 'Solar + mains'];
        var current = [], output = [], source = [];
        var previousTime = null, previousSource = null, changes = 0;
        rows.forEach(function (row) {
            var x = Date.parse(row.time);
            var canonical = Object.prototype.hasOwnProperty.call(states, row.charger_priority)
                ? row.charger_priority : null;
            var changed = previousSource !== null && canonical !== null && canonical !== previousSource;
            if (changed) changes += 1;
            // An unmapped raw observation breaks canonical comparison continuity.
            // Do not count a transition into, out of, or across an unknown state.
            previousSource = canonical;
            if (previousTime !== null && x - previousTime > 60 * 60 * 1000) {
                // Null separators break rendering only; they are not measurements.
                var gap = {x: previousTime + 1, y: null};
                current.push(gap); output.push(gap); source.push(gap);
            }
            current.push({x: x, y: row.battery_current_chg == null ? null : row.battery_current_chg, row: row});
            output.push({x: x, y: row.output_power_w == null ? null : row.output_power_w, row: row});
            source.push({x: x, y: Object.prototype.hasOwnProperty.call(states, row.charger_priority)
                ? states[row.charger_priority] : null, row: row, changed: changed});
            previousTime = x;
        });
        document.getElementById('chargeSourceChanges').textContent = 'Observed source changes: ' + changes;
        if (chart) chart.destroy();
        var now = Date.now();
        chart = new Chart(document.getElementById('chargerHistoryChart'), {
            type: 'line',
            data: {datasets: [
                {label: 'Battery charge current (A DC)', data: current, yAxisID: 'current',
                    borderColor: '#ffca28', pointRadius: 1, spanGaps: false},
                {label: 'Inverter output (W)', data: output, yAxisID: 'output',
                    borderColor: '#26a69a', pointRadius: 1, spanGaps: false},
                {label: 'Charger source', data: source, yAxisID: 'source',
                    borderColor: '#42a5f5', stepped: true, spanGaps: false,
                    pointRadius: function (context) { return context.raw && context.raw.changed ? 4 : 0; }}
            ]},
            options: {
                responsive: true, maintainAspectRatio: false, animation: false,
                interaction: {mode: 'nearest', intersect: false},
                scales: {
                    x: {type: 'linear', min: now - 7 * 24 * 60 * 60 * 1000, max: now,
                        ticks: {maxTicksLimit: 8, callback: function (value) {
                            return new Date(value).toLocaleString('en-GB', {
                                day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'
                            });
                        }}},
                    current: {type: 'linear', position: 'left', beginAtZero: true,
                        title: {display: true, text: 'Battery charge current (A DC)'}},
                    output: {type: 'linear', position: 'right', beginAtZero: true,
                        grid: {drawOnChartArea: false}, title: {display: true, text: 'Inverter output (W)'}},
                    source: {type: 'linear', position: 'right', min: 0, max: 2,
                        grid: {drawOnChartArea: false}, ticks: {stepSize: 1,
                            callback: function (value) { return sourceLabels[value]; }}}
                },
                plugins: {tooltip: {callbacks: {
                    title: function (items) { return items.length ? fmtDT(items[0].raw.row.time) : ''; },
                    label: function (context) {
                        var row = context.raw.row;
                        function text(value) { return value == null ? '—' : value; }
                        return [
                            'Charger source: ' + text(row.charger_priority),
                            'Raw charger source: ' + text(row.charger_priority_raw),
                            'Working mode: ' + text(row.working_mode),
                            'Battery voltage: ' + fmtNumber(row.battery_voltage, 'V'),
                            'SOC: ' + fmtNumber(row.battery_soc, '%'),
                            'Battery charge current: ' + fmtNumber(row.battery_current_chg, 'A DC'),
                            'Battery discharge current: ' + fmtNumber(row.battery_current_dis, 'A DC'),
                            'PV power: ' + fmtNumber(row.pv_power_w, 'W'),
                            'Inverter output: ' + fmtNumber(row.output_power_w, 'W'),
                            'AC input voltage: ' + fmtNumber(row.ac_input_voltage, 'V'),
                            'Output voltage: ' + fmtNumber(row.output_voltage, 'V'),
                            'Load: ' + fmtNumber(row.ac_output_load_pct, '%')
                        ];
                    }
                }}}
            }
        });
    }

    async function loadChargeHistory() {
        var body = document.getElementById('chargeObservationsBody');
        var since = document.getElementById('chargeVerifiedSince');
        if (!body || !since) return;
        var version = ++requestVersion;
        message(body, 'Loading…');
        since.textContent = 'Loading charger-source telemetry…';
        try {
            var response = await fetch('/api/charge/history?days=7', {cache: 'no-store'});
            if (!response.ok) throw new Error('Request failed');
            var data = await response.json();
            if (version !== requestVersion) return;
            since.textContent = data.verified_since === null
                ? 'No charger-source telemetry has been recorded yet.'
                : 'Charger-source telemetry recorded since: ' + fmtDT(data.verified_since);
            renderObservations(body, data.observations);
            renderChart(data.observations);
        } catch (error) {
            if (version !== requestVersion) return;
            since.textContent = 'Charger-source telemetry unavailable.';
            document.getElementById('chargeSourceChanges').textContent = 'Observed source changes: —';
            if (chart) { chart.destroy(); chart = null; }
            message(body, 'Failed to load observations. Reload the page to retry.');
        }
    }

    document.addEventListener('DOMContentLoaded', loadChargeHistory);
}());
