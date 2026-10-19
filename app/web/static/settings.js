/* Charging policy editor on the existing /settings page. */
(function () {
    'use strict';
    var form = document.getElementById('charge-policy-form');
    if (!form) return;
    var fields = document.getElementById('charge-policy-fields');
    var feedback = document.getElementById('charge-policy-feedback');
    var statusError = document.getElementById('charge-policy-status-error');
    var endpoint = '/api/settings/charge-policy';
    var loaded = false;

    function populate(policy) {
        Array.from(form.elements).forEach(function (input) {
            if (!input.name || input.name === 'csrf_token') return;
            var value = input.name.split('.').reduce(function (obj, key) { return obj[key]; }, policy);
            if (input.type === 'checkbox') input.checked = value;
            else input.value = value;
        });
    }
    function readPolicy() {
        var policy = {};
        Array.from(form.elements).forEach(function (input) {
            if (!input.name || input.name === 'csrf_token') return;
            var path = input.name.split('.');
            var target = policy;
            var key = path.pop();
            path.forEach(function (part) { target = target[part] || (target[part] = {}); });
            target[key] = input.type === 'checkbox' ? input.checked :
                input.type === 'number' || input.name === 'grid_charge.current_a' ? Number(input.value) : input.value;
        });
        return policy;
    }
    function showStatus(status) {
        var body = document.getElementById('charge-policy-status');
        body.replaceChildren();
        function number(value, suffix) { return value == null ? 'Unavailable' : value + suffix; }
        function mode(value) { return value ? value.replace(/_/g, ' ') : 'Unavailable'; }
        var rows = {
            'State': status.state ? mode(status.state).toUpperCase() : 'Waiting for controller',
            'Battery': number(status.battery_voltage, ' V'),
            'PV': number(status.pv_power_w, ' W'),
            'Daylight': number(status.daylight_hours, ' h'),
            'Long-night mode': status.long_night == null ? 'Unavailable' : status.long_night ? 'Active' : 'Inactive',
            'Window': status.window_start ? status.window_start + '–' + status.window_end + (status.window_active ? ' — active' : ' — inactive') : 'Unavailable',
            'Charger requested': mode(status.desired_charger_mode),
            'Charger observed': mode(status.observed_charger_mode),
            'Reason': status.reason || 'No controller observation yet',
            'Seasonal eligibility': status.seasonal_reason || 'Unavailable',
            'Target': number(status.target_voltage_v, ' V')
        };
        Object.keys(rows).forEach(function (label) {
            var row = document.createElement('tr');
            [label, rows[label]].forEach(function (text) {
                var cell = document.createElement('td');
                cell.textContent = text;
                row.appendChild(cell);
            });
            body.appendChild(row);
        });
        statusError.textContent = [status.policy_error, status.command_error, status.observation_reason].filter(Boolean).join(' ');
    }
    async function request(options) {
        var response = await fetch(endpoint, Object.assign({credentials: 'same-origin', cache: 'no-store'}, options));
        var data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Unable to access charging policy');
        return data;
    }
    async function refresh() {
        try {
            var data = await request();
            if (!loaded) {
                populate(data.policy);
                fields.disabled = false;
                loaded = true;
                feedback.textContent = '';
            }
            // Polling updates status only, preserving unsaved form edits.
            showStatus(data.status || {});
        } catch (error) {
            statusError.textContent = 'Status unavailable: ' + error.message;
            if (!loaded) feedback.textContent = error.message;
        }
    }
    form.addEventListener('submit', async function (event) {
        event.preventDefault();
        if (!loaded || !form.reportValidity()) return;
        var policy = readPolicy();
        fields.disabled = true;
        feedback.textContent = 'Saving…';
        try {
            var data = await request({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
                csrf_token: form.elements.csrf_token.value, policy: policy
            })});
            populate(data.policy);
            showStatus(data.status || {});
            feedback.textContent = 'Saved. Policy takes effect on the next inverter update (normally within 60 seconds).';
        } catch (error) {
            feedback.textContent = error.message;
        } finally {
            fields.disabled = false;
        }
    });
    refresh();
    setInterval(refresh, 60000);
}());
