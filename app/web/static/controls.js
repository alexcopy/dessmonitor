/* controls.js — Charging Session History chart and events table */
(function () {
    var chartInst = null;

    /* colour a bar by starting SOC:
       0-30 % → red   30-60 % → amber   60-100 % → teal */
    function socColor(soc) {
        if (soc < 30)  return '#ef5350';   /* red    */
        if (soc < 60)  return '#ffa726';   /* amber  */
        return '#26a69a';                  /* teal   */
    }

    /* "Oct 18 07:10" from ISO string (UTC→local) */
    function fmtDT(iso) {
        var d = new Date(iso);
        return d.toLocaleDateString('en-GB', { month: 'short', day: 'numeric' })
             + ' '
             + d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
    }

    function renderSessionChart(sessions) {
        var canvas = document.getElementById('chargeVoltChart');
        if (!canvas) return;

        /* newest first from API → reverse for chronological left→right */
        var ordered = sessions.slice().reverse();

        var labels  = ordered.map(function(s) { return fmtDT(s.start); });
        var heights = ordered.map(function(s) {
            /* SOC gained; floor at 1 so zero-gain sessions still show */
            return Math.max(s.soc_end - s.soc_start, 1);
        });
        var colors  = ordered.map(function(s) { return socColor(s.soc_start); });

        if (chartInst) { chartInst.destroy(); }
        chartInst = new Chart(canvas, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: 'SOC gained (%)',
                    data: heights,
                    backgroundColor: colors,
                    borderColor: colors,
                    borderWidth: 1,
                    borderRadius: 3
                }]
            },
            options: {
                responsive: true,
                animation: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            title: function(items) {
                                var s = ordered[items[0].dataIndex];
                                return fmtDT(s.start) + ' – ' + fmtDT(s.end);
                            },
                            label: function(ctx) {
                                var s = ordered[ctx.dataIndex];
                                var dur = s.dur_min >= 60
                                    ? Math.floor(s.dur_min/60) + 'h ' + (s.dur_min%60) + 'm'
                                    : s.dur_min + ' min';
                                return [
                                    'SOC: ' + s.soc_start + '% → ' + s.soc_end + '%',
                                    'Voltage: ' + s.v_start.toFixed(1) + 'V → ' + s.v_end.toFixed(1) + 'V',
                                    'Duration: ' + dur,
                                    'Max current: ' + s.max_chg_a.toFixed(1) + ' A'
                                ];
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        ticks: {
                            color: '#888',
                            font: { size: 9 },
                            maxRotation: 45,
                            autoSkip: true,
                            maxTicksLimit: 16
                        },
                        grid: { color: 'rgba(255,255,255,0.04)' }
                    },
                    y: {
                        min: 0,
                        max: 100,
                        title: {
                            display: true,
                            text: 'SOC gained (%)',
                            color: '#888',
                            font: { size: 10 }
                        },
                        ticks: {
                            color: '#888',
                            font: { size: 10 },
                            callback: function(v) { return v + '%'; }
                        },
                        grid: { color: 'rgba(255,255,255,0.06)' }
                    }
                }
            }
        });
    }

    function renderEventsTable(sessions) {
        var tbody = document.getElementById('chargeEventsBody');
        if (!tbody) return;
        if (!sessions || !sessions.length) {
            tbody.innerHTML = '<tr><td colspan="6" class="dm-mono">No charge sessions yet</td></tr>';
            return;
        }
        tbody.innerHTML = sessions.map(function(s) {
            var dur = s.dur_min >= 60
                ? Math.floor(s.dur_min/60) + 'h ' + (s.dur_min % 60) + 'm'
                : s.dur_min + ' min';
            var socGain = s.soc_end - s.soc_start;
            var color = s.soc_start < 30 ? 'red-text' : s.soc_start < 60 ? 'amber-text' : 'teal-text';
            return '<tr>' +
                '<td class="dm-mono">' + (s.start || '').substring(0, 16).replace('T', ' ') + '</td>' +
                '<td class="dm-mono ' + color + '">' + s.soc_start + '% → ' + s.soc_end + '%</td>' +
                '<td class="dm-mono amber-text">' + s.v_start.toFixed(1) + 'V → ' + s.v_end.toFixed(1) + 'V</td>' +
                '<td class="dm-mono blue-text">' + dur + '</td>' +
                '<td class="dm-mono green-text">+' + socGain + '%</td>' +
                '<td class="dm-mono teal-text">' + s.max_chg_a.toFixed(1) + ' A</td>' +
                '</tr>';
        }).join('');
    }

    function loadChargeHistory(days) {
        fetch('/api/charge/history?days=' + (days || 14))
            .then(function(r) { return r.ok ? r.json() : null; })
            .then(function(data) {
                if (!data) return;
                renderSessionChart(data.sessions || []);
                renderEventsTable(data.sessions || []);
            })
            .catch(function() {});
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
