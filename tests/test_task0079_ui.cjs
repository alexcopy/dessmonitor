'use strict';
// Task 0079: runs the real dashboard.js and analytics.js against synthetic read-only payloads.
process.env.TZ = 'UTC';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const ROOT = path.join(__dirname, '..');
const read = rel => fs.readFileSync(path.join(ROOT, rel), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
const XSS = '<img src=x onerror="alert(1)">';

function makeDom() {
    const created = [];
    const innerHtmlWrites = [];
    function element(tag) {
        const el = {
            tagName: String(tag || 'div').toUpperCase(), children: [], dataset: {}, style: {}, attributes: {},
            textContent: '', className: '', title: '', colSpan: 1, listeners: {},
            classList: {set: new Set(), add(c) {this.set.add(c);}, remove(c) {this.set.delete(c);},
                toggle(c, on) {if (on === undefined ? !this.set.has(c) : on) {this.set.add(c);} else {this.set.delete(c);}},
                contains(c) {return this.set.has(c);}},
            appendChild(child) {this.children.push(child); return child;},
            removeChild(child) {this.children = this.children.filter(c => c !== child); return child;},
            get firstChild() {return this.children[0] || null;},
            setAttribute(k, v) {this.attributes[k] = String(v);},
            getAttribute(k) {return this.attributes[k];},
            addEventListener(name, fn) {this.listeners[name] = fn;},
            querySelector() {return element('div');}, querySelectorAll() {return [];},
        };
        Object.defineProperty(el, 'innerHTML', {
            get() {return '';}, set(v) {innerHtmlWrites.push({id: el.id, value: String(v)}); el.children = [];},
        });
        created.push(el);
        return el;
    }
    const byId = {};
    const document = {
        readyState: 'complete', hidden: false, listeners: {},
        getElementById(id) {if (!byId[id]) {byId[id] = element('div'); byId[id].id = id;} return byId[id];},
        createElement: element, createTextNode: text => ({textContent: String(text), children: []}),
        createElementNS: (_, tag) => element(tag),
        addEventListener(name, fn) {this.listeners[name] = fn;}, querySelector() {return null;}, querySelectorAll() {return [];},
    };
    return {document, byId, created, innerHtmlWrites};
}

function textOf(node) {
    return node.children && node.children.length ? node.children.map(textOf).join(' | ') : String(node.textContent);
}

const WINTER = {
    status: 'ok', decision: 'RECOVERY_OR_FAULT_HOLD', profile: 'WINTER', solar_opportunity: 'UNKNOWN',
    charging_context: 'CHARGING_REPORTED', charger_priority_reported: XSS, charge_current_a_reported: 4,
    reserve_evidence_quality: 'INCOMPLETE_UNVERIFIED', interlock_decision: 'BLOCK', interlock_state: 'FAULT_HOLD',
    interlock_reasons: ['CHARGER_STATE_UNVERIFIED'], reasons: ['BATTERY_RESERVE_UNVERIFIED', XSS],
    missing_requirements: ['independently verified BMS/shunt usable reserve'], post_recovery_deadlock: true,
    freshness: 'RECEIPT_CURRENT_MEASUREMENT_UNVERIFIED', received_at: '2026-12-10T12:00:00+00:00',
    receipt_age_seconds: 42, measurement_time: null, timestamp_quality: 'device_time_unverified',
    last_successful_evaluation: '2026-12-10T12:00:00+00:00', last_failure_at: null, diagnostic_failure: null,
    failure_count: 0, persistence_status: 'degraded', shadow_only: true, command_issued: false,
    telemetry: {responses: 10, repeated_responses: 7, changed_payloads: 3, mode_transitions_observed: 2,
        checkpoints: 4, verified_independent_observations: 0, queue_drops: 1, checkpoint_interval_seconds: 300,
        checkpoint_context: 'unknown_solar_context'},
};

function controlState(winter) {
    return {status: 'ok', read_only: true, warnings: [], snapshot: {
        created_at: '2026-12-10T12:00:00+00:00', loads: [], sensors: [], winter_diagnostics: winter,
        inverter: [{working_mode: 'Line Mode', observed_at: null, received_at: new Date().toISOString(),
            timestamp_quality: 'device_time_unverified', battery_voltage: 26.4, battery_soc: 60}],
    }};
}

async function runDashboard(payload) {
    const dom = makeDom();
    const fetch = async () => ({ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(payload))});
    const window = {addEventListener() {}, location: {href: '/'}};
    const sandbox = {document: dom.document, window, fetch, AbortController, setTimeout: () => 0, clearTimeout() {},
        console, Date, Math, JSON, Number, String, Array, Object, isNaN, parseFloat, parseInt};
    vm.runInNewContext(read('app/web/static/dashboard.js'), sandbox);
    for (let i = 0; i < 6; i++) await tick();
    return dom;
}

test('winter panel renders decision, reasons and freshness as literal text', async () => {
    const dom = await runDashboard(controlState(WINTER));
    const get = id => dom.byId[id];
    assert.equal(get('winter-decision').textContent, 'RECOVERY_OR_FAULT_HOLD');
    assert.match(get('winter-decision').className, /error/);
    assert.equal(get('winter-freshness').textContent, 'receipt current · measurement unverified');
    assert.equal(get('winter-reserve').textContent, 'INCOMPLETE_UNVERIFIED');
    assert.match(get('winter-interlock').textContent, /BLOCK \/ FAULT_HOLD .*post-recovery deadlock/);
    assert.match(get('winter-measurement').textContent, /^unverified/);
    assert.equal(get('winter-persistence').textContent, 'degraded');
    assert.match(get('winter-telemetry').textContent, /repeats 7 .*verified 0 .*drops 1/);
    assert.match(get('winter-charging').textContent, /CHARGING_REPORTED/);
    const reasons = get('winter-reasons').children.map(li => li.textContent);
    assert.deepEqual(reasons, ['BATTERY_RESERVE_UNVERIFIED', XSS]);  // literal text, not markup
    assert.ok(get('winter-charging').textContent.includes(XSS));
    const winterIds = Object.keys(dom.byId).filter(id => id.startsWith('winter-'));
    assert.deepEqual(dom.innerHtmlWrites.filter(w => winterIds.includes(w.id)), []);
    assert.ok(!dom.created.some(el => ['BUTTON', 'A', 'INPUT', 'FORM'].includes(el.tagName)
        && winterIds.some(id => dom.byId[id].children.includes(el))));
});

test('missing winter block renders explicit unavailable state, never stale current data', async () => {
    const dom = await runDashboard(controlState(null));
    assert.equal(dom.byId['winter-decision'].textContent, 'UNAVAILABLE');
    assert.equal(dom.byId['winter-freshness'].textContent, 'unavailable');
    assert.equal(dom.byId['winter-solar'].textContent, 'unavailable');
    assert.deepEqual(dom.byId['winter-reasons'].children.map(li => li.textContent), ['WINTER_DIAGNOSTICS_UNAVAILABLE']);
});

// Shape produced by WinterRuntimeDiagnostics after: success with 2 A charging,
// a failed evaluation, and a newer DESS response (Task 0079 blocker 3).
const WINTER_FAILED = Object.assign({}, WINTER, {
    status: 'evaluation_failed', decision: 'UNAVAILABLE', profile: 'UNAVAILABLE', solar_opportunity: 'UNAVAILABLE',
    charging_context: 'UNAVAILABLE', charger_priority_reported: null, charge_current_a_reported: null,
    reserve_evidence_quality: 'UNAVAILABLE_UNVERIFIED', interlock_decision: 'UNAVAILABLE',
    interlock_state: 'UNAVAILABLE', interlock_reasons: [], post_recovery_deadlock: false,
    reasons: ['DIAGNOSTIC_EVALUATION_FAILED', 'RUNTIME_TRUSTED_RESERVE_INTEGRATION_ABSENT'],
    freshness: 'UNAVAILABLE', received_at: null, receipt_age_seconds: null,
    last_successful_evaluation: '2026-12-10T12:00:00+00:00', last_failure_at: '2026-12-10T12:02:00+00:00',
    diagnostic_failure: 'RuntimeError', failure_count: 2,
    historical_last_success: {label: 'HISTORICAL_NOT_CURRENT', decision: 'RECOVERY_OR_FAULT_HOLD',
        charging_context: 'CHARGING_REPORTED', charge_current_a_reported: 2, charger_priority_reported: 'Solar only',
        received_at: '2026-12-10T12:00:00+00:00', last_successful_evaluation: '2026-12-10T12:00:00+00:00'},
});

const CURRENT_FIELDS = ['winter-decision', 'winter-freshness', 'winter-solar', 'winter-charging', 'winter-reserve',
    'winter-interlock', 'winter-receipt'];

test('failed evaluation never renders historical charging values as current', async () => {
    const dom = await runDashboard(controlState(WINTER_FAILED));
    const get = id => dom.byId[id].textContent;
    assert.equal(get('winter-decision'), 'UNAVAILABLE');
    assert.equal(get('winter-freshness'), 'evaluation failed');
    assert.equal(get('winter-charging'), 'unavailable (evaluation failed)');
    assert.equal(get('winter-receipt'), 'unavailable (evaluation failed)');
    for (const id of CURRENT_FIELDS) {
        assert.doesNotMatch(get(id), /CHARGING_REPORTED|\b2 A\b|receipt current/, id);
    }
    assert.match(get('winter-failure'), /RuntimeError · 2 total · last successful result RECOVERY_OR_FAULT_HOLD at .*\(historical, not current\)/);
    assert.match(get('winter-last-eval'), /last successful; latest evaluation failed/);
    assert.deepEqual(dom.byId['winter-reasons'].children.map(li => li.textContent),
        ['DIAGNOSTIC_EVALUATION_FAILED', 'RUNTIME_TRUSTED_RESERVE_INTEGRATION_ABSENT']);
});

test('evaluation_failed payload carrying old values is still rendered unavailable', async () => {
    // The pre-fix server shape: failure status but prior charging fields and current receipt freshness.
    const stale = Object.assign({}, WINTER, {status: 'evaluation_failed', decision: 'UNAVAILABLE',
        diagnostic_failure: 'RuntimeError', failure_count: 1, charge_current_a_reported: 2});
    const dom = await runDashboard(controlState(stale));
    for (const id of CURRENT_FIELDS) {
        assert.doesNotMatch(dom.byId[id].textContent, /CHARGING_REPORTED|2 A reported|receipt current|BLOCK/, id);
    }
    assert.equal(dom.byId['winter-freshness'].textContent, 'evaluation failed');
});

test('successful result with stale receipt is labelled not current', async () => {
    const dom = await runDashboard(controlState(Object.assign({}, WINTER, {freshness: 'RECEIPT_STALE'})));
    assert.equal(dom.byId['winter-freshness'].textContent, 'receipt stale');
    assert.match(dom.byId['winter-freshness'].className, /degraded/);
    assert.match(dom.byId['winter-charging'].textContent, /CHARGING_REPORTED.*as of receipt stale, not current$/);
    assert.match(dom.byId['winter-solar'].textContent, /not current$/);
});

test('inverter freshness falls back to labelled receipt time', async () => {
    const dom = await runDashboard(controlState(WINTER));
    assert.equal(dom.byId['inv-freshness'].textContent, 'receipt fresh');
    assert.match(dom.byId['inv-timestamp'].title, /receipt time; device measurement time unverified/);
});

test('markup has no controls inside the winter panel', () => {
    const html = read('app/web/templates/index.html');
    const panel = html.slice(html.indexOf('id="winter-diagnostics-panel"'), html.indexOf('<!-- BOTTOM ROW'));
    assert.ok(panel.length > 100);
    assert.doesNotMatch(panel, /<(button|a|form|input|select|textarea)\b/i);
    assert.match(panel, /cannot authorise battery use/);
});

async function runAnalytics(body, status = 200) {
    const dom = makeDom();
    const fetchCalls = [];
    const fetch = async (url, opts) => {
        fetchCalls.push({url, method: opts && opts.method});
        return {ok: status === 200, status, json: async () => JSON.parse(JSON.stringify(body))};
    };
    const sandbox = {document: dom.document, window: {}, fetch, setInterval() {}, setTimeout() {}, console,
        Date, Math, JSON, Number, String, Array, Object, isNaN, parseFloat, Chart: function () {}};
    const source = read('app/web/static/analytics.js');
    const start = source.indexOf('/* ── Telemetry Events & Provenance');
    const end = source.indexOf('/* ── Meter Daily Energy');
    assert.ok(start >= 0 && end > start);
    vm.runInNewContext(source.slice(start, end), sandbox);
    dom.document.listeners.DOMContentLoaded();
    for (let i = 0; i < 6; i++) await tick();
    return {dom, fetchCalls};
}

test('analytics telemetry section renders provenance safely via GET only', async () => {
    const events = [
        {event_kind: 'mode_transition', from_mode: 'Line Mode', to_mode: XSS, changed_fields: ['working_mode'],
            quantized_values: {}, receipt_at: '2026-12-10T01:00:00+00:00', collected_at: '2026-12-10T01:00:01+00:00',
            timestamp_quality: 'device_time_unverified', sample_continuity: 'gap', verified_observation: false},
        {event_kind: 'checkpoint', to_mode: 'Line Mode', changed_fields: [], quantized_values: {},
            payload_changed_since_last_checkpoint: false, receipt_at: '2026-12-10T00:55:00+00:00',
            collected_at: '2026-12-10T00:55:01+00:00', timestamp_quality: 'device_time_unverified',
            sample_continuity: 'checkpoint', verified_observation: false},
    ];
    const {dom, fetchCalls} = await runAnalytics({events, limit: 100, persistence_status: 'available',
        counts: {mode_transition: 1, significant_payload_change: 0, checkpoint: 1}});
    assert.deepEqual(fetchCalls, [{url: '/api/inverter/events', method: 'GET'}]);
    const rows = dom.byId.telemetryEventsBody.children.map(textOf);
    assert.equal(rows.length, 2);
    assert.ok(rows[0].includes('Observed mode change'));
    assert.ok(rows[0].includes('Line Mode → ' + XSS));
    assert.ok(rows[0].includes('gap: DESS update(s) missed'));
    assert.ok(rows[1].includes('repeated payload'));
    assert.match(dom.byId.telemetryEventsSummary.textContent, /verified device measurements 0/);
    assert.deepEqual(dom.innerHtmlWrites, []);
});

test('analytics telemetry section shows unavailable on database outage', async () => {
    const {dom} = await runAnalytics({detail: 'telemetry events unavailable', persistence_status: 'degraded'}, 500);
    assert.match(dom.byId.telemetryEventsSummary.textContent, /unavailable \(persistence degraded\)/);
    assert.equal(textOf(dom.byId.telemetryEventsBody.children[0]), 'Unavailable');
});
