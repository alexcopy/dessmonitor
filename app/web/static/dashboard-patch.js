/* dashboard-patch.js — dessmonitor
 * Патч поверх dashboard.js. Подключается как внешний файл (/static/dashboard-patch.js).
 * Не содержит inline-кода — CSP 'default-src self' совместим.
 *
 * Исправляет:
 *   1. mini-output-power / mini-battery-v — заполнение из ds-output-power / ds-battery-voltage
 *   2. Battery voltage — цветовой класс bat-green / bat-amber / bat-red
 *   3. Toggle switch — фильтрация неактивных строк в таблице loads
 *   4. Timestamp — корректировка timezone (observed_at без Z → UTC → Europe/London)
 */

(function () {
    'use strict';

    /* ── HELPERS ──────────────────────────────────────────────── */

    function watchElement(srcId, callback) {
        var el = document.getElementById(srcId);
        if (!el) return;
        callback(el.textContent.trim());
        new MutationObserver(function () {
            callback(el.textContent.trim());
        }).observe(el, { childList: true, characterData: true, subtree: true });
    }

    /* ── 1 + 2. MINI OUTPUT POWER ─────────────────────────────
       dashboard.js пишет inv.output_power в #ds-output-power.
       Синхронизируем в #mini-output-power.
    ──────────────────────────────────────────────────────────── */
    function setupMiniOutputPower() {
        watchElement('ds-output-power', function (val) {
            var dst = document.getElementById('mini-output-power');
            if (!dst) return;
            dst.textContent = (val && val !== 'N/A') ? val + ' W' : '\u2014';
        });
    }

    /* ── 2. BATTERY VOLTAGE COLOR + MINI BOX ─────────────────
       Пороги: >= 26.5 → green, >= 25.3 → amber, < 25.3 → red
       Применяется к #ds-battery-voltage-wrap (hero) и
       #mini-battery-v, #mini-bat-icon (мини-бокс).
    ──────────────────────────────────────────────────────────── */
    function setupMiniBattery() {
        watchElement('ds-battery-voltage', function (val) {
            var v = parseFloat(val);
            var cls = isNaN(v) ? 'bat-amber'
                    : v >= 26.5 ? 'bat-green'
                    : v >= 25.3 ? 'bat-amber'
                    : 'bat-red';
            var iconCls = cls === 'bat-green' ? 'green'
                        : cls === 'bat-red'   ? 'red'
                        : 'amber';

            /* hero wrap */
            var wrap = document.getElementById('ds-battery-voltage-wrap');
            if (wrap) {
                wrap.classList.remove('bat-green', 'bat-amber', 'bat-red');
                wrap.classList.add(cls);
            }

            /* mini-battery-v value */
            var dst = document.getElementById('mini-battery-v');
            if (dst) {
                dst.textContent = (val && val !== 'N/A') ? val + ' V' : '\u2014';
                dst.classList.remove('green', 'amber', 'red');
                dst.classList.add(iconCls);
            }

            /* mini-bat-icon color */
            var icon = document.getElementById('mini-bat-icon');
            if (icon) {
                icon.classList.remove('green', 'amber', 'red');
                icon.classList.add(iconCls);
            }
        });
    }

    /* ── 3. TOGGLE SWITCH ─────────────────────────────────────
       dashboard.js строит <tr> через createElement без классов.
       Определяем активные строки по наличию .dm-state-on.
       MutationObserver на tbody (dashboard.js сбрасывает его
       через textContent="" при каждом poll).
    ──────────────────────────────────────────────────────────── */
    function setupToggle() {
        var chk   = document.getElementById('loads-show-all');
        var tbody = document.getElementById('loads-table-body');
        if (!chk || !tbody) return;

        function applyFilter() {
            var showAll = chk.checked;
            tbody.querySelectorAll('tr').forEach(function (tr) {
                var isActive = !!tr.querySelector('.dm-state-on');
                if (isActive) {
                    tr.style.display = '';
                } else {
                    tr.style.display = showAll ? '' : 'none';
                }
            });
        }

        chk.addEventListener('change', applyFilter);

        /* dashboard.js очищает tbody.textContent → childList mutation */
        new MutationObserver(function () {
            setTimeout(applyFilter, 80);
        }).observe(tbody, { childList: true });
    }

    /* ── 4. TIMESTAMP TIMEZONE ────────────────────────────────
       dashboard.js: formatLondonTimestamp(inv.observed_at)
         → new Date("2026-07-25T13:46:39")   ← без Z
         → парсится как LOCAL time, не UTC   ← баг

       Патч: если #inv-timestamp содержит ISO-строку без Z —
       добавляем Z и конвертируем в Europe/London.
       Если уже в формате dd/mm/yyyy — не трогаем.
    ──────────────────────────────────────────────────────────── */
    function fixTs(raw) {
        if (!raw || raw === 'N/A' || raw === '-' || raw === '\u2014') return raw;
        /* уже отформатировано en-GB: "25/07/2026, 14:06:20" */
        if (/^\d{2}\/\d{2}\/\d{4}/.test(raw)) return raw;
        try {
            /* "2026-07-25T13:46:39" → добавляем Z → явный UTC */
            var iso = (raw.indexOf('T') !== -1 && raw.slice(-1) !== 'Z')
                ? raw + 'Z' : raw;
            var d = new Date(iso);
            if (isNaN(d.getTime())) return raw;
            return d.toLocaleString('en-GB', {
                timeZone: 'Europe/London',
                day: '2-digit', month: '2-digit', year: 'numeric',
                hour: '2-digit', minute: '2-digit', second: '2-digit',
                hour12: false
            });
        } catch (e) { return raw; }
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
                if (fixed !== raw) {
                    busy = true;
                    el.textContent = fixed;
                    busy = false;
                }
            }).observe(el, { childList: true, characterData: true, subtree: true });
        });
    }

    /* ── INIT ─────────────────────────────────────────────────
       MutationObserver реактивен — ждать первого poll не нужно.
    ──────────────────────────────────────────────────────────── */
    function init() {
        setupMiniOutputPower();
        setupMiniBattery();
        setupToggle();
        setupTimestampFix();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

}());
