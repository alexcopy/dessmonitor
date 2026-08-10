/* devices-editor.js — dessmonitor device editor
 * CSP 'default-src self' compliant. No inline styles, no eval.
 *
 * Loads devices from /api/devices, renders form fields,
 * saves back via POST /api/devices with CSRF token.
 */
(function () {
    'use strict';

    var state = { devices: [] };

    /* ── DOM HELPERS ──────────────────────────────────────────── */
    function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text !== undefined) e.textContent = text;
        return e;
    }

    function inp(cls, val, placeholder, type) {
        var e = document.createElement('input');
        e.className = 'de-field-input' + (cls ? ' ' + cls : '');
        e.type = type || 'text';
        e.value = val !== null && val !== undefined ? String(val) : '';
        if (placeholder) e.placeholder = placeholder;
        return e;
    }

    function sel(cls, options, current) {
        var e = document.createElement('select');
        e.className = 'de-field-select' + (cls ? ' ' + cls : '');
        options.forEach(function (opt) {
            var o = document.createElement('option');
            o.value = opt;
            o.textContent = opt;
            if (opt === current) o.selected = true;
            e.appendChild(o);
        });
        return e;
    }

    function field(label, input) {
        var wrap = el('div', 'de-field');
        wrap.appendChild(el('label', 'de-field-label', label));
        wrap.appendChild(input);
        return wrap;
    }

    function setStatus(text, type) {
        var bar = document.getElementById('status-bar');
        if (!bar) return;
        bar.className = 'de-status-bar' + (type ? ' ' + type : '');
        document.getElementById('status-text').textContent = text;
    }

    /* ── TYPE BADGE CLASS ─────────────────────────────────────── */
    function typeBadgeClass(dtype) {
        var map = {
            'switch': 'de-type-switch',
            'thermo': 'de-type-thermo',
            'pump':   'de-type-pump',
            'multi_switch': 'de-type-multi',
            'meter':       'de-type-meter'
        };
        return map[dtype] || 'de-type-other';
    }

    /* ── RENDER SINGLE DEVICE CARD ────────────────────────────── */
    function renderDevice(dev, idx) {
        var card = el('div', 'de-device-card');
        card.dataset.idx = idx;

        /* ── HEADER ── */
        var hdr = el('div', 'de-device-header');

        var badge = el('span', 'de-device-type-badge ' + typeBadgeClass(dev.device_type),
            dev.device_type || 'switch');
        hdr.appendChild(badge);

        var nameLabel = el('span', 'de-device-name-label', dev.name || '(unnamed)');
        hdr.appendChild(nameLabel);

        var idLabel = el('span', 'de-device-id-label', 'id: ' + (dev.id || '?'));
        hdr.appendChild(idLabel);

        /* enabled checkbox */
        var enabledWrap = el('label', 'de-enabled-toggle');
        var enabledChk = document.createElement('input');
        enabledChk.type = 'checkbox';
        enabledChk.checked = dev.enabled !== false;
        enabledChk.addEventListener('change', function () {
            state.devices[idx].enabled = enabledChk.checked;
        });
        enabledWrap.appendChild(enabledChk);
        enabledWrap.appendChild(document.createTextNode('enabled'));
        hdr.appendChild(enabledWrap);

        var arrow = el('span', 'de-collapse-arrow', '▼');
        hdr.appendChild(arrow);

        hdr.addEventListener('click', function (e) {
            if (e.target === enabledChk) return;
            card.classList.toggle('expanded');
        });
        card.appendChild(hdr);

        /* ── BODY ── */
        var body = el('div', 'de-device-body');

        /* Basic fields grid */
        var grid1 = el('div', 'de-field-grid');

        var idInp = inp('', dev.id, 'id');
        idInp.addEventListener('input', function () { state.devices[idx].id = idInp.value; });
        grid1.appendChild(field('ID', idInp));

        var nameInp = inp('', dev.name, 'name');
        nameInp.addEventListener('input', function () {
            state.devices[idx].name = nameInp.value;
            nameLabel.textContent = nameInp.value || '(unnamed)';
        });
        grid1.appendChild(field('Name', nameInp));

        var descInp = inp('', dev.desc, 'description');
        descInp.addEventListener('input', function () { state.devices[idx].desc = descInp.value; });
        grid1.appendChild(field('Description', descInp));

        var typeInp = sel('', ['switch', 'thermo', 'pump', 'multi_switch', 'analog', 'meter'], dev.device_type);
        typeInp.addEventListener('change', function () {
            state.devices[idx].device_type = typeInp.value;
            badge.className = 'de-device-type-badge ' + typeBadgeClass(typeInp.value);
            badge.textContent = typeInp.value;
        });
        grid1.appendChild(field('Device Type', typeInp));

        var tuyaInp = inp('', dev.tuya_device_id, 'tuya_device_id');
        tuyaInp.addEventListener('input', function () { state.devices[idx].tuya_device_id = tuyaInp.value; });
        grid1.appendChild(field('Tuya Device ID', tuyaInp));

        var prioInp = inp('', dev.priority, '0', 'number');
        prioInp.addEventListener('input', function () { state.devices[idx].priority = parseFloat(prioInp.value) || 0; });
        grid1.appendChild(field('Priority', prioInp));

        body.appendChild(grid1);

        /* Electrical params */
        body.appendChild(el('div', 'de-section-divider', 'Electrical Parameters'));

        var grid2 = el('div', 'de-field-grid four-col');

        var minVInp = inp('', dev.min_volt, '0.0', 'number');
        minVInp.addEventListener('input', function () { state.devices[idx].min_volt = parseFloat(minVInp.value) || 0; });
        grid2.appendChild(field('Min Voltage (V)', minVInp));

        var maxVInp = inp('', dev.max_volt, '0.0', 'number');
        maxVInp.addEventListener('input', function () { state.devices[idx].max_volt = parseFloat(maxVInp.value) || 0; });
        grid2.appendChild(field('Max Voltage (V)', maxVInp));

        var loadInp = inp('', dev.load_in_wt, '0', 'number');
        loadInp.addEventListener('input', function () { state.devices[idx].load_in_wt = parseFloat(loadInp.value) || 0; });
        grid2.appendChild(field('Load (W)', loadInp));

        var coefInp = inp('', dev.coefficient !== undefined ? dev.coefficient : 1, '1.0', 'number');
        coefInp.addEventListener('input', function () { state.devices[idx].coefficient = parseFloat(coefInp.value) || 1; });
        grid2.appendChild(field('Coefficient', coefInp));

        body.appendChild(grid2);

        /* Control keys */
        body.appendChild(el('div', 'de-section-divider', 'Control Keys'));

        var grid3 = el('div', 'de-field-grid two-col');

        var ctrlInp = inp('', dev.control_key, 'switch_1');
        ctrlInp.addEventListener('input', function () { state.devices[idx].control_key = ctrlInp.value; });
        grid3.appendChild(field('Control Key', ctrlInp));

        var stateInp = inp('', dev.state_key, 'switch_1');
        stateInp.addEventListener('input', function () { state.devices[idx].state_key = stateInp.value; });
        grid3.appendChild(field('State Key', stateInp));

        body.appendChild(grid3);

        /* Multi-switch children */
        if (dev.device_type === 'multi_switch' && dev.switches) {
            body.appendChild(el('div', 'de-section-divider', 'Switches (channels)'));
            var swContainer = el('div', 'de-switches-container');
            Object.keys(dev.switches).forEach(function (swKey) {
                swContainer.appendChild(renderSwitchRow(dev, idx, swKey));
            });
            body.appendChild(swContainer);
        }

        /* Actions */
        var actions = el('div', 'de-device-actions');
        var delBtn = el('button', 'de-btn de-btn-danger de-btn-sm', '✕ Remove');
        delBtn.addEventListener('click', function () {
            if (confirm('Remove device "' + (dev.name || dev.id) + '"?')) {
                state.devices.splice(idx, 1);
                renderAll();
            }
        });
        actions.appendChild(delBtn);
        body.appendChild(actions);

        card.appendChild(body);
        return card;
    }

    /* ── RENDER SWITCH ROW (multi_switch child) ───────────────── */
    function renderSwitchRow(dev, devIdx, swKey) {
        var sw = dev.switches[swKey];
        var row = el('div', 'de-switch-row');

        var rowHdr = el('div', 'de-switch-row-header');
        rowHdr.appendChild(el('span', 'de-switch-key-label', swKey));
        row.appendChild(rowHdr);

        var grid = el('div', 'de-field-grid four-col');

        var nameInp = inp('', sw.name, 'name');
        nameInp.addEventListener('input', function () {
            state.devices[devIdx].switches[swKey].name = nameInp.value;
        });
        grid.appendChild(field('Name', nameInp));

        var descInp = inp('', sw.desc, 'description');
        descInp.addEventListener('input', function () {
            state.devices[devIdx].switches[swKey].desc = descInp.value;
        });
        grid.appendChild(field('Description', descInp));

        var loadInp = inp('', sw.load_in_wt, '0', 'number');
        loadInp.addEventListener('input', function () {
            state.devices[devIdx].switches[swKey].load_in_wt = parseFloat(loadInp.value) || 0;
        });
        grid.appendChild(field('Load (W)', loadInp));

        var prioInp = inp('', sw.priority, '0', 'number');
        prioInp.addEventListener('input', function () {
            state.devices[devIdx].switches[swKey].priority = parseFloat(prioInp.value) || 0;
        });
        grid.appendChild(field('Priority', prioInp));

        var minVInp = inp('', sw.min_volt, '0.0', 'number');
        minVInp.addEventListener('input', function () {
            state.devices[devIdx].switches[swKey].min_volt = parseFloat(minVInp.value) || 0;
        });
        grid.appendChild(field('Min V', minVInp));

        var maxVInp = inp('', sw.max_volt, '0.0', 'number');
        maxVInp.addEventListener('input', function () {
            state.devices[devIdx].switches[swKey].max_volt = parseFloat(maxVInp.value) || 0;
        });
        grid.appendChild(field('Max V', maxVInp));

        var delayInp = inp('', sw.time_delay, '10', 'number');
        delayInp.addEventListener('input', function () {
            state.devices[devIdx].switches[swKey].time_delay = parseInt(delayInp.value) || 10;
        });
        grid.appendChild(field('Time Delay (s)', delayInp));

        var availInp = document.createElement('input');
        availInp.type = 'checkbox';
        availInp.checked = sw.available !== false;
        availInp.addEventListener('change', function () {
            state.devices[devIdx].switches[swKey].available = availInp.checked;
        });
        var availWrap = el('div', 'de-field');
        availWrap.appendChild(el('label', 'de-field-label', 'Available'));
        var chkWrap = el('div', 'de-enabled-toggle');
        chkWrap.appendChild(availInp);
        chkWrap.appendChild(document.createTextNode('enabled'));
        availWrap.appendChild(chkWrap);
        grid.appendChild(availWrap);

        row.appendChild(grid);
        return row;
    }

    /* ── RENDER ALL ───────────────────────────────────────────── */
    function renderAll() {
        var container = document.getElementById('devices-container');
        if (!container) return;
        container.textContent = '';
        state.devices.forEach(function (dev, idx) {
            container.appendChild(renderDevice(dev, idx));
        });
    }

    /* ── LOAD FROM API ────────────────────────────────────────── */
    function loadDevices() {
        setStatus('Loading devices from server...', '');
        fetch('/api/devices', {
            method: 'GET',
            credentials: 'same-origin',
            headers: { 'Accept': 'application/json' }
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.devices) {
                state.devices = JSON.parse(JSON.stringify(data.devices));
                renderAll();
                setStatus('Loaded ' + state.devices.length + ' devices from devices.yaml', 'ok');
            } else {
                setStatus('Error: ' + (data.detail || 'unknown'), 'error');
            }
        })
        .catch(function (e) {
            setStatus('Network error: ' + e.message, 'error');
        });
    }

    /* ── SAVE TO API ──────────────────────────────────────────── */
    function saveDevices() {
        var csrf = document.getElementById('csrf-token');
        if (!csrf) { setStatus('CSRF token missing', 'error'); return; }

        setStatus('Saving and applying...', 'saving');

        fetch('/api/devices', {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            },
            body: JSON.stringify({
                csrf_token: csrf.textContent.trim(),
                devices: state.devices
            })
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.ok) {
                var msg = 'Saved and applied — ' + data.added + ' devices loaded';
                if (data.errors && data.errors.length > 0) {
                    msg += ' (' + data.errors.length + ' errors: ' + data.errors.join('; ') + ')';
                    setStatus(msg, 'error');
                } else {
                    setStatus(msg, 'ok');
                }
            } else {
                setStatus('Error: ' + (data.detail || 'unknown'), 'error');
            }
        })
        .catch(function (e) {
            setStatus('Network error: ' + e.message, 'error');
        });
    }

    /* ── ADD BLANK DEVICE ─────────────────────────────────────── */
    function addDevice() {
        state.devices.push({
            id: String(Date.now()),
            name: 'new_device',
            desc: '',
            tuya_device_id: '',
            device_type: 'switch',
            control_key: 'switch_1',
            state_key: 'switch_1',
            min_volt: 0,
            max_volt: 30,
            load_in_wt: 0,
            priority: 10,
            coefficient: 1,
            enabled: true,
            available: true
        });
        renderAll();
        /* Scroll to last card and expand it */
        var cards = document.querySelectorAll('.de-device-card');
        var last = cards[cards.length - 1];
        if (last) {
            last.classList.add('expanded');
            last.scrollIntoView({ behavior: 'smooth' });
        }
    }

    /* ── INIT ─────────────────────────────────────────────────── */
    function init() {
        var btnSave   = document.getElementById('btn-save');
        var btnReload = document.getElementById('btn-reload');
        var btnAdd    = document.getElementById('btn-add-device');

        if (btnSave)   btnSave.addEventListener('click', saveDevices);
        if (btnReload) btnReload.addEventListener('click', loadDevices);
        if (btnAdd)    btnAdd.addEventListener('click', addDevice);

        loadDevices();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
}());
