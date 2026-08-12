/* analytics.js — dessmonitor
 * External script for analytics page. CSP 'default-src self' compliant.
 * No inline code, no eval, no external resources.
 */
(function () {
    'use strict';

    /* Date range button toggle */
    function setupDateRange() {
        var btns = document.querySelectorAll('.dm-range-btn');
        btns.forEach(function (btn) {
            btn.addEventListener('click', function () {
                btns.forEach(function (b) { b.classList.remove('active'); });
                btn.classList.add('active');
            });
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', setupDateRange);
    } else {
        setupDateRange();
    }
}());

/* ── Daily Energy Table ─────────────────────────────────── */
(function() {
    function wh2kwh(wh) {
        if (wh == null) return "—";
        var v = (wh / 1000).toFixed(2);
        return wh > 0 ? v : '<span class="red-text">' + v + '</span>';
    }
    function selfPct(solar, load) {
        if (!load || load <= 0) return "—";
        return Math.min(100, Math.round((solar / load) * 100)) + "%";
    }

    function loadDailyTable() {
        var tbody = document.getElementById("dailyEnergyBody");
        var tfoot = document.getElementById("dailyEnergyFoot");
        if (!tbody) return;

        fetch("/api/energy/daily?days=30")
            .then(function(r) { return r.ok ? r.json() : null; })
            .then(function(data) {
                if (!data || !data.week) {
                    tbody.innerHTML = '<tr><td colspan="7">No data</td></tr>';
                    return;
                }
                var rows = data.week.slice().reverse(); // newest first
                var totPv = 0, totBatt = 0, totGrid = 0, totLoad = 0;

                tbody.innerHTML = rows.map(function(r) {
                    var solar = (r.pv_wh || 0) + (r.battery_wh || 0);
                    totPv   += r.pv_wh || 0;
                    totBatt += r.battery_wh || 0;
                    totGrid += r.grid_wh || 0;
                    totLoad += r.load_wh || 0;
                    var self = selfPct(solar, r.load_wh);
                    var selfClass = self === "100%" ? "green-text"
                                  : parseInt(self) >= 70 ? "teal-text"
                                  : parseInt(self) >= 30 ? "amber-text" : "red-text";
                    return '<tr>' +
                        '<td class="dm-mono">' + r.day + '</td>' +
                        '<td class="dm-mono teal-text">' + wh2kwh(r.pv_wh) + '</td>' +
                        '<td class="dm-mono amber-text">' + wh2kwh(r.battery_wh) + '</td>' +
                        '<td class="dm-mono teal-text">' + wh2kwh(solar) + '</td>' +
                        '<td class="dm-mono blue-text">' + wh2kwh(r.grid_wh) + '</td>' +
                        '<td class="dm-mono green-text">' + wh2kwh(r.load_wh) + '</td>' +
                        '<td class="dm-mono ' + selfClass + '">' + self + '</td>' +
                        '</tr>';
                }).join("");

                /* totals row */
                var totSolar = totPv + totBatt;
                tfoot.innerHTML = '<tr class="dm-table-total">' +
                    '<td class="dm-mono">TOTAL</td>' +
                    '<td class="dm-mono teal-text">' + wh2kwh(totPv) + '</td>' +
                    '<td class="dm-mono amber-text">' + wh2kwh(totBatt) + '</td>' +
                    '<td class="dm-mono teal-text">' + wh2kwh(totSolar) + '</td>' +
                    '<td class="dm-mono blue-text">' + wh2kwh(totGrid) + '</td>' +
                    '<td class="dm-mono green-text">' + wh2kwh(totLoad) + '</td>' +
                    '<td class="dm-mono purple-text">' + selfPct(totSolar, totLoad) + '</td>' +
                    '</tr>';
            })
            .catch(function() {
                tbody.innerHTML = '<tr><td colspan="7">Failed to load</td></tr>';
            });
    }

    document.addEventListener("DOMContentLoaded", loadDailyTable);
})();

/* ── Inverter Metrics Table (TimescaleDB) ─────────────────── */
(function() {
    function fmt(v, dec) {
        return v == null ? "—" : parseFloat(v).toFixed(dec || 0);
    }
    function modeClass(m) {
        if (!m) return "";
        if (m.indexOf("Invert") >= 0) return "teal-text";
        if (m.indexOf("Line") >= 0) return "blue-text";
        return "amber-text";
    }

    function loadInverterMetrics() {
        var tbody = document.getElementById("inverterMetricsBody");
        if (!tbody) return;
        fetch("/api/inverter/metrics")
            .then(function(r) { return r.ok ? r.json() : null; })
            .then(function(data) {
                if (!data || !data.rows || !data.rows.length) {
                    tbody.innerHTML = '<tr><td colspan="9">No data yet</td></tr>';
                    return;
                }
                tbody.innerHTML = data.rows.map(function(r) {
                    var t = new Date(r.time);
                    var ts = t.toISOString().replace('T',' ').substring(0,19);
                    return '<tr>' +
                        '<td class="dm-mono">' + ts + '</td>' +
                        '<td class="dm-mono ' + modeClass(r.mode) + '">' + (r.mode || '—') + '</td>' +
                        '<td class="dm-mono teal-text">' + fmt(r.pv_w) + '</td>' +
                        '<td class="dm-mono amber-text">' + fmt(r.batt_v, 1) + '</td>' +
                        '<td class="dm-mono amber-text">' + fmt(r.batt_soc) + '</td>' +
                        '<td class="dm-mono">' + fmt(r.batt_dis, 1) + '</td>' +
                        '<td class="dm-mono green-text">' + fmt(r.output_w) + '</td>' +
                        '<td class="dm-mono green-text">' + fmt(r.load_w) + '</td>' +
                        '<td class="dm-mono">' + fmt(r.load_pct) + '</td>' +
                        '</tr>';
                }).join('');
            })
            .catch(function() {
                tbody.innerHTML = '<tr><td colspan="9">Failed to load</td></tr>';
            });
    }

    document.addEventListener("DOMContentLoaded", function() {
        loadInverterMetrics();
        setInterval(loadInverterMetrics, 120000); // refresh every 2min
    });
})();

/* ── Meter Daily Energy ─────────────────────────────────── */
(function() {
    var currentDays = 30;
    var chartInstance = null;

    function loadMeterData(days) {
        currentDays = days;
        var tbody = document.getElementById("meterDailyBody");
        var tfoot = document.getElementById("meterDailyFoot");
        var placeholder = document.getElementById("meterChartPlaceholder");
        if (tbody) tbody.innerHTML = '<tr><td colspan="4" class="dm-mono">Loading...</td></tr>';

        fetch("/api/energy/meter/daily?days=" + days)
            .then(function(r) { return r.ok ? r.json() : null; })
            .then(function(data) {
                if (!data || !data.days || !data.days.length) {
                    if (tbody) tbody.innerHTML = '<tr><td colspan="4" class="dm-mono">No meter data yet</td></tr>';
                    return;
                }
                if (placeholder) placeholder.style.display = "none";

                var rows = data.days;
                var totalKwh = 0;

                if (tbody) {
                    tbody.innerHTML = rows.map(function(r) {
                        totalKwh += r.consumed_kwh || 0;
                        return '<tr>' +
                            '<td class="dm-mono">' + r.day + '</td>' +
                            '<td class="dm-mono teal-text">' + (r.consumed_kwh || 0).toFixed(3) + '</td>' +
                            '<td class="dm-mono amber-text">' + (r.end_kwh || 0).toFixed(1) + '</td>' +
                            '<td class="dm-mono" style="color:var(--text-dim)">' + (r.samples || 0) + '</td>' +
                            '</tr>';
                    }).join('');
                }

                if (tfoot) {
                    tfoot.innerHTML = '<tr class="dm-table-total">' +
                        '<td class="dm-mono">TOTAL ' + days + 'd</td>' +
                        '<td class="dm-mono teal-text">' + totalKwh.toFixed(2) + ' kWh</td>' +
                        '<td></td><td></td></tr>';
                }

                // Chart
                renderMeterChart(rows);
            })
            .catch(function() {
                if (tbody) tbody.innerHTML = '<tr><td colspan="4">Failed to load</td></tr>';
            });
    }

    function renderMeterChart(rows) {
        var canvas = document.getElementById("meterChart");
        if (!canvas) return;
        if (typeof Chart === "undefined") return;

        var labels = rows.map(function(r) { return r.day; }).reverse();
        var values = rows.map(function(r) { return r.consumed_kwh || 0; }).reverse();

        if (chartInstance) { chartInstance.destroy(); }
        chartInstance = new Chart(canvas.getContext("2d"), {
            type: "bar",
            data: {
                labels: labels,
                datasets: [{
                    label: "kWh consumed",
                    data: values,
                    backgroundColor: "rgba(0,209,178,0.4)",
                    borderColor: "rgba(0,209,178,0.8)",
                    borderWidth: 1,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    y: { beginAtZero: true, ticks: { color: "#8b949e" }, grid: { color: "#21262d" } },
                    x: { ticks: { color: "#8b949e", maxRotation: 45 }, grid: { display: false } }
                }
            }
        });
    }

    document.addEventListener("DOMContentLoaded", function() {
        // Period buttons
        document.querySelectorAll(".dm-meter-period").forEach(function(btn) {
            btn.addEventListener("click", function() {
                document.querySelectorAll(".dm-meter-period").forEach(function(b) {
                    b.classList.remove("active");
                });
                btn.classList.add("active");
                loadMeterData(parseInt(btn.dataset.days));
            });
        });
        loadMeterData(30);
    });
})();


/* ── Load Thresholds Table ───────────────────────────────── */
function loadThresholds() {
    fetch("/api/thresholds")
        .then(function(r) { return r.ok ? r.json() : null; })
        .then(function(data) {
            if (!data) return;
            var tbody = document.getElementById("thresholdsBody");
            var badge = document.getElementById("thresholdsSolarBadge");
            var voltEl = document.getElementById("thresholdsVoltage");
            if (!tbody) return;

            if (voltEl && data.battery_voltage != null) {
                voltEl.textContent = "Battery: " + parseFloat(data.battery_voltage).toFixed(1) + "V";
            }
            if (badge) {
                badge.style.display = data.solar_period ? "" : "none";
            }

            if (!data.devices || !data.devices.length) {
                tbody.innerHTML = '<tr><td colspan="6" class="dm-mono">No devices</td></tr>';
                return;
            }

            var bv = data.battery_voltage ? parseFloat(data.battery_voltage) : null;
            tbody.innerHTML = data.devices.map(function(d) {
                var isOn = d.is_on === true;
                var hasSolar = d.coefficient > 0;
                var activeMin = data.solar_period && hasSolar ? d.solar_min_volt : d.min_volt;

                // Status indicator
                var status = "";
                if (bv !== null) {
                    if (isOn) {
                        status = bv < activeMin
                            ? '<span class="amber-text">will OFF soon</span>'
                            : '<span class="teal-text">ON ✓</span>';
                    } else {
                        var effectiveMax = (data.solar_period && hasSolar && d.solar_max_volt) ? d.solar_max_volt : d.max_volt;
                  status = bv >= effectiveMax
                            ? '<span class="green-text">ready to ON</span>'
                            : '<span class="dm-text-dim">waiting ' + effectiveMax.toFixed(1) + 'V' + (data.solar_period && hasSolar ? ' ☀' : '') + '</span>';
                    }
                }

                return '<tr>' +
                    '<td class="dm-mono">' + (d.desc || d.name) + '</td>' +
                    '<td class="dm-mono amber-text">' + d.max_volt.toFixed(1) + 'V</td>' +
                    '<td class="dm-mono red-text">' + d.min_volt.toFixed(1) + 'V</td>' +
                    '<td class="dm-mono ' + (hasSolar ? 'teal-text' : 'dm-text-dim') + '">' +
                        (hasSolar ? d.solar_max_volt.toFixed(1) + 'V' : '—') + '</td>' +
                    '<td class="dm-mono ' + (hasSolar ? 'teal-text' : 'dm-text-dim') + '">' +
                        (hasSolar ? d.solar_min_volt.toFixed(1) + 'V' : '—') + '</td>' +
                    '<td class="dm-mono" style="color:var(--text-dim)">' + (hasSolar ? d.coefficient.toFixed(1) + 'V' : '—') + '</td>' +
                    '<td>' + status + '</td>' +
                    '</tr>';
            }).join('');
        })
        .catch(function() {});
}

document.addEventListener("DOMContentLoaded", function() {
    loadThresholds();
    setInterval(loadThresholds, 30000);
});

/* ── Analytics Charts (Chart.js) ─────────────────────────────── */
(function() {
    'use strict';

    if (typeof Chart === 'undefined') return;

    /* Chart defaults (set once) */
    Chart.defaults.color = '#8b949e';
    Chart.defaults.borderColor = '#21262d';
    Chart.defaults.font.family = 'monospace';

    /* Shared options template */
    function baseOptions() {
        return {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                x: { ticks: { color: '#8b949e', maxTicksLimit: 8 }, grid: { color: '#21262d' } },
                y: { ticks: { color: '#8b949e' }, grid: { color: '#21262d' }, beginAtZero: false }
            }
        };
    }

    /* Store chart instances so we can destroy before re-render */
    window._charts = window._charts || {};

    function destroyChart(id) {
        if (window._charts[id]) {
            window._charts[id].destroy();
            delete window._charts[id];
        }
    }

    function renderChart(id, config) {
        var canvas = document.getElementById(id);
        if (!canvas) return;
        destroyChart(id);
        window._charts[id] = new Chart(canvas.getContext('2d'), config);
    }

    /* ── PV Power line chart ─────────────────────────────────── */
    function loadPvPowerChart(hours) {
        fetch('/api/charts/inverter?hours=' + hours)
            .then(function(r) { return r.ok ? r.json() : null; })
            .then(function(data) {
                if (!data || !data.data || !data.data.length) return;
                var labels = data.data.map(function(p) { return p.time; });
                var values = data.data.map(function(p) { return p.pv_power_w; });
                renderChart('pvPowerChart', {
                    type: 'line',
                    data: {
                        labels: labels,
                        datasets: [{
                            label: 'PV Power (W)',
                            data: values,
                            borderColor: '#00d1b2',
                            backgroundColor: 'rgba(0,209,178,0.15)',
                            borderWidth: 2,
                            pointRadius: 0,
                            fill: true,
                            tension: 0.2
                        }]
                    },
                    options: baseOptions()
                });
            })
            .catch(function() {});
    }

    /* ── Battery voltage line chart ──────────────────────────── */
    function loadBattVoltChart(hours) {
        fetch('/api/charts/inverter?hours=' + hours)
            .then(function(r) { return r.ok ? r.json() : null; })
            .then(function(data) {
                if (!data || !data.data || !data.data.length) return;
                var labels = data.data.map(function(p) { return p.time; });
                var values = data.data.map(function(p) { return p.battery_voltage; });
                renderChart('battVoltChart', {
                    type: 'line',
                    data: {
                        labels: labels,
                        datasets: [{
                            label: 'Battery Voltage (V)',
                            data: values,
                            borderColor: '#ffdd57',
                            backgroundColor: 'rgba(255,221,87,0.15)',
                            borderWidth: 2,
                            pointRadius: 0,
                            fill: true,
                            tension: 0.2
                        }]
                    },
                    options: baseOptions()
                });
            })
            .catch(function() {});
    }

    /* ── Device energy bar chart (today) ─────────────────────── */
    function loadDeviceEnergyChart() {
        fetch('/api/energy/devices/today')
            .then(function(r) { return r.ok ? r.json() : null; })
            .then(function(data) {
                if (!data || !data.devices || !data.devices.length) return;
                var labels = data.devices.map(function(d) { return d.device_name; });
                var values = data.devices.map(function(d) { return d.real_wh || 0; });
                var colors = ['#00d1b2', '#48c78e', '#ffdd57', '#ff6b6b', '#a78bfa', '#f97316'];
                renderChart('deviceEnergyChart', {
                    type: 'bar',
                    data: {
                        labels: labels,
                        datasets: [{
                            label: 'Energy (Wh)',
                            data: values,
                            backgroundColor: colors,
                            borderWidth: 0
                        }]
                    },
                    options: baseOptions()
                });
            })
            .catch(function() {});
    }

    /* ── Hourly load bar chart ───────────────────────────────── */
    function loadHourlyLoadChart(hours) {
        fetch('/api/charts/hourly-load?hours=' + hours)
            .then(function(r) { return r.ok ? r.json() : null; })
            .then(function(data) {
                if (!data || !data.data || !data.data.length) return;
                var labels = data.data.map(function(p) { return p.hour; });
                var values = data.data.map(function(p) { return p.avg_power_w; });
                renderChart('hourlyLoadChart', {
                    type: 'bar',
                    data: {
                        labels: labels,
                        datasets: [{
                            label: 'Avg Power (W)',
                            data: values,
                            backgroundColor: '#48c78e',
                            borderWidth: 0
                        }]
                    },
                    options: baseOptions()
                });
            })
            .catch(function() {});
    }

    /* ── Load all charts for a given period ──────────────────── */
    function loadAllCharts(hours) {
        loadPvPowerChart(hours);
        loadBattVoltChart(hours);
        loadDeviceEnergyChart();
        loadHourlyLoadChart(hours);
    }

    /* ── Wire up time period buttons ─────────────────────────── */
    function setupPeriodButtons() {
        var btns = document.querySelectorAll('.dm-range-btn');
        btns.forEach(function(btn) {
            btn.addEventListener('click', function() {
                var hours = parseInt(btn.getAttribute('data-period'), 10);
                if (!hours) return;
                btns.forEach(function(b) { b.classList.remove('active'); });
                btn.classList.add('active');
                loadAllCharts(hours);
            });
        });
    }

    document.addEventListener('DOMContentLoaded', function() {
        setupPeriodButtons();
        loadAllCharts(24);
    });
})();
