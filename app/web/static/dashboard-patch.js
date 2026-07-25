/* dashboard-patch.js — dessmonitor CSP-clean v2 */
(function () {
    'use strict';

    function watchElement(srcId, callback) {
        var el = document.getElementById(srcId);
        if (!el) return;
        callback(el.textContent.trim());
        new MutationObserver(function () {
            callback(el.textContent.trim());
        }).observe(el, { childList: true, characterData: true, subtree: true });
    }

    function setupMiniOutputPower() {
        watchElement('ds-output-power', function (val) {
            var dst = document.getElementById('mini-output-power');
            if (!dst) return;
            dst.textContent = (val && val !== 'N/A') ? val + ' W' : '\u2014';
        });
    }

    function setupMiniBattery() {
        watchElement('ds-battery-voltage', function (val) {
            var v = parseFloat(val);
            var cls = isNaN(v) ? 'bat-amber' : v >= 26.5 ? 'bat-green' : v >= 25.3 ? 'bat-amber' : 'bat-red';
            var iconCls = cls === 'bat-green' ? 'green' : cls === 'bat-red' ? 'red' : 'amber';
            var wrap = document.getElementById('ds-battery-voltage-wrap');
            if (wrap) { wrap.classList.remove('bat-green','bat-amber','bat-red'); wrap.classList.add(cls); }
            var dst = document.getElementById('mini-battery-v');
            if (dst) { dst.textContent = (val && val !== 'N/A') ? val + ' V' : '\u2014'; dst.classList.remove('green','amber','red'); dst.classList.add(iconCls); }
            var icon = document.getElementById('mini-bat-icon');
            if (icon) { icon.classList.remove('green','amber','red'); icon.classList.add(iconCls); }
        });
    }

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

    function fixTs(raw) {
        if (!raw || raw === 'N/A' || raw === '-') return raw;
        if (/^\d{2}\/\d{2}\/\d{4}/.test(raw)) return raw;
        try {
            var iso = (raw.indexOf('T') !== -1 && raw.slice(-1) !== 'Z') ? raw + 'Z' : raw;
            var d = new Date(iso);
            if (isNaN(d.getTime())) return raw;
            return d.toLocaleString('en-GB', { timeZone: 'Europe/London', day:'2-digit', month:'2-digit', year:'numeric', hour:'2-digit', minute:'2-digit', second:'2-digit', hour12: false });
        } catch(e) { return raw; }
    }

    function setupTimestampFix() {
        ['inv-timestamp','footer-inv-time'].forEach(function(id) {
            var el = document.getElementById(id);
            if (!el) return;
            var busy = false;
            new MutationObserver(function() {
                if (busy) return;
                var raw = el.textContent.trim();
                var fixed = fixTs(raw);
                if (fixed !== raw) { busy = true; el.textContent = fixed; busy = false; }
            }).observe(el, { childList: true, characterData: true, subtree: true });
        });
    }

    function init() {
        setupMiniOutputPower();
        setupMiniBattery();
        setupToggle();
        setupTimestampFix();
    }

    if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', init); }
    else { init(); }
}());
