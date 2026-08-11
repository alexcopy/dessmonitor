/* dashboard-patch.js — dessmonitor CSP-clean v3
 * No inline styles, no eval, no external resources.
 *
 * 1. mini-output-power / mini-battery-v sync
 * 2. Battery voltage color (LiFePO4 24V thresholds)
 * 3. Battery GAUGE — replaces SOC gauge with voltage-based arc
 *    LiFePO4 24V: 24.0V = 0%, 29.2V = 100%
 * 4. Toggle switch for loads table
 * 5. Timestamp timezone fix (observed_at UTC → Europe/London)
 * 6. Stale data warning — red highlight if last update > 10 min
 */
(function () {
    'use strict';

    /* ── LIFEPO4 VOLTAGE → PERCENT ───────────────────────────── */
    var LIFEPO4_MIN = 24.0;
    var LIFEPO4_MAX = 29.2;

    function voltToPct(v) {
        if (isNaN(v)) return null;
        var pct = (v - LIFEPO4_MIN) / (LIFEPO4_MAX - LIFEPO4_MIN) * 100;
        return Math.max(0, Math.min(100, Math.round(pct)));
    }

    /* ── HELPERS ──────────────────────────────────────────────── */
    function watchElement(srcId, callback) {
        var el = document.getElementById(srcId);
        if (!el) return;
        callback(el.textContent.trim());
        new MutationObserver(function () {
            callback(el.textContent.trim());
        }).observe(el, { childList: true, characterData: true, subtree: true });
    }

    /* ── 1. MINI OUTPUT POWER ─────────────────────────────────── */
    function setupMiniOutputPower() {
        watchElement('ds-output-power', function (val) {
            var dst = document.getElementById('mini-output-power');
            if (!dst) return;
            dst.textContent = (val && val !== 'N/A') ? val + ' W' : '\u2014';
        });
    }

    /* ── 2 + 3. BATTERY VOLTAGE COLOR + GAUGE ────────────────────
       LiFePO4 24V thresholds:
       >= 27.2V  → green  (>= 80% capacity)
       >= 25.6V  → amber  (>= 20% capacity)
       <  25.6V  → red    (< 20%, critical)
    ──────────────────────────────────────────────────────────── */
    function renderBatteryGauge(voltage) {
        var pct = voltToPct(voltage);
        var fill = document.getElementById('socGaugeFill');
        var label = document.getElementById('socPct');
        if (!fill || !label) return;

        if (pct === null) {
            label.textContent = '\u2014';
            fill.setAttribute('d', 'M 11 70 A 54 54 0 0 1 11.01 70');
            fill.setAttribute('stroke', '#30363d');
            return;
        }

        /* arc calculation — same geometry as SOC gauge */
        var angle = (pct / 100) * Math.PI;
        var cx = 65, cy = 70, r = 54;
        var x1 = cx + r * Math.cos(Math.PI);
        var y1 = cy + r * Math.sin(Math.PI);
        var x2 = cx + r * Math.cos(Math.PI + angle);
        var y2 = cy + r * Math.sin(Math.PI + angle);
        var largeArc = angle > Math.PI ? 1 : 0;
        fill.setAttribute('d',
            'M ' + x1.toFixed(1) + ' ' + y1.toFixed(1) +
            ' A ' + r + ' ' + r + ' 0 ' + largeArc + ' 1 ' +
            x2.toFixed(1) + ' ' + y2.toFixed(1)
        );

        /* color by voltage */
        var color = voltage >= 27.2 ? '#23d160'
                  : voltage >= 25.6 ? '#f5a623'
                  : '#ff3860';
        fill.setAttribute('stroke', color);
        label.textContent = voltage.toFixed(1) + ' V';
        label.className = 'dm-gauge-pct';

        /* remove old color classes, add new */
        label.classList.remove('green-text', 'amber-text', 'red-text');
        label.classList.add(
            voltage >= 27.2 ? 'green-text' :
            voltage >= 25.6 ? 'amber-text' : 'red-text'
        );
    }

    function setupMiniBattery() {
        watchElement('ds-battery-voltage', function (val) {
            var v = parseFloat(val);
            var cls = isNaN(v) ? 'bat-amber'
                    : v >= 27.2 ? 'bat-green'
                    : v >= 25.6 ? 'bat-amber'
                    : 'bat-red';
            var iconCls = cls === 'bat-green' ? 'green'
                        : cls === 'bat-red'   ? 'red'
                        : 'amber';

            /* hero wrap color */
            var wrap = document.getElementById('ds-battery-voltage-wrap');
            if (wrap) {
                wrap.classList.remove('bat-green', 'bat-amber', 'bat-red');
                wrap.classList.add(cls);
            }

            /* mini-battery-v */
            var dst = document.getElementById('mini-battery-v');
            if (dst) {
                dst.textContent = (val && val !== 'N/A') ? val + ' V' : '\u2014';
                dst.classList.remove('green', 'amber', 'red');
                dst.classList.add(iconCls);
            }

            /* mini-bat-icon */
            var icon = document.getElementById('mini-bat-icon');
            if (icon) {
                icon.classList.remove('green', 'amber', 'red');
                icon.classList.add(iconCls);
            }

            /* battery gauge arc */
            if (!isNaN(v)) renderBatteryGauge(v);
        });
    }

    /* ── 4. TOGGLE SWITCH ─────────────────────────────────────── */
    function setupToggle() {
        var chk   = document.getElementById('loads-show-all');
        var tbody = document.getElementById('loads-table-body');
        if (!chk || !tbody) return;
        function applyFilter() {
            if (chk.checked) { tbody.classList.remove('dm-active-only'); }
            else              { tbody.classList.add('dm-active-only'); }
        }
        chk.addEventListener('change', applyFilter);
        applyFilter();
    }

    /* ── 5. TIMESTAMP TIMEZONE ────────────────────────────────── */
    function fixTs(raw) {
        if (!raw || raw === 'N/A' || raw === '-') return raw;
        if (/^\d{2}\/\d{2}\/\d{4}/.test(raw)) return raw;
        try {
            var iso = (raw.indexOf('T') !== -1 && raw.slice(-1) !== 'Z') ? raw + 'Z' : raw;
            var d = new Date(iso);
            if (isNaN(d.getTime())) return raw;
            return d.toLocaleString('en-GB', {
                timeZone: 'Europe/London',
                day: '2-digit', month: '2-digit', year: 'numeric',
                hour: '2-digit', minute: '2-digit', second: '2-digit',
                hour12: false
            });
        } catch(e) { return raw; }
    }

    function setupTimestampFix() {
        ['inv-timestamp', 'footer-inv-time'].forEach(function (id) {
            var el = document.getElementById(id);
            if (!el) return;
            var busy = false;
            new MutationObserver(function () {
                if (busy) return;
                var raw = el.textContent.trim();
                var fixed = fixTs(raw);
                if (fixed !== raw) { busy = true; el.textContent = fixed; busy = false; }
            }).observe(el, { childList: true, characterData: true, subtree: true });
        });
    }

    /* ── 6. STALE DATA WARNING ────────────────────────────────────
       "Updated Xs ago" в footer — подсвечиваем если > 10 минут.
       dashboard.js пишет в #footer-updated.
       Добавляем класс dm-footer-stale если > 600s.
    ──────────────────────────────────────────────────────────── */
    function setupStaleWarning() {
        var el = document.getElementById('footer-updated');
        if (!el) return;
        new MutationObserver(function () {
            var txt = el.textContent.trim();
            /* парсим "Xs ago" */
            var m = txt.match(/(\d+)s ago/);
            if (!m) return;
            var secs = parseInt(m[1], 10);
            var footer = document.querySelector('.dm-footer-bar');
            if (!footer) return;
            if (secs > 600) {
                footer.classList.add('dm-footer-stale');
            } else {
                footer.classList.remove('dm-footer-stale');
            }
        }).observe(el, { childList: true, characterData: true, subtree: true });
    }

    /* ── INIT ─────────────────────────────────────────────────── */
    function init() {
        setupMiniOutputPower();
        setupMiniBattery();
        setupToggle();
        setupTimestampFix();
        setupStaleWarning();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
}());

/* ── Energy Today ─────────────────────────────────────── */
function wh2kwh(wh) {
    return wh == null ? "—" : (wh / 1000).toFixed(2) + " kWh";
}

function loadEnergyToday() {
    fetch("/api/energy/daily")
        .then(function(r) { return r.ok ? r.json() : null; })
        .then(function(data) {
            if (!data || !data.today) return;
            var t = data.today;
            var ep = document.getElementById("energy-pv");
            var eb = document.getElementById("energy-batt");
            var eg = document.getElementById("energy-grid");
            var el = document.getElementById("energy-load");
            if (ep) {
                var netBatt = (t.battery_wh || 0) - (t.charge_wh || 0);
                var solarTotal = (t.pv_wh || 0) + Math.max(0, netBatt);
                ep.textContent = wh2kwh(solarTotal);
                ep.title = "PV: " + wh2kwh(t.pv_wh) + " + Net Battery: " + wh2kwh(netBatt);
            }
            if (eb) {
                var selfPct = t.load_wh > 0
                    ? Math.round((t.solar_total_wh / t.load_wh) * 100)
                    : 0;
                eb.textContent = Math.min(selfPct, 100) + "% self";
                eb.title = "Self-sufficiency: solar / total load";
            }
            if (eg) eg.textContent = t.grid_wh > 0 ? wh2kwh(t.grid_wh) : "0.00 kWh";
            if (el) el.textContent = wh2kwh(t.load_wh);
        })
        .catch(function() {});
}

/* Load once on page load, then every 5 minutes */
document.addEventListener("DOMContentLoaded", function() {
    loadEnergyToday();
    setInterval(loadEnergyToday, 300000);
});

/* ── Overload Alert Banner ───────────────────────────────── */
(function() {
    var LEVELS = {
        soft:     { cls: "dm-overload-soft",     msg: "⚠ High battery current (>40A) — monitoring" },
        hard:     { cls: "dm-overload-hard",     msg: "🔴 Battery overload (>50A) — shedding loads" },
        critical: { cls: "dm-overload-critical", msg: "🚨 Critical overload (>2000W) — emergency shed" },
    };

    function checkOverload() {
        fetch("/api/overload/alert")
            .then(function(r) { return r.ok ? r.json() : null; })
            .then(function(data) {
                if (!data) return;
                var banner = document.getElementById("overload-banner");
                if (!banner) return;
                var level = data.level || "ok";
                if (level === "ok") {
                    banner.className = "dm-overload-banner dm-hidden";
                    banner.textContent = "";
                    return;
                }
                var info = LEVELS[level] || LEVELS.soft;
                banner.className = "dm-overload-banner " + info.cls;
                banner.textContent = info.msg +
                    " | " + (data.battery_current_dis || 0).toFixed(0) + "A" +
                    " | " + (data.output_power_w || 0).toFixed(0) + "W";
            })
            .catch(function() {});
    }

    document.addEventListener("DOMContentLoaded", function() {
        checkOverload();
        setInterval(checkOverload, 30000);
    });
})();

/* ── Device Energy Today ─────────────────────────────────── */
function loadDeviceEnergyToday() {
    fetch("/api/energy/devices/today")
        .then(function(r) { return r.ok ? r.json() : null; })
        .then(function(data) {
            if (!data || !data.devices) return;
            var map = {};
            data.devices.forEach(function(d) {
                map[d.device_name.toLowerCase()] = d;
            });
            window._deviceEnergyToday = map;
        })
        .catch(function() {});
}

document.addEventListener("DOMContentLoaded", function() {
    loadDeviceEnergyToday();
    setInterval(loadDeviceEnergyToday, 300000); // refresh every 5 min
});

/* ── Overload Events Table ───────────────────────────────── */
function loadOverloadEvents() {
    fetch("/api/overload/events")
        .then(function(r) { return r.ok ? r.json() : null; })
        .then(function(data) {
            if (!data) return;
            var tbody = document.getElementById("overloadEventsBody");
            var badge = document.getElementById("overloadEventsCount");
            var card  = document.getElementById("overloadEventsCard");
            if (!tbody) return;

            var total = data.total || 0;
            if (badge) {
                if (total > 0) {
                    badge.textContent = total + " events";
                    badge.style.display = "";
                } else {
                    badge.style.display = "none";
                }
            }

            if (!data.events || !data.events.length) {
                tbody.innerHTML = '<tr><td colspan="2" class="dm-mono">No overload events today</td></tr>';
                return;
            }

            tbody.innerHTML = data.events.slice(0, 10).map(function(e) {
                var cls = e.msg.indexOf("CRITICAL") >= 0 ? "red-text"
                        : e.msg.indexOf("HARD") >= 0 ? "amber-text"
                        : e.msg.indexOf("shed") >= 0 ? "amber-text"
                        : "teal-text";
                return '<tr>' +
                    '<td class="dm-mono" style="white-space:nowrap;font-size:0.75rem">' + e.ts + '</td>' +
                    '<td class="dm-mono ' + cls + '" style="font-size:0.75rem">' + e.msg + '</td>' +
                    '</tr>';
            }).join('');
        })
        .catch(function() {});
}

document.addEventListener("DOMContentLoaded", function() {
    loadOverloadEvents();
    setInterval(loadOverloadEvents, 60000);
});

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
                        status = bv >= d.max_volt
                            ? '<span class="green-text">ready to ON</span>'
                            : '<span style="color:var(--text-dim)">waiting ' + d.max_volt.toFixed(1) + 'V</span>';
                    }
                }

                return '<tr>' +
                    '<td class="dm-mono">' + (d.desc || d.name) + '</td>' +
                    '<td class="dm-mono amber-text">' + d.max_volt.toFixed(1) + 'V</td>' +
                    '<td class="dm-mono red-text">' + d.min_volt.toFixed(1) + 'V</td>' +
                    '<td class="dm-mono ' + (hasSolar ? 'teal-text' : 'dm-text-dim') + '">' +
                        (hasSolar ? d.max_volt.toFixed(1) + 'V' : '—') + '</td>' +
                    '<td class="dm-mono ' + (hasSolar ? 'teal-text' : 'dm-text-dim') + '">' +
                        (hasSolar ? d.solar_min_volt.toFixed(1) + 'V' : '—') + '</td>' +
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
