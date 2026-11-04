/* controls.js — observed inverter output-mode diagnostics */
(function () {
    'use strict';

    var requestVersion = 0;
    var charts = {};
    var allObservations = [];
    var apiTransitions = [];
    var summary24h = {};
    var currentDays = 1;
    var GAP_MS = 60 * 60 * 1000;
    var REFRESH_MS = 60 * 1000;
    var loading = false;
    var hasData = false;

    function fmtDT(value) {
        if (value == null) return '—';
        return new Date(value).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
    }
    function fmtDTFull(value) {
        if (value == null) return '—';
        return new Date(value).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }
    function fmtValue(value, unit, decimals) {
        if (value == null) return '—';
        var number = Number(value);
        return Number.isFinite(number) ? number.toFixed(decimals == null ? 1 : decimals) + ' ' + unit : '—';
    }
    function fmtDuration(seconds) {
        if (seconds == null) return '—';
        var minutes = Math.round(Number(seconds) / 60);
        if (minutes < 1) return '<1 min';
        if (minutes < 60) return minutes + ' min';
        var hours = Math.floor(minutes / 60), remainder = minutes % 60;
        return hours + ' h' + (remainder ? ' ' + remainder + ' min' : '');
    }
    function fmtSignificantDuration(ms) {
        if (ms <= 0) return '<1 min';
        var minutes = Math.round(ms / 60000);
        if (minutes < 60) return minutes + ' min';
        var hours = Math.floor(minutes / 60), remainder = minutes % 60;
        return hours + ' h' + (remainder ? ' ' + remainder + ' min' : '');
    }

    var SOURCE_LABEL = { solar_only: 'Solar only', solar_priority: 'Solar priority', solar_and_mains: 'Solar + mains' };
    var SOURCE_COLOR = { solar_only: '#26a69a', solar_priority: '#ffca28', solar_and_mains: '#42a5f5' };

    function setText(id, value) {
        var element = document.getElementById(id);
        if (element) element.textContent = value == null ? '—' : value;
    }
    function sortedRows(rows) {
        return rows.slice().sort(function (a, b) { return Date.parse(a.time) - Date.parse(b.time); });
    }
    function rowsForDays(rows, days) {
        var cutoff = Date.now() - days * 24 * 60 * 60 * 1000;
        return sortedRows(rows).filter(function (row) { return Date.parse(row.time) >= cutoff; });
    }

    /* Historical significant events compare against the last recorded event. */
    function buildSignificantEvents(rows, days) {
        var events = [], prev = null;
        rowsForDays(rows, days).forEach(function (row) {
            var soc = typeof row.battery_soc === 'number' && Number.isFinite(row.battery_soc)
                ? Math.round(row.battery_soc) : null;
            var batTenths = typeof row.battery_voltage === 'number' && Number.isFinite(row.battery_voltage)
                ? Math.round(row.battery_voltage * 10) : null;
            var src = row.charger_priority || '—', mode = row.working_mode || '—';
            var changed = !prev || src !== prev.src || mode !== prev.mode ||
                (soc !== null && prev.soc !== null && Math.abs(soc - prev.soc) >= 5) ||
                (batTenths !== null && prev.batTenths !== null && Math.abs(batTenths - prev.batTenths) >= 3);
            if (!changed) return;
            if (prev && events.length) {
                events[events.length - 1].duration = Date.parse(row.time) - Date.parse(events[events.length - 1].time);
            }
            events.push({ time: row.time, duration: null, src: src, mode: mode,
                soc: soc, batV: batTenths === null ? null : batTenths / 10,
                pvW: row.pv_power_w, outW: row.output_power_w });
            prev = { src: src, mode: mode, soc: soc, batTenths: batTenths };
        });
        return events.reverse();
    }

    /* Fallback for cached/older API responses. SOC and voltage never create events. */
    function buildObservedTransitions(rows, days) {
        var filtered = rowsForDays(rows, days), transitions = [], previousMode = null, previousIndex = null;
        filtered.forEach(function (row, index) {
            var mode = row.working_mode;
            if (mode == null || String(mode).trim() === '') {
                previousMode = null; previousIndex = null; return;
            }
            mode = String(mode);
            if (previousMode !== null && mode !== previousMode && previousIndex !== null) {
                transitions.push({
                    observed_at: row.time, from_mode: previousMode, to_mode: mode,
                    battery_voltage: row.battery_voltage, battery_soc: row.battery_soc,
                    battery_current_chg: row.battery_current_chg, battery_current_dis: row.battery_current_dis,
                    pv_power_w: row.pv_power_w, output_power_w: row.output_power_w,
                    ac_input_voltage: row.ac_input_voltage, output_voltage: row.output_voltage,
                    charger_priority: row.charger_priority, output_priority_raw: row.output_priority_raw,
                    mains_status: row.mains_status, observed_dwell: null, _index: index
                });
            }
            previousMode = mode; previousIndex = index;
        });
        transitions.forEach(function (transition, index) {
            var next = transitions[index + 1];
            if (!next) return;
            var continuous = true;
            for (var rowIndex = transition._index; rowIndex < next._index; rowIndex++) {
                if (Date.parse(filtered[rowIndex + 1].time) - Date.parse(filtered[rowIndex].time) > GAP_MS) {
                    continuous = false; break;
                }
            }
            if (continuous) transition.observed_dwell = (Date.parse(next.observed_at) - Date.parse(transition.observed_at)) / 1000;
        });
        transitions.forEach(function (transition) { delete transition._index; });
        return transitions.reverse();
    }
    function transitionsForDays(days) {
        var cutoff = Date.now() - days * 24 * 60 * 60 * 1000;
        var transitions = apiTransitions.length ? apiTransitions : buildObservedTransitions(allObservations, days);
        return transitions.filter(function (transition) { return Date.parse(transition.observed_at) >= cutoff; });
    }

    function renderSummary(observations, summary) {
        var sorted = sortedRows(observations);
        var ids = ['kpi-source', 'kpi-mode', 'kpi-mode-changes', 'kpi-line-invert', 'kpi-invert-line',
            'kpi-source-changes', 'kpi-shortest-dwell', 'kpi-median-dwell', 'kpi-soc', 'kpi-batv'];
        if (!sorted.length) {
            ids.forEach(function (id) { setText(id, '—'); });
            setText('sourceChangesCount', 'No observations available');
            return;
        }
        var last = sorted[sorted.length - 1]; summary = summary || {};
        setText('kpi-source', SOURCE_LABEL[last.charger_priority] || last.charger_priority || '—');
        setText('kpi-mode', last.working_mode || '—');
        setText('kpi-mode-changes', summary.output_mode_changes == null ? 0 : summary.output_mode_changes);
        setText('kpi-line-invert', summary.line_to_invert == null ? 0 : summary.line_to_invert);
        setText('kpi-invert-line', summary.invert_to_line == null ? 0 : summary.invert_to_line);
        setText('kpi-source-changes', summary.charger_source_changes == null ? 0 : summary.charger_source_changes);
        setText('kpi-shortest-dwell', fmtDuration(summary.shortest_observed_dwell));
        setText('kpi-median-dwell', fmtDuration(summary.median_observed_dwell));
        setText('kpi-soc', last.battery_soc == null ? '—' : Math.round(last.battery_soc) + '%');
        setText('kpi-batv', fmtValue(last.battery_voltage, 'V', 1));
        var sourceElement = document.getElementById('kpi-source');
        if (sourceElement) sourceElement.style.color = SOURCE_COLOR[last.charger_priority] || '';
        var modeElement = document.getElementById('kpi-mode');
        if (modeElement) modeElement.style.color = last.working_mode === 'Invert Mode' ? 'var(--amber)' : '';
        setText('sourceChangesCount', (summary.output_mode_changes == null ? 0 : summary.output_mode_changes) +
            ' output-mode changes · ' + (summary.charger_source_changes == null ? 0 : summary.charger_source_changes) +
            ' charger-source changes in 24h');
    }

    function td_(row, text, cls) {
        var cell = document.createElement('td');
        if (cls) cell.className = cls;
        cell.textContent = text == null ? '—' : text;
        row.appendChild(cell);
        return cell;
    }
    function renderSignificantEvents(events) {
        var body = document.getElementById('significantEventsBody');
        if (!body) return;
        body.replaceChildren();
        setText('significant-events-count', events.length + ' event' + (events.length !== 1 ? 's' : ''));
        if (!events.length) {
            var empty = document.createElement('tr'), cell = document.createElement('td');
            cell.colSpan = 7;
            cell.className = 'dm-mono';
            cell.style.textAlign = 'center';
            cell.style.color = 'var(--text-dim)';
            cell.textContent = 'No significant changes in this period.';
            empty.appendChild(cell);
            body.appendChild(empty);
            return;
        }
        var fragment = document.createDocumentFragment();
        events.forEach(function (event) {
            var row = document.createElement('tr');
            td_(row, fmtDT(event.time), 'dm-mono');
            var sourceCell = document.createElement('td'), badge = document.createElement('span');
            badge.className = 'dm-ev-badge';
            badge.style.color = SOURCE_COLOR[event.src] || 'var(--text)';
            badge.textContent = SOURCE_LABEL[event.src] || event.src;
            sourceCell.appendChild(badge);
            row.appendChild(sourceCell);
            td_(row, event.mode, 'dm-mono');
            var socCell = td_(row, event.soc === null ? '—' : event.soc + '%', 'dm-mono');
            if (event.soc !== null) socCell.style.color = event.soc >= 80 ? 'var(--green)' : event.soc >= 40 ? 'var(--amber)' : 'var(--red)';
            td_(row, event.batV === null ? '—' : event.batV.toFixed(1) + ' V', 'dm-mono');
            td_(row, typeof event.pvW === 'number' && Number.isFinite(event.pvW) ? Math.round(event.pvW) + ' W' : '—', 'dm-mono');
            td_(row, event.duration === null ? '…' : fmtSignificantDuration(event.duration), 'dm-mono');
            fragment.appendChild(row);
        });
        body.appendChild(fragment);
    }
    function renderTransitions(transitions) {
        var body = document.getElementById('eventsBody');
        if (!body) return;
        body.replaceChildren();
        transitions = transitions.slice().sort(function (a, b) {
            return Date.parse(b.observed_at) - Date.parse(a.observed_at);
        });
        setText('events-count', transitions.length + ' transition' + (transitions.length === 1 ? '' : 's'));
        if (!transitions.length) {
            var empty = document.createElement('tr'), emptyCell = document.createElement('td');
            emptyCell.colSpan = 10;
            emptyCell.className = 'dm-mono';
            emptyCell.style.cssText = 'color:var(--text-dim);text-align:center';
            emptyCell.textContent = 'No observed output-mode transitions in this period.';
            empty.appendChild(emptyCell);
            body.appendChild(empty);
            return;
        }
        var fragment = document.createDocumentFragment();
        transitions.forEach(function (transition) {
            var row = document.createElement('tr');
            row.title = transition.battery_soc == null ? '' : 'Reported SOC at transition: ' + Math.round(transition.battery_soc) + '%';
            td_(row, fmtDT(transition.observed_at), 'dm-mono');
            td_(row, transition.from_mode + ' → ' + transition.to_mode, 'dm-mono');
            td_(row, fmtValue(transition.battery_voltage, 'V', 1), 'dm-mono');
            td_(row, fmtValue(transition.pv_power_w, 'W', 0), 'dm-mono');
            td_(row, fmtValue(transition.battery_current_chg, 'A', 1), 'dm-mono');
            td_(row, fmtValue(transition.battery_current_dis, 'A', 1), 'dm-mono');
            td_(row, fmtValue(transition.output_power_w, 'W', 0), 'dm-mono');
            td_(row, SOURCE_LABEL[transition.charger_priority] || transition.charger_priority || '—', 'dm-mono');
            td_(row, transition.output_priority_raw || '—', 'dm-mono');
            td_(row, fmtDuration(transition.observed_dwell), 'dm-mono');
            fragment.appendChild(row);
        });
        body.appendChild(fragment);
    }

    function destroyCharts() {
        Object.keys(charts).forEach(function (key) { if (charts[key]) charts[key].destroy(); });
        charts = {};
    }
    function withGaps(rows, key) {
        var points = [], previous = null;
        rows.forEach(function (row) {
            var x = Date.parse(row.time);
            if (previous !== null && x - previous > GAP_MS) points.push({ x: previous + 1, y: null });
            points.push({ x: x, y: row[key] == null ? null : row[key] });
            previous = x;
        });
        return points;
    }
    var TICK_OPTS = {
        maxTicksLimit: 7,
        callback: function (value) {
            return new Date(value).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
        }
    };
    function xScale(days) {
        return { type: 'linear', min: Date.now() - days * 24 * 60 * 60 * 1000, max: Date.now(), ticks: TICK_OPTS };
    }

    function renderOutputChart(rows, days) {
        var canvas = document.getElementById('outputChart');
        if (!canvas) return;
        var data = rowsForDays(rows, days), states = {};
        data.forEach(function (row) {
            if (row.working_mode != null && String(row.working_mode).trim() !== '') {
                var label = String(row.working_mode);
                if (!Object.prototype.hasOwnProperty.call(states, label)) states[label] = Object.keys(states).length;
            }
        });
        var labels = Object.keys(states);
        var modePoints = data.map(function (row) {
            var mode = row.working_mode == null ? null : String(row.working_mode);
            return { x: Date.parse(row.time), y: Object.prototype.hasOwnProperty.call(states, mode) ? states[mode] : null };
        });
        var transitionPoints = transitionsForDays(days).map(function (transition) {
            return { x: Date.parse(transition.observed_at), y: Object.prototype.hasOwnProperty.call(states, transition.to_mode) ? states[transition.to_mode] : null };
        });
        if (charts.output) charts.output.destroy();
        charts.output = new Chart(canvas, {
            type: 'line',
            data: { datasets: [
                { label: 'Battery voltage (V)', data: withGaps(data, 'battery_voltage'), yAxisID: 'voltage', borderColor: '#26a69a', pointRadius: 0, borderWidth: 2, spanGaps: false, tension: 0 },
                { label: 'Output mode', data: modePoints, yAxisID: 'mode', borderColor: '#ffca28', pointRadius: 0, borderWidth: 2, spanGaps: false, stepped: true, tension: 0 },
                { label: 'Observed transition', data: transitionPoints, yAxisID: 'mode', borderColor: '#ef5350', backgroundColor: '#ef5350', pointRadius: 5, pointHoverRadius: 7, showLine: false, pointStyle: 'triangle' }
            ]},
            options: {
                responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: 'nearest', intersect: false },
                scales: {
                    x: xScale(days),
                    voltage: { type: 'linear', position: 'left', title: { display: true, text: 'Battery voltage (V)' } },
                    mode: { type: 'linear', position: 'right', min: -0.5, max: Math.max(0.5, labels.length - 0.5), grid: { drawOnChartArea: false }, ticks: { stepSize: 1, callback: function (value) { return labels[value] || ''; } }, title: { display: true, text: 'Output mode' } }
                },
                plugins: { tooltip: { callbacks: {
                    title: function (items) { return items.length ? fmtDTFull(items[0].raw.x) : ''; },
                    label: function (context) {
                        if (context.dataset.label === 'Observed transition') return 'Observed transition';
                        if (context.dataset.yAxisID === 'mode') return 'Output mode: ' + (labels[context.raw.y] || '—');
                        return context.dataset.label + ': ' + fmtValue(context.raw.y, 'V', 1);
                    }
                }}}
            }
        });
    }

    function renderPvChart(rows, days) {
        var canvas = document.getElementById('pvChart');
        if (!canvas) return;
        var data = rowsForDays(rows, days);
        if (charts.pv) charts.pv.destroy();
        charts.pv = new Chart(canvas, {
            type: 'line',
            data: { datasets: [{ label: 'PV power (W)', data: withGaps(data, 'pv_power_w'), borderColor: '#42a5f5', backgroundColor: 'rgba(66,165,245,0.10)', pointRadius: 0, borderWidth: 2, spanGaps: false, tension: 0, fill: true }] },
            options: { responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: 'nearest', intersect: false }, scales: { x: xScale(days), y: { beginAtZero: true, title: { display: true, text: 'PV power (W)' } } }, plugins: { tooltip: { callbacks: { title: function (items) { return items.length ? fmtDTFull(items[0].raw.x) : ''; } } } } }
        });
    }

    function renderSourceChart(rows) {
        var canvas = document.getElementById('sourceChart');
        if (!canvas) return;
        var states = { solar_only: 0, solar_priority: 1, solar_and_mains: 2 };
        var labels = ['Solar only', 'Solar priority', 'Solar + mains'];
        var data = sortedRows(rows), points = [], previous = null;
        data.forEach(function (row) {
            var x = Date.parse(row.time);
            if (previous !== null && x - previous > GAP_MS) points.push({ x: previous + 1, y: null });
            points.push({ x: x, y: Object.prototype.hasOwnProperty.call(states, row.charger_priority) ? states[row.charger_priority] : null });
            previous = x;
        });
        if (charts.source) charts.source.destroy();
        charts.source = new Chart(canvas, {
            type: 'line',
            data: { datasets: [{ label: 'Charger source', data: points, borderColor: '#ab47bc', stepped: true, pointRadius: 0, spanGaps: false }] },
            options: { responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: 'nearest', intersect: false }, scales: { x: xScale(7), y: { min: -0.5, max: 2.5, ticks: { stepSize: 1, callback: function (value) { return labels[value] || ''; } } } }, plugins: { tooltip: { callbacks: { title: function (items) { return items.length ? fmtDTFull(items[0].raw.x) : ''; }, label: function (context) { return 'Charger source: ' + (labels[context.raw.y] || '—'); } } } } }
        });
    }

    function renderAll(days) {
        renderTransitions(transitionsForDays(days));
        renderSignificantEvents(buildSignificantEvents(allObservations, days));
        renderOutputChart(allObservations, days);
        renderPvChart(allObservations, days);
        setText('solarRangeLabel', days === 1 ? '24h' : days + ' days');
        setText('gridRangeLabel', days === 1 ? '24h' : days + ' days');
    }
    function bindRangeButtons() {
        try {
            var saved = Number(sessionStorage.getItem('controls.days'));
            if ([1, 2, 7].indexOf(saved) !== -1) currentDays = saved;
        } catch (_) { /* Storage may be disabled. */ }
        document.querySelectorAll('[data-days]').forEach(function (button) {
            button.classList.toggle('active', Number(button.dataset.days) === currentDays);
            button.addEventListener('click', function () {
                document.querySelectorAll('[data-days]').forEach(function (other) { other.classList.remove('active'); });
                button.classList.add('active');
                currentDays = parseInt(button.dataset.days, 10);
                try { sessionStorage.setItem('controls.days', String(currentDays)); } catch (_) { /* Optional. */ }
                renderAll(currentDays);
            });
        });
    }
    async function load() {
        if (loading) return;
        loading = true;
        var version = ++requestVersion;
        var controller = new AbortController();
        var timeout = setTimeout(function () { controller.abort(); }, 20000);
        if (!hasData) setText('loadStatus', 'Loading…');
        try {
            var response = await fetch('/api/charge/history?days=7', { cache: 'no-store', signal: controller.signal });
            if (!response.ok) throw new Error('Request failed');
            var payload = await response.json();
            if (!payload || !Array.isArray(payload.observations) || !Array.isArray(payload.transitions) ||
                !payload.summary_24h || typeof payload.summary_24h !== 'object' || Array.isArray(payload.summary_24h) ||
                payload.observations.some(function (row) { return !row || typeof row !== 'object' || !Number.isFinite(Date.parse(row.time)); }) ||
                payload.transitions.some(function (row) { return !row || typeof row !== 'object' || !Number.isFinite(Date.parse(row.observed_at)); })) {
                throw new Error('Invalid response');
            }
            if (version !== requestVersion) return;
            allObservations = payload.observations;
            apiTransitions = payload.transitions;
            summary24h = payload.summary_24h;
            renderSummary(allObservations, summary24h);
            renderSourceChart(allObservations);
            renderAll(currentDays);
            hasData = true;
            setText('loadStatus', 'Updated ' + new Date().toLocaleTimeString('en-GB') + ' · refreshes every minute');
        } catch (error) {
            if (version === requestVersion) {
                setText('loadStatus', hasData ? 'Refresh failed; showing the last successful results. Retrying automatically.' : 'Unable to load diagnostics. Retrying automatically.');
                if (!hasData) setText('sourceChangesCount', 'Diagnostics unavailable');
            }
        } finally {
            clearTimeout(timeout);
            loading = false;
        }
    }
    document.addEventListener('DOMContentLoaded', function () {
        bindRangeButtons();
        load();
        setInterval(function () { if (!document.hidden) load(); }, REFRESH_MS);
        document.addEventListener('visibilitychange', function () { if (!document.hidden) load(); });
    });
}());
