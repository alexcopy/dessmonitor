/* controls.js — Night Charge History chart and events table */
(function () {
    var chartInst = null;

    /* palette for up to 14 night sessions */
    var SESSION_COLORS = [
        '#4fc3f7','#81d4fa','#29b6f6','#0288d1',
        '#ffb74d','#ffa726','#fb8c00','#f57c00',
        '#80cbc4','#4db6ac','#26a69a','#00897b',
        '#ce93d8','#ba68c8'
    ];

    /* Split flat point array into sessions; gap > 3 h → new session */
    function splitSessions(points) {
        var GAP_MS = 3 * 60 * 60 * 1000;
        var sessions = [];
        var cur = [];
        for (var i = 0; i < points.length; i++) {
            if (cur.length && (new Date(points[i].time) - new Date(cur[cur.length-1].time)) > GAP_MS) {
                sessions.push(cur);
                cur = [];
            }
            cur.push(points[i]);
        }
        if (cur.length) sessions.push(cur);
        return sessions;
    }

    /* "Oct 18" label from ISO string */
    function nightLabel(isoStr) {
        var d = new Date(isoStr);
        return d.toLocaleDateString('en-GB', { month: 'short', day: 'numeric' });
    }

    function renderVoltChart(points) {
        var canvas = document.getElementById('chargeVoltChart');
        if (!canvas) return;

        var sessions = splitSessions(points);

        /* Build a merged time-axis (union of all session timestamps) */
        var allTimes = [];
        sessions.forEach(function(s) {
            s.forEach(function(p) { allTimes.push(p.time); });
        });
        allTimes.sort();

        /* One dataset per session */
        var datasets = sessions.map(function(sess, idx) {
            var color = SESSION_COLORS[idx % SESSION_COLORS.length];
            /* sparse array — null for timestamps not in this session */
            var sessSet = {};
            sess.forEach(function(p) { sessSet[p.time] = p; });
            var data = allTimes.map(function(t) {
                return sessSet[t] ? sessSet[t].v : null;
            });
            return {
                label: nightLabel(sess[0].time),
                data: data,
                borderColor: color,
                backgroundColor: color.replace(')', ',0.08)').replace('rgb', 'rgba'),
                pointBackgroundColor: color,
                pointRadius: 0,
                pointHoverRadius: 4,
                borderWidth: 2,
                fill: false,
                tension: 0.3,
                spanGaps: false
            };
        });

        if (chartInst) { chartInst.destroy(); }
        chartInst = new Chart(canvas, {
            type: 'line',
            data: { labels: allTimes, datasets: datasets },
            options: {
                responsive: true,
                animation: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: {
                        display: true,
                        position: 'bottom',
                        labels: {
                            color: '#aaa',
                            font: { size: 11 },
                            boxWidth: 12,
                            padding: 8,
                            usePointStyle: true
                        }
                    },
                    tooltip: {
                        callbacks: {
                            title: function(items) {
                                var t = new Date(items[0].label);
                                return t.toLocaleString('en-GB', {
                                    month: 'short', day: 'numeric',
                                    hour: '2-digit', minute: '2-digit'
                                });
                            },
                            label: function(ctx) {
                                if (ctx.raw === null) return null;
                                return ctx.dataset.label + ': ' + ctx.raw.toFixed(1) + ' V';
                            }
                        },
                        filter: function(item) { return item.raw !== null; }
                    }
                },
                scales: {
                    x: {
                        display: true,
                        ticks: {
                            maxTicksLimit: 8,
                            color: '#888',
                            font: { size: 10 },
                            callback: function(val, idx) {
                                var t = new Date(allTimes[idx]);
                                if (!allTimes[idx]) return '';
                                /* show date only at midnight transitions */
                                return t.toLocaleString('en-GB', {
                                    month: 'short', day: 'numeric',
                                    hour: '2-digit', minute: '2-digit'
                                });
                            }
                        },
                        grid: { color: 'rgba(255,255,255,0.04)' }
                    },
                    y: {
                        min: 22,
                        max: 29,
                        title: {
                            display: true,
                            text: 'Voltage (V)',
                            color: '#888',
                            font: { size: 10 }
                        },
                        ticks: {
                            color: '#888',
                            font: { size: 10 },
                            callback: function(v) { return v.toFixed(0) + 'V'; }
                        },
                        grid: { color: 'rgba(255,255,255,0.06)' }
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
