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
    'journal-mode', 'journal-repetition', 'journal-workers', 'journal-events-body', 'journal-raw-events',
    'journal-raw-events-disclosure', 'decision-history-source', 'worker-summary-status',
    'worker-summary-evaluated-at', 'worker-current-decision', 'worker-summary-cycling',
    'worker-summary-evidence', 'worker-summary-current-threshold', 'worker-summary-hold'];
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
    assert.deepEqual(rows[0].children[0].children.map(c => c.textContent),
        ['2026-11-10T11:00:00+00:00', 'Worker reported an error']);
    assert.match(rows[0].children[1].textContent, /reported a failure/);
    assert.equal(rows[0].children[2].children[0].textContent, 'Original event payload');
    rows[0].children[2].open = true; rows[0].children[2].toggle();
    assert.match(rows[0].children[2].children[1].textContent, /worker_id/);
    elements['journal-raw-events-disclosure'].open = true;
    elements['journal-raw-events-disclosure'].toggle();
    assert.equal(elements['journal-raw-events'].children.length, 2);
    assert.match(elements['journal-raw-events'].children[1].children[0].children[1].textContent, /initialized_from/);
    assert.ok(elements['journal-raw-events'].children[1].children[0].children[1].textContent.length > 300,
        'the complete event payload remains available in Technical details');
    // Latest panel shows the v2 evaluation; history keeps the v1 row.
    assert.equal(elements['worker-last-evaluation'].textContent, '2026-11-10T12:00:00+00:00');
    assert.equal(elements['decision-body'].children.length, 1);
    assert.equal(elements['worker-current-decision'].textContent,'HOLD — no threshold change recommended');
    assert.equal(elements['worker-summary-evaluated-at'].textContent,'2026-11-10T12:00:00+00:00');
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
    assert.equal(elements['journal-events-body'].children[0].textContent, 'No significant events yet.');
    assert.equal(elements['worker-current-decision'].textContent,'No Journal v2 evaluation recorded');
    assert.match(elements['worker-summary-hold'].textContent,/historical Journal v1 records are not used/);
    assert.equal(elements['decision-body'].children[0].children[1].textContent,'HOLD',
        'the v1 historical row remains in History without becoming the current decision');
});

test('timeline shows recent events first and keeps older events and raw payloads expandable', async () => {
    const payload = JSON.parse(JSON.stringify(V2));
    payload.journal.events = Array.from({length: 10}, (_, index) => ({
        event_time: '2026-11-10T' + String(9 - index).padStart(2, '0') + ':00:00+00:00',
        event_type: 'WORKER_RECOVERED', worker_id: 'worker-' + index, payload: {revision: index}
    }));
    const elements = await render(payload);
    elements['journal-raw-events-disclosure'].open = true;
    elements['journal-raw-events-disclosure'].toggle();
    const timeline = elements['journal-events-body'].children;
    assert.equal(timeline.length, 9, 'eight recent events plus one closed earlier-events disclosure');
    assert.match(timeline[0].children[0].children[0].textContent, /09:00:00/);
    assert.match(timeline[7].children[0].children[0].textContent, /02:00:00/);
    const earlier = timeline[8].children[0];
    assert.equal(earlier.open, undefined, 'older event list is collapsed by default');
    earlier.open = true; earlier.toggle();
    assert.equal(earlier.children[1].children.length,2,'older event records render only after expansion');
    assert.equal(timeline[0].children[2].children[0].textContent,'Original event payload');
    assert.equal(elements['journal-raw-events'].children.length, 10,
        'all raw Journal v2 event objects remain accessible under Technical details');
});

test('v1 journal payload (no journal key, or installed but inactive) is labelled v1', async () => {
    let elements = await render({decisions: [], has_more: false, latest_decision: null, worker_health: {}});
    assert.equal(elements['journal-mode'].textContent, 'Journal v1: every evaluation is stored as a full entry');
    elements = await render({decisions: [], has_more: false, latest_decision: null, worker_health: {},
        journal: {mode: 'v1', v2_installed: true}});
    assert.match(elements['journal-mode'].textContent, /Journal v2 installed but not active/);
    assert.match(elements['journal-events-body'].children[0].textContent, /separate event timeline is unavailable/);
});
