/* controls.js — Night Charge History chart and events table */
(function() {
    var chartInst = null;

    function loadChargeHistory(days) {
        fetch('/api/charge/history?days=' + (days || 14))
            .then(function(r) { return r.ok ? r.json() : null; })
            .then(function(data) {
                if (!data) return;
                renderVoltChart(data.voltage || []);
                renderEventsTable(data.events || []);
            })
            .catch(function() {});
    }

    function renderVoltChart(points) {
        var canvas = document.getElementById('chargeVoltChart');
        if (!canvas) return;

        var labels = points.map(function(p) { return p.time; });
        var volts  = points.map(function(p) { return p.v; });
        var colors = points.map(function(p) {
            return p.mode === 'Line Mode'
                ? 'rgba(100,149,237,0.8)'
                : 'rgba(255,221,87,0.8)';
        });

        if (chartInst) { chartInst.destroy(); }
        chartInst = new Chart(canvas, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Battery Voltage (V)',
                    data: volts,
                    borderColor: '#ffdd57',
                    backgroundColor: 'rgba(255,221,87,0.1)',
                    pointBackgroundColor: colors,
                    pointRadius: 2,
                    borderWidth: 1.5,
                    fill: true,
                    tension: 0.2,
                }]
            },
            options: {
                responsive: true,
                animation: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: function(ctx) {
                                var p = points[ctx.dataIndex];
                                return 'V: ' + ctx.parsed.y.toFixed(1) +
                                    ' | ' + (p.mode || '—') +
                                    (p.pv > 0 ? ' | PV: ' + p.pv + 'W' : '');
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        display: true,
                        ticks: {
                            maxTicksLimit: 10,
                            color: '#888',
                            font: { size: 10 },
                            callback: function(val, idx) {
                                var t = labels[idx];
                                return t ? t.substring(5, 16).replace('T', ' ') : '';
                            }
                        },
                        grid: { color: 'rgba(255,255,255,0.05)' }
                    },
                    y: {
                        min: 22,
                        max: 29,
                        ticks: { color: '#888', font: { size: 10 } },
                        grid: { color: 'rgba(255,255,255,0.05)' }
                    }
                }
            }
        });
    }

    function renderEventsTable(events) {
        var tbody = document.getElementById('chargeEventsBody');
        if (!tbody) return;
        if (!events.length) {
            tbody.innerHTML = '<tr><td colspan="5" class="dm-mono">No charge events yet</td></tr>';
            return;
        }
        tbody.innerHTML = events.map(function(e) {
            var isOn  = e.event === 'enabled';
            var color = isOn ? 'amber-text' : 'teal-text';
            var icon  = isOn ? '&#8679;' : '&#8681;';
            return '<tr>' +
                '<td class="dm-mono">' + (e.time || '').substring(0, 19).replace('T', ' ') + '</td>' +
                '<td class="dm-mono ' + color + '">' + icon + ' ' + e.event + '</td>' +
                '<td class="dm-mono amber-text">' + (e.voltage ? e.voltage.toFixed(1) + 'V' : '—') + '</td>' +
                '<td class="dm-mono blue-text">' + (e.duration_min ? e.duration_min.toFixed(0) + ' min' : '—') + '</td>' +
                '<td class="dm-mono green-text">' + (e.energy_kwh ? e.energy_kwh.toFixed(3) + ' kWh' : '—') + '</td>' +
                '</tr>';
        }).join('');
    }

    document.addEventListener('DOMContentLoaded', function() {
        loadChargeHistory(14);
        var sel = document.getElementById('chargeHistoryDays');
        if (sel) {
            sel.addEventListener('change', function() {
                loadChargeHistory(parseInt(this.value));
            });
        }
    });
})();
