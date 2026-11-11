'use strict';
// Runs the real adaptive_decisions.js against v1 and v2 API payloads.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '..', 'app/web/static/adaptive_decisions.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
const IDS = ['decision-status', 'decision-body', 'decision-export', 'decision-load-more', 'battery-grid-status',
    'battery-observation-body', 'battery-behavior-body', 'battery-load-more', 'worker-status', 'worker-last-evaluation',
    'worker-last-success', 'worker-reason', 'worker-window', 'worker-evidence', 'worker-current', 'worker-baseline',
    'worker-proposed', 'worker-stability', 'worker-confidence', 'worker-monitored-threshold', 'worker-battery-response',
    'journal-mode', 'journal-repetition', 'journal-workers', 'journal-events-body'];
function element() {
    return {children: [], dataset: {}, textContent: '', href: '', appendChild(child) {this.children.push(child);},
        replaceChildren() {this.children = [];}, addEventListener(name, fn) {this[name] = fn;}};
}
async function render(payload) {
    const elements = Object.fromEntries(IDS.map(id => [id, element()]));
    let start;
    const document = {getElementById: id => elements[id], createElement: element,
        querySelectorAll: () => [], addEventListener: (_, fn) => {start = fn;}};
    const fetch = async url => ({ok: true, json: async () => url.includes('/battery-observations')
        ? {observations: [], battery_behavior: [], has_more: false} : JSON.parse(JSON.stringify(payload))});
    vm.runInNewContext(source, {document, fetch, JSON, String, Number});
    start(); await tick(); await tick();
    return elements;
}
const V2 = {
    decisions: [{evaluation_id: 'v1-old', evaluated_at: '2026-11-10T09:00:00+00:00', decision: 'HOLD'}],
    has_more: false, next_cursor: null,
    latest_decision: {evaluated_at: '2026-11-10T12:00:00+00:00', decision: 'HOLD', stability: 'NO_CANDIDATE',
        journal_version: 'v2', state_revision: 7, battery_v: 26.4, battery_v_status: 'VALID', battery_v_reported_raw: 26.4},
    worker_health: {enabled: true, status: 'degraded', status_source: 'JOURNAL_V2_HEARTBEAT',
        last_evaluation_at: '2026-11-10T12:00:00+00:00', last_successful_evaluation_at: '2026-11-10T12:00:00+00:00',
        last_error: null, blocking_reason: 'VERIFIED_OBSERVATION_SOURCE_UNAVAILABLE', worker_count: 2, failing_workers: 1},
    journal: {mode: 'v2', v2_installed: true, changed_at: '2026-11-10T10:00:00+00:00',
        history_note: 'Decision history below is the Journal v1 record up to the v2 cutover.',
        state: {revision: 7, initialized_from: 'V1:abc', last_evaluated_at: '2026-11-10T12:00:00+00:00'},
        current_repetition: {outcome: {decision: 'HOLD', stability: 'NO_CANDIDATE'}, evaluation_count: 1000,
            first_seen_at: '2026-11-10T10:00:01+00:00', last_seen_at: '2026-11-10T12:00:00+00:00',
            new_evidence_count: 12, repeated_evidence_count: 988},
        workers: [{worker_id: 'pod-a', status: 'healthy', last_heartbeat_at: '2026-11-10T12:00:00+00:00'},
            {worker_id: 'pod-b', status: 'failing', last_heartbeat_at: '2026-11-10T11:59:00+00:00',
             consecutive_failures: 3, last_error: 'RuntimeError'}],
        events: [{event_time: '2026-11-10T11:00:00+00:00', event_type: 'WORKER_FAILED', payload: {worker_id: 'pod-b'}},
            {event_time: '2026-11-10T10:00:00+00:00', event_type: 'JOURNAL_V2_CUTOVER',
             payload: {initialized_from: 'V1:abc', note: 'x'.repeat(400)}}]},
};

test('v2 journal: mode, repetition counters, workers and events are rendered', async () => {
    const elements = await render(V2);
    assert.match(elements['journal-mode'].textContent, /^Journal v2 active since 2026-11-10T10:00:00\+00:00; state revision 7/);
    assert.match(elements['journal-mode'].textContent, /initialised from V1:abc/);
    assert.equal(elements['journal-repetition'].textContent, 'HOLD / NO_CANDIDATE unchanged for 1000 evaluations ' +
        '(first 2026-11-10T10:00:01+00:00, last 2026-11-10T12:00:00+00:00); new evidence 12, repeated or cached evidence 988');
    assert.match(elements['journal-workers'].textContent, /pod-a: HEALTHY/);
    assert.match(elements['journal-workers'].textContent, /pod-b: FAILING.*3 consecutive failures \(RuntimeError\)/);
    const rows = elements['journal-events-body'].children;
    assert.equal(rows.length, 2);
    assert.deepEqual(rows[0].children.slice(0, 2).map(c => c.textContent), ['2026-11-10T11:00:00+00:00', 'WORKER_FAILED']);
    assert.ok(rows[1].children[2].textContent.length <= 300, 'long payloads are bounded');
    // Latest panel shows the v2 evaluation; history keeps the v1 row.
    assert.equal(elements['worker-last-evaluation'].textContent, '2026-11-10T12:00:00+00:00');
    assert.equal(elements['decision-body'].children.length, 1);
});

test('v2 journal with no evaluation yet never falls back to a v1 latest entry', async () => {
    const payload = JSON.parse(JSON.stringify(V2));
    payload.latest_decision = null;
    payload.journal.current_repetition = null;
    payload.journal.events = [];
    payload.worker_health = {enabled: true, status: 'degraded', blocking_reason: 'NO_WORKER_HEARTBEAT'};
    const elements = await render(payload);
    assert.equal(elements['journal-repetition'].textContent, 'No Journal v2 evaluation yet');
    assert.equal(elements['worker-battery-response'].textContent, 'Not recorded');
    assert.equal(elements['worker-reason'].textContent, 'NO_WORKER_HEARTBEAT');
    assert.equal(elements['journal-events-body'].children[0].children[1].textContent, 'No significant events yet');
});

test('v1 journal payload (no journal key, or installed but inactive) is labelled v1', async () => {
    let elements = await render({decisions: [], has_more: false, latest_decision: null, worker_health: {}});
    assert.equal(elements['journal-mode'].textContent, 'Journal v1: every evaluation is stored as a full entry');
    elements = await render({decisions: [], has_more: false, latest_decision: null, worker_health: {},
        journal: {mode: 'v1', v2_installed: true}});
    assert.match(elements['journal-mode'].textContent, /Journal v2 installed but not active/);
    assert.equal(elements['journal-events-body'].children.length, 0);
});
