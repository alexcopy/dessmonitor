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
        return wh == null ? "—" : (wh / 1000).toFixed(2);
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
