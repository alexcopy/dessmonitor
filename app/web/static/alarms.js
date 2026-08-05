/* dessmonitor — Alarms page */
"use strict";

function loadAlarms() {
    fetch("/api/overload/events")
        .then(function(r) { return r.ok ? r.json() : null; })
        .then(function(data) {
            if (!data) return;

            var tbody = document.getElementById("alarmsBody");
            var todayCount = document.getElementById("alarmsTodayCount");
            var shedCount = document.getElementById("alarmsShedCount");
            var currentStatus = document.getElementById("alarmsCurrentStatus");

            var total = data.total || 0;
            var events = data.events || [];
            var sheds = events.filter(function(e) { return e.msg.indexOf("shed") >= 0; });

            if (todayCount) todayCount.textContent = total;
            if (shedCount) shedCount.textContent = sheds.length;

            if (!tbody) return;
            if (!events.length) {
                tbody.innerHTML = '<tr><td colspan="2" class="dm-mono">No overload events today ✓</td></tr>';
                if (currentStatus) { currentStatus.textContent = "OK"; currentStatus.className = "dm-summary-val green-text"; }
                return;
            }

            // Current status from latest event
            if (currentStatus && events[0]) {
                var latest = events[0].msg;
                var lvl = latest.indexOf("CRITICAL") >= 0 ? "CRITICAL"
                        : latest.indexOf("HARD") >= 0 ? "HARD"
                        : latest.indexOf("SOFT") >= 0 ? "SOFT" : "OK";
                currentStatus.textContent = lvl;
                currentStatus.className = "dm-summary-val " +
                    (lvl === "OK" ? "green-text" : lvl === "SOFT" ? "amber-text" : "red-text");
            }

            tbody.innerHTML = events.map(function(e) {
                var cls = e.msg.indexOf("CRITICAL") >= 0 ? "red-text"
                        : e.msg.indexOf("shed") >= 0 ? "amber-text"
                        : e.msg.indexOf("HARD") >= 0 ? "amber-text"
                        : e.msg.indexOf("OK") === 0 || e.msg.indexOf("→ OK") >= 0 ? "green-text"
                        : "teal-text";
                return '<tr>' +
                    '<td class="dm-mono" style="white-space:nowrap">' + e.ts + '</td>' +
                    '<td class="dm-mono ' + cls + '">' + e.msg + '</td>' +
                    '</tr>';
            }).join('');
        })
        .catch(function() {});

    // Also update overload banner
    fetch("/api/overload/alert")
        .then(function(r) { return r.ok ? r.json() : null; })
        .then(function(data) {
            if (!data) return;
            var banner = document.getElementById("overload-banner");
            if (!banner) return;
            var LEVELS = {
                soft:     { cls: "dm-overload-soft",     msg: "⚠ High battery current (>40A)" },
                hard:     { cls: "dm-overload-hard",     msg: "🔴 Battery overload (>50A) — shedding loads" },
                critical: { cls: "dm-overload-critical", msg: "🚨 Critical overload (>2000W)" },
            };
            var level = data.level || "ok";
            if (level === "ok") {
                banner.className = "dm-overload-banner dm-hidden";
                return;
            }
            var info = LEVELS[level] || LEVELS.soft;
            banner.className = "dm-overload-banner " + info.cls;
            banner.textContent = info.msg + " | " +
                (data.battery_current_dis || 0).toFixed(0) + "A | " +
                (data.output_power_w || 0).toFixed(0) + "W";
        });
}

document.addEventListener("DOMContentLoaded", function() {
    loadAlarms();
    setInterval(loadAlarms, 30000);
});

/* Help modal */
document.addEventListener("DOMContentLoaded", function() {
    var btn   = document.getElementById("helpBtn");
    var modal = document.getElementById("helpModal");
    var close = document.getElementById("helpClose");
    if (btn && modal) {
        btn.addEventListener("click", function() { modal.classList.remove("dm-hidden"); });
        close.addEventListener("click", function() { modal.classList.add("dm-hidden"); });
        modal.addEventListener("click", function(e) {
            if (e.target === modal) modal.classList.add("dm-hidden");
        });
    }
});
