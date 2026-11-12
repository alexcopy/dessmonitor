'use strict';
// Renders the real adaptive_decisions.js against the shared Python/Node contract
// (tests/fixtures/decision_log_battery_voltage_cases.json): latest evaluation,
// historical rows and expanded details must never show an invalid voltage as a
// measurement, for API-normalised items and for raw legacy items alike.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'app/web/static/adaptive_decisions.js'), 'utf8');
const {cases} = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/decision_log_battery_voltage_cases.json'), 'utf8'));
const tick = () => new Promise(resolve => setImmediate(resolve));
const IDS = ['decision-status', 'decision-body', 'decision-export', 'decision-load-more', 'battery-grid-status',
    'battery-observation-body', 'battery-behavior-body', 'battery-load-more', 'worker-status', 'worker-last-evaluation',
    'worker-last-success', 'worker-reason', 'worker-window', 'worker-evidence', 'worker-current', 'worker-baseline',
    'worker-proposed', 'worker-stability', 'worker-confidence', 'worker-monitored-threshold', 'worker-fast-context',
    'worker-battery-response', 'worker-daily-review', 'worker-virtual-thresholds', 'worker-adaptive-hysteresis',
    'worker-telemetry-provenance', 'worker-charging-context'];
function element() {
    return {children: [], dataset: {}, textContent: '', href: '', appendChild(child) {this.children.push(child);},
        replaceChildren() {this.children = [];}, addEventListener(name, fn) {this[name] = fn;}};
}
function entry(fields, minute) {
    return Object.assign({evaluation_id: 'id-' + minute, evaluated_at: '2026-11-10T10:0' + minute + ':00+00:00',
        decision: 'HOLD', evidence_level: 'EXPLORATORY', reason_codes: ['INVALID_BATTERY_VOLTAGE'],
        charge_a: 0, discharge_a: 11, explanation: 'synthetic',
        battery_response: {capacity_status: 'CAPACITY_NOT_IDENTIFIABLE', throughput_status: 'NONE',
            charged_ah_proxy: null, discharged_ah_proxy: null, latest_voltage_slope_v_per_hour: null,
            freshness: 'RECENT', confidence: 'LOW', recent_intervals: []}}, fields);
}
async function render(latest, decisions, observations) {
    const elements = Object.fromEntries(IDS.map(id => [id, element()]));
    let start;
    const document = {getElementById: id => elements[id], createElement: element,
        querySelectorAll: () => [], addEventListener: (_, fn) => {start = fn;}};
    // Round-trip through JSON text exactly as the browser receives the API response.
    const wire = value => JSON.parse(JSON.stringify(value));
    const fetch = async url => ({ok: true, json: async () => url.includes('/battery-observations')
        ? wire({observations: observations || [], battery_behavior: [], has_more: false})
        : wire({latest_decision: latest, worker_health: {enabled: true, status: 'ok'}, decisions, has_more: false})});
    vm.runInNewContext(source, {document, fetch, JSON, String, Number});
    start(); await tick(); await tick();
    return elements;
}
function expandedDetail(row) {
    const detail = row.children[3].children[0]; detail.open = true; detail.toggle();
    return detail.children[1].textContent;
}
function assertRendered(elements, expected, label) {
    assert.ok(elements['worker-battery-response'].textContent.includes('; battery ' + expected + '; '),
        label + ' latest: ' + elements['worker-battery-response'].textContent);
    const row = elements['decision-body'].children[0];
    assert.ok(row.children[2].textContent.includes('battery ' + expected), label + ' row: ' + row.children[2].textContent);
    assert.ok(expandedDetail(row).startsWith('Latest battery voltage: ' + expected + '\n'), label + ' detail');
    if (expected.startsWith('Invalid')) {
        for (const text of [elements['worker-battery-response'].textContent, row.children[2].textContent]) {
            assert.doesNotMatch(text, /(battery|Battery|V) -?\d+(\.\d+)? V;/, label + ' shows a bare voltage: ' + text);
        }
    }
}

test('reviewer example 26.0 V -> 0.0 V is shown as invalid, with raw value and reason', async () => {
    const reviewer = cases.find(c => c.name === 'reviewer_zero_after_26_0');
    const item = entry(reviewer.api, 1);
    const elements = await render(item, [item]);
    assert.equal(reviewer.ui, 'Invalid (reported 0.0 V; INVALID_ZERO_VOLTAGE; excluded from calculations)');
    assertRendered(elements, reviewer.ui, 'reviewer');
    assert.doesNotMatch(elements['worker-battery-response'].textContent, /battery 0 V/);
    const detail = expandedDetail(elements['decision-body'].children[0]);
    assert.match(detail, /"battery_v": null/);
    assert.match(detail, /"battery_v_reported_raw": 0/);
    assert.match(detail, /"battery_v_status": "INVALID_ZERO_VOLTAGE"/);
});

for (const c of cases) {
    test('API item renders per contract: ' + c.name, async () => {
        const item = entry(c.api, 1);
        assertRendered(await render(item, [item]), c.ui, c.name);
    });
    test('stored item without API normalisation renders identically: ' + c.name, async () => {
        const item = entry(c.stored, 2);
        assertRendered(await render(item, [item]), c.ui, c.name);
    });
}

test('historical rows each keep their own validity', async () => {
    const byName = Object.fromEntries(cases.map(c => [c.name, c]));
    const names = ['valid_25_5', 'reviewer_zero_after_26_0', 'legacy_zero', 'missing', 'negative', 'nan'];
    const rows = names.map((name, i) => entry(byName[name].api, i));
    const elements = await render(rows[0], rows);
    names.forEach((name, i) => {
        assert.ok(elements['decision-body'].children[i].children[2].textContent.includes('battery ' + byName[name].ui), name);
    });
    assert.ok(elements['worker-battery-response'].textContent.includes('; battery 25.5 V; '));
});

test('battery observation grid marks invalid raw voltage instead of a blank or value', async () => {
    const base = {collection_time: '2026-11-10T10:01:00+00:00', mode: 'Invert Mode', missing_fields: []};
    const elements = await render(null, [], [
        Object.assign({}, base, {battery_voltage_v: null, battery_voltage_v_reported_raw: 0.0, invalid_fields: ['battery_voltage']}),
        Object.assign({}, base, {collection_time: '2026-11-10T10:00:00+00:00', battery_voltage_v: 26.0,
            battery_voltage_v_reported_raw: 26.0, invalid_fields: []}),
        Object.assign({}, base, {collection_time: '2026-11-10T09:59:00+00:00', battery_voltage_v: null,
            battery_voltage_v_reported_raw: null, invalid_fields: [], missing_fields: ['battery_voltage']})]);
    const rows = elements['battery-observation-body'].children;
    assert.equal(rows[0].children[3].textContent, 'Invalid (reported 0.0 V; excluded from calculations)');
    assert.equal(rows[1].children[3].textContent, '26');
    assert.equal(rows[2].children[3].textContent, '—');
});

// Fix3: the latest panel must show the entry's voltage even when the entry has no
// battery_response (legacy journal rows), and agree with the history-row formatter.
const INV = ' excluded from calculations)';
const LATEST_WITHOUT_RESPONSE = [
    ['legacy battery_v 0.0', {battery_v: 0.0}, 'Invalid (reported 0.0 V; INVALID_ZERO_VOLTAGE;' + INV],
    ['legacy battery_v 25.5', {battery_v: 25.5}, '25.5 V'],
    ['legacy battery_v -1.0', {battery_v: -1.0}, 'Invalid (reported -1.0 V; INVALID_NEGATIVE_VOLTAGE;' + INV],
    ['legacy battery_v missing', {}, 'Unavailable'],
    ['legacy battery_v null', {battery_v: null}, 'Unavailable (not reported)'],
    ['new format zero', {battery_v: null, battery_v_reported_raw: 0.0, battery_v_status: 'INVALID_ZERO_VOLTAGE',
        battery_v_validity_source: 'RECORDED_BY_WORKER'}, 'Invalid (reported 0.0 V; INVALID_ZERO_VOLTAGE;' + INV],
    ['new format valid', {battery_v: 25.5, battery_v_reported_raw: 25.5, battery_v_status: 'VALID',
        battery_v_validity_source: 'RECORDED_BY_WORKER'}, '25.5 V'],
    ['new format non-finite', {battery_v: null, battery_v_reported_raw: 'NaN', battery_v_status: 'INVALID_NON_FINITE_VOLTAGE',
        battery_v_validity_source: 'RECORDED_BY_WORKER'}, 'Invalid (reported "NaN"; INVALID_NON_FINITE_VOLTAGE;' + INV],
    ['new format missing', {battery_v: null, battery_v_reported_raw: null, battery_v_status: 'MISSING',
        battery_v_validity_source: 'RECORDED_BY_WORKER'}, 'Unavailable (not reported)'],
];
for (const [label, fields, expected] of LATEST_WITHOUT_RESPONSE) {
    test('latest panel without battery_response shows voltage: ' + label, async () => {
        const item = Object.assign({evaluation_id: 'legacy', evaluated_at: '2026-11-01T00:00:00+00:00',
            decision: 'HOLD', algorithm_version: 'online-exploratory-5'}, fields);
        assert.equal('battery_response' in item, false);
        const elements = await render(item, [item]);
        const latest = elements['worker-battery-response'].textContent;
        assert.notEqual(latest, 'Not recorded');
        assert.equal(latest, 'battery ' + expected + '; reported charge/discharge —/— A; battery response diagnostics not recorded');
        const history = elements['decision-body'].children[0].children[2].textContent;
        const historyVoltage = history.slice(history.indexOf('battery ') + 'battery '.length);
        assert.equal(historyVoltage, expected, 'history row');
        assert.equal(latest.slice('battery '.length, latest.indexOf('; reported charge/discharge')), historyVoltage,
            'latest panel and history row agree');
    });
}

test('latest panel keeps numeric zero currents and says Not recorded only without an entry', async () => {
    const item = {evaluated_at: '2026-11-01T00:00:00+00:00', decision: 'HOLD', battery_v: 0.0, charge_a: 0, discharge_a: 0};
    let elements = await render(item, [item]);
    assert.equal(elements['worker-battery-response'].textContent, 'battery Invalid (reported 0.0 V; INVALID_ZERO_VOLTAGE;' + INV +
        '; reported charge/discharge 0/0 A; battery response diagnostics not recorded');
    elements = await render(null, []);
    assert.equal(elements['worker-battery-response'].textContent, 'Not recorded');
});
