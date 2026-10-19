/* controls.js — Grid Charging History: daily kWh bar chart + session table */
(function () {
    var chartInst = null;

    /* "19 Oct" from YYYY-MM-DD */
    function fmtDay(iso) {
        var d = new Date(iso + 'T00:00:00Z');
        return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' });
    }

    /* "Oct 19 07:10" from ISO datetime */
    function fmtDT(iso) {
        var d = new Date(iso);
        return d.toLocaleDateString('en-GB', { month: 'short', day: 'numeric' })
             + ' '
             + d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
    }

    /* duration in minutes → "1h 50m" or "45 min" */
    function fmtDur(min) {
        min = Math.round(min);
        if (min >= 60) return Math.floor(min / 60) + 'h ' + (min % 60) + 'm';
        return min + ' min';
    }

    function renderDailyChart(daily) {
        var canvas = document.getElementById('chargeVoltChart');
        if (!canvas) return;

        var labels  = daily.map(function(d) { return fmtDay(d.day); });
        var kwh     = daily.map(function(d) { return d.energy_kwh; });
        var counts  = daily.map(function(d) { return d.sessions; });

        if (chartInst) { chartInst.destroy(); }
        chartInst = new Chart(canvas, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Grid energy (kWh)',
                    data: kwh,
                    backgroundColor: 'rgba(41,182,246,0.75)',
                    borderColor:     '#29b6f6',
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
                                var idx = items[0].dataIndex;
                                return fmtDay(daily[idx].day);
                            },
                            label: function(ctx) {
                                var d = daily[ctx.dataIndex];
                                return [
                                    'Energy: ' + d.energy_kwh.toFixed(3) + ' kWh',
                                    'Sessions: ' + d.sessions
                                ];
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        ticks: { color: '#888', font: { size: 10 } },
                        grid:  { color: 'rgba(255,255,255,0.04)' }
                    },
                    y: {
                        min: 0,
                        title: {
                            display: true,
                            text: 'kWh from grid',
                            color: '#888',
                            font: { size: 10 }
                        },
                        ticks: {
                            color: '#888',
                            font: { size: 10 },
                            callback: function(v) { return v.toFixed(2); }
                        },
                        grid: { color: 'rgba(255,255,255,0.06)' }
                    }
                }
            }
        });
    }

    function renderSessionsTable(sessions) {
        var tbody = document.getElementById('chargeEventsBody');
        if (!tbody) return;
        if (!sessions || !sessions.length) {
            tbody.innerHTML = '<tr><td colspan="6" class="dm-mono">No grid charge sessions yet</td></tr>';
            return;
        }
        tbody.innerHTML = sessions.map(function(s) {
            return '<tr>' +
                '<td class="dm-mono">'                            + fmtDT(s.start)                         + '</td>' +
                '<td class="dm-mono blue-text">'                  + fmtDur(s.dur_min)                      + '</td>' +
                '<td class="dm-mono amber-text">'                 + s.avg_a.toFixed(1) + ' A'              + '</td>' +
                '<td class="dm-mono teal-text">'                  + Math.round(s.avg_w) + ' W'             + '</td>' +
                '<td class="dm-mono green-text">'                 + s.energy_kwh.toFixed(3) + ' kWh'       + '</td>' +
                '<td class="dm-mono" style="color:#aaa">'         + s.v_start.toFixed(1) + '→' + s.v_end.toFixed(1) + 'V</td>' +
                '</tr>';
        }).join('');
    }

    function loadChargeHistory(days) {
        fetch('/api/charge/history?days=' + (days || 14))
            .then(function(r) { return r.ok ? r.json() : null; })
            .then(function(data) {
                if (!data) return;
                renderDailyChart(data.daily   || []);
                renderSessionsTable(data.sessions || []);
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
