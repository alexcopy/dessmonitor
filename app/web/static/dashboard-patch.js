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

        /* Operator default: active loads only. All is an explicit operator action. */
        chk.checked = false;

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
