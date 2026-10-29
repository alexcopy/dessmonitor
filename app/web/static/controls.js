/* controls.js — redesigned charger source history */
(function () {
    'use strict';

    var requestVersion = 0;
    var charts = {};
    var allObservations = [];
    var currentDays = 2;

    /* ─── formatting helpers ─────────────────────────────────── */
    function fmtDT(value) {
        if (value == null) return '—';
        return new Date(value).toLocaleString('en-GB', {
            day: 'numeric', month: 'short',
            hour: '2-digit', minute: '2-digit'
        });
    }

    function fmtDTFull(value) {
        if (value == null) return '—';
        return new Date(value).toLocaleString('en-GB', {
            day: 'numeric', month: 'short',
            hour: '2-digit', minute: '2-digit', second: '2-digit'
        });
    }

    function fmtNum(value, unit, decimals) {
        if (value == null) return '—';
        return value.toFixed(decimals != null ? decimals : 1) + ' ' + unit;
    }

    function fmtDuration(ms) {
        if (ms <= 0) return '<1 min';
        var m = Math.round(ms / 60000);
        if (m < 60) return m + ' min';
        var h = Math.floor(m / 60), rm = m % 60;
        return h + ' h' + (rm ? ' ' + rm + ' min' : '');
    }

    var SOURCE_LABEL = {
        solar_only: 'Solar only',
        solar_priority: 'Solar priority',
        solar_and_mains: 'Solar + mains'
    };

    var SOURCE_COLOR = {
        solar_only: '#26a69a',
        solar_priority: '#ffca28',
        solar_and_mains: '#42a5f5'
    };

    /* ─── smart events: collapse identical runs ──────────────── */
    function buildEvents(rows, days) {
        var cutoff = Date.now() - days * 24 * 60 * 60 * 1000;
        var filtered = rows.filter(function (r) {
            return Date.parse(r.time) >= cutoff;
        });
        if (!filtered.length) return [];

        var events = [];
        var prev = null;

        filtered.forEach(function (row) {
            var soc    = row.battery_soc    != null ? Math.round(row.battery_soc)    : null;
            var batV   = row.battery_voltage != null ? Math.round(row.battery_voltage * 10) / 10 : null;
            var src    = row.charger_priority  || '—';
            var mode   = row.working_mode      || '—';

            var changed = false;
            if (!prev) {
                changed = true;
            } else {
                if (src  !== prev.src)  changed = true;
                if (mode !== prev.mode) changed = true;
                if (soc  !== null && prev.soc  !== null && Math.abs(soc - prev.soc)   >= 5) changed = true;
                if (batV !== null && prev.batV !== null && Math.abs(batV - prev.batV) >= 0.3) changed = true;
            }

            if (changed) {
                if (prev && events.length) {
                    events[events.length - 1].end = row.time;
                    events[events.length - 1].duration =
                        Date.parse(row.time) - Date.parse(events[events.length - 1].time);
                }
                events.push({
                    time: row.time,
                    end: null,
                    duration: null,
                    src: src, mode: mode,
                    soc: soc, batV: batV,
                    pvW:  row.pv_power_w,
                    outW: row.output_power_w,
                    acV:  row.ac_input_voltage,
                    chgA: row.battery_current_chg,
                    disA: row.battery_current_dis,
                    raw:  row
                });
                prev = { src, mode, soc, batV };
            }
        });

        return events.reverse(); // newest first
    }

    /* ─── summary KPIs ────────────────────────────────────────── */
    function renderSummary(observations, verifiedSince) {
        if (!observations.length) return;

        // most recent row
        var last = observations[observations.length - 1];
        var now  = Date.now();

        // count source changes
        var changes = 0;
        var prevSrc = null;
        observations.forEach(function (r) {
            if (prevSrc !== null && r.charger_priority !== prevSrc) changes++;
            prevSrc = r.charger_priority;
        });

        // mains time in last 24h
        var cutoff24 = now - 24 * 3600 * 1000;
        var mainsMs = 0;
        var sortedByTime = observations.slice().sort(function(a,b){ return Date.parse(a.time)-Date.parse(b.time); });
        for (var i = 1; i < sortedByTime.length; i++) {
            var t0 = Date.parse(sortedByTime[i-1].time);
            var t1 = Date.parse(sortedByTime[i].time);
            if (t1 < cutoff24) continue;
            var start = Math.max(t0, cutoff24);
            var gap = t1 - t0;
            if (gap < 3600000 && sortedByTime[i-1].charger_priority === 'solar_and_mains') {
                mainsMs += t1 - start;
            }
        }

        setText('kpi-source', SOURCE_LABEL[last.charger_priority] || last.charger_priority || '—');
        setText('kpi-soc',    last.battery_soc != null ? Math.round(last.battery_soc) + '%' : '—');
        setText('kpi-batv',   last.battery_voltage != null ? last.battery_voltage.toFixed(1) + ' V' : '—');
        setText('kpi-changes', changes);
        setText('kpi-mains24', fmtDuration(mainsMs));
        setText('kpi-since',   verifiedSince ? fmtDTFull(verifiedSince) : '—');

        var srcEl = document.getElementById('kpi-source');
        if (srcEl) {
            srcEl.style.color = SOURCE_COLOR[last.charger_priority] || '';
        }

        // SOC color
        var socEl = document.getElementById('kpi-soc');
        if (socEl && last.battery_soc != null) {
            var s = last.battery_soc;
            socEl.style.color = s >= 80 ? 'var(--green)' : s >= 40 ? 'var(--amber)' : 'var(--red)';
        }
    }

    function setText(id, value) {
        var el = document.getElementById(id);
        if (el) el.textContent = value;
    }

    /* ─── events table ────────────────────────────────────────── */
    function renderEvents(events) {
        var body = document.getElementById('eventsBody');
        if (!body) return;
        body.replaceChildren();

        if (!events.length) {
            var tr = document.createElement('tr');
            var td = document.createElement('td');
            td.colSpan = 7;
            td.className = 'dm-mono';
            td.style.textAlign = 'center';
            td.style.color = 'var(--text-dim)';
            td.textContent = 'No significant changes in this period.';
            tr.appendChild(td);
            body.appendChild(tr);
            return;
        }

        var frag = document.createDocumentFragment();
        events.forEach(function (ev) {
            var tr = document.createElement('tr');

            // time
            td_(tr, fmtDT(ev.time), 'dm-mono');

            // source badge
            var srcTd = document.createElement('td');
            var badge = document.createElement('span');
            badge.className = 'dm-ev-badge';
            badge.style.color = SOURCE_COLOR[ev.src] || 'var(--text)';
            badge.textContent = SOURCE_LABEL[ev.src] || ev.src;
            srcTd.appendChild(badge);
            tr.appendChild(srcTd);

            // mode
            td_(tr, ev.mode, 'dm-mono');

            // SOC
            var socTd = document.createElement('td');
            socTd.className = 'dm-mono';
            if (ev.soc != null) {
                socTd.textContent = ev.soc + '%';
                socTd.style.color = ev.soc >= 80 ? 'var(--green)' : ev.soc >= 40 ? 'var(--amber)' : 'var(--red)';
            } else {
                socTd.textContent = '—';
            }
            tr.appendChild(socTd);

            // Battery V
            td_(tr, ev.batV != null ? ev.batV.toFixed(1) + ' V' : '—', 'dm-mono');

            // PV W
            td_(tr, ev.pvW != null ? Math.round(ev.pvW) + ' W' : '—', 'dm-mono');

            // Duration
            td_(tr, ev.duration != null ? fmtDuration(ev.duration) : '…', 'dm-mono');

            frag.appendChild(tr);
        });
        body.appendChild(frag);
    }

    function td_(row, text, cls) {
        var td = document.createElement('td');
        if (cls) td.className = cls;
        td.textContent = text == null ? '—' : text;
        row.appendChild(td);
        return td;
    }

    /* ─── charts ──────────────────────────────────────────────── */
    function destroyCharts() {
        Object.values(charts).forEach(function (c) { if (c) c.destroy(); });
        charts = {};
    }

    function buildTimePoints(rows, days) {
        var cutoff = Date.now() - days * 24 * 60 * 60 * 1000;
        var sorted = rows.filter(function (r) {
            return Date.parse(r.time) >= cutoff;
        }).sort(function (a, b) {
            return Date.parse(a.time) - Date.parse(b.time);
        });
        return sorted;
    }

    function withGaps(rows, valueKey) {
        var points = [];
        var prev = null;
        rows.forEach(function (r) {
            var x = Date.parse(r.time);
            if (prev !== null && x - prev > 3600000) {
                points.push({ x: prev + 1, y: null });
            }
            points.push({ x: x, y: r[valueKey] });
            prev = x;
        });
        return points;
    }

    var TICK_OPTS = {
        maxTicksLimit: 7,
        callback: function (value) {
            return new Date(value).toLocaleString('en-GB', {
                day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'
            });
        }
    };

    function xScale(days) {
        return {
            type: 'linear',
            min: Date.now() - days * 24 * 60 * 60 * 1000,
            max: Date.now(),
            ticks: TICK_OPTS
        };
    }

    /* Chart 1 — Solar PV power + battery SOC over selected period */
    function renderSolarChart(rows, days) {
        var canvas = document.getElementById('solarChart');
        if (!canvas) return;
        var data = buildTimePoints(rows, days);

        if (charts.solar) charts.solar.destroy();
        charts.solar = new Chart(canvas, {
            type: 'line',
            data: { datasets: [
                {
                    label: 'PV power (W)',
                    data: withGaps(data, 'pv_power_w'),
                    yAxisID: 'pv',
                    borderColor: '#26a69a',
                    backgroundColor: 'rgba(38,166,154,0.08)',
                    fill: true,
                    pointRadius: 0,
                    spanGaps: false,
                    tension: 0.3
                },
                {
                    label: 'SOC (%)',
                    data: withGaps(data, 'battery_soc'),
                    yAxisID: 'soc',
                    borderColor: '#ffca28',
                    pointRadius: 0,
                    spanGaps: false,
                    tension: 0.2
                }
            ]},
            options: {
                responsive: true, maintainAspectRatio: false, animation: false,
                interaction: { mode: 'nearest', intersect: false },
                scales: {
                    x: xScale(days),
                    pv: {
                        type: 'linear', position: 'left', beginAtZero: true,
                        title: { display: true, text: 'PV (W)' }
                    },
                    soc: {
                        type: 'linear', position: 'right', min: 0, max: 100,
                        grid: { drawOnChartArea: false },
                        title: { display: true, text: 'SOC (%)' }
                    }
                },
                plugins: { tooltip: { callbacks: {
                    title: function (items) {
                        return items.length ? fmtDTFull(items[0].raw.x) : '';
                    }
                }}}
            }
        });
    }

    /* Chart 2 — Grid charging: charge current + AC input voltage (only solar_and_mains rows) */
    function renderGridChart(rows, days) {
        var canvas = document.getElementById('gridChart');
        if (!canvas) return;
        var data = buildTimePoints(rows, days)
            .filter(function (r) { return r.charger_priority === 'solar_and_mains'; });

        if (!data.length) {
            var wrap = canvas.parentElement;
            wrap.innerHTML = '<div class="dm-chart-empty">No grid charging in this period</div>';
            return;
        }

        var pts_chg = [], pts_acv = [];
        data.forEach(function (r) {
            var x = Date.parse(r.time);
            pts_chg.push({ x: x, y: r.battery_current_chg });
            pts_acv.push({ x: x, y: r.ac_input_voltage });
        });

        if (charts.grid) charts.grid.destroy();
        charts.grid = new Chart(canvas, {
            type: 'line',
            data: { datasets: [
                {
                    label: 'Charge current (A DC)',
                    data: pts_chg,
                    yAxisID: 'chg',
                    borderColor: '#42a5f5',
                    backgroundColor: 'rgba(66,165,245,0.08)',
                    fill: true,
                    pointRadius: 2,
                    spanGaps: false,
                    tension: 0.2
                },
                {
                    label: 'AC input (V)',
                    data: pts_acv,
                    yAxisID: 'acv',
                    borderColor: '#ab47bc',
                    pointRadius: 0,
                    spanGaps: false,
                    tension: 0.2
                }
            ]},
            options: {
                responsive: true, maintainAspectRatio: false, animation: false,
                interaction: { mode: 'nearest', intersect: false },
                scales: {
                    x: xScale(days),
                    chg: {
                        type: 'linear', position: 'left', beginAtZero: true,
                        title: { display: true, text: 'Charge A (DC)' }
                    },
                    acv: {
                        type: 'linear', position: 'right',
                        grid: { drawOnChartArea: false },
                        title: { display: true, text: 'AC input (V)' }
                    }
                },
                plugins: { tooltip: { callbacks: {
                    title: function (items) {
                        return items.length ? fmtDTFull(items[0].raw.x) : '';
                    }
                }}}
            }
        });
    }

    /* Chart 3 — Source timeline (7 days only, stepped) */
    function renderSourceChart(rows) {
        var canvas = document.getElementById('sourceChart');
        if (!canvas) return;

        var STATES = { solar_only: 0, solar_priority: 1, solar_and_mains: 2 };
        var SRC_LABELS = ['Solar only', 'Solar priority', 'Solar + mains'];
        var sorted = rows.slice().sort(function (a, b) {
            return Date.parse(a.time) - Date.parse(b.time);
        });

        var pts = [], prev = null;
        sorted.forEach(function (r) {
            var x = Date.parse(r.time);
            if (prev !== null && x - prev > 3600000) {
                pts.push({ x: prev + 1, y: null });
            }
            pts.push({
                x: x,
                y: Object.prototype.hasOwnProperty.call(STATES, r.charger_priority)
                    ? STATES[r.charger_priority] : null
            });
            prev = x;
        });

        if (charts.source) charts.source.destroy();
        charts.source = new Chart(canvas, {
            type: 'line',
            data: { datasets: [{
                label: 'Charger source',
                data: pts,
                borderColor: '#42a5f5',
                stepped: true,
                pointRadius: 0,
                spanGaps: false
            }]},
            options: {
                responsive: true, maintainAspectRatio: false, animation: false,
                interaction: { mode: 'nearest', intersect: false },
                scales: {
                    x: xScale(7),
                    y: {
                        type: 'linear', min: 0, max: 2,
                        ticks: {
                            stepSize: 1,
                            callback: function (v) { return SRC_LABELS[v] || ''; }
                        }
                    }
                },
                plugins: { tooltip: { callbacks: {
                    title: function (items) {
                        return items.length ? fmtDTFull(items[0].raw.x) : '';
                    },
                    label: function (ctx) {
                        return 'Source: ' + (SRC_LABELS[ctx.raw.y] || '—');
                    }
                }}}
            }
        });
    }

    /* ─── date range tabs ────────────────────────────────────── */
    function bindRangeButtons() {
        document.querySelectorAll('[data-days]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                document.querySelectorAll('[data-days]').forEach(function (b) {
                    b.classList.remove('active');
                });
                btn.classList.add('active');
                currentDays = parseInt(btn.dataset.days, 10);
                renderAll(currentDays);
            });
        });
    }

    function renderAll(days) {
        var events = buildEvents(allObservations, days);
        renderEvents(events);

        // Update events count badge
        setText('events-count', events.length + ' event' + (events.length !== 1 ? 's' : ''));

        renderSolarChart(allObservations, days);
        renderGridChart(allObservations, days);
    }

    /* ─── load data ───────────────────────────────────────────── */
    async function load() {
        var version = ++requestVersion;
        setText('loadStatus', 'Loading…');

        try {
            var response = await fetch('/api/charge/history?days=7', { cache: 'no-store' });
            if (!response.ok) throw new Error('Request failed');
            var data = await response.json();
            if (version !== requestVersion) return;

            allObservations = (data.observations || []).slice().sort(function (a, b) {
                return Date.parse(a.time) - Date.parse(b.time);
            });

            // hide load status
            var statusEl = document.getElementById('loadStatus');
            if (statusEl) statusEl.style.display = 'none';

            renderSummary(allObservations, data.verified_since);
            renderSourceChart(allObservations);
            renderAll(currentDays);

            // count changes for header
            var changes = 0;
            var prevSrc = null;
            allObservations.forEach(function (r) {
                if (prevSrc !== null && r.charger_priority !== prevSrc) changes++;
                prevSrc = r.charger_priority;
            });
            setText('sourceChangesCount', changes + ' source change' + (changes !== 1 ? 's' : '') + ' in 7 days');

        } catch (err) {
            if (version !== requestVersion) return;
            setText('loadStatus', 'Failed to load. Reload to retry.');
        }
    }

    document.addEventListener('DOMContentLoaded', function () {
        bindRangeButtons();
        load();
    });
}());
