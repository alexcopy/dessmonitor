'use strict';
// Runs the real adaptive_decisions.js against v1 and v2 API payloads.
process.env.TZ = 'UTC';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '..', 'app/web/static/adaptive_decisions.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
function element() {
    return {children: [], dataset: {}, textContent: '', href: '', appendChild(child) {this.children.push(child);},
        replaceChildren() {this.children = [];}, addEventListener(name, fn) {this[name] = fn;}};
}
function textOf(node) {
    return node.children && node.children.length ? node.children.map(textOf).join(' ') : String(node.textContent);
}
function rows(list) {
    return Object.fromEntries(list.children.map(row => [row.children[0].textContent, textOf(row.children[1])]));
}
async function render(payload) {
    const elements = {};
    const get = id => id === 'decision-history' ? undefined : elements[id] || (elements[id] = element());
    let start;
    const document = {getElementById: get, createElement: element, querySelectorAll: () => [],
        addEventListener: (_, fn) => {start = fn;}};
    const fetch = async url => ({ok: true, json: async () => url.includes('/battery-observations')
        ? {observations: [], battery_behavior: [], has_more: false} : JSON.parse(JSON.stringify(payload))});
    vm.runInNewContext(source, {document, fetch, JSON, String, Number});
    start(); await tick(); await tick();
    return get;
}
const V2 = {
    decisions: [{evaluation_id: 'v1-old', evaluated_at: '2026-11-10T09:00:00+00:00', decision: 'HOLD'}],
    has_more: false, next_cursor: null,
    latest_decision: {evaluated_at: '2026-11-10T12:00:00+00:00', decision: 'HOLD', stability: 'NO_CANDIDATE',
        journal_version: 'v2', state_revision: 7, battery_v: 26.4, battery_v_status: 'VALID', battery_v_reported_raw: 26.4,
        snapshot_evaluated_at: '2026-11-10T11:00:00+00:00', last_evaluated_at: '2026-11-10T12:00:00+00:00',
        evaluations_since_snapshot: 60},
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
const copy = value => JSON.parse(JSON.stringify(value));

test('v2 journal: mode, repetition counters, workers and events are rendered', async () => {
    const get = await render(V2);
    const journal = rows(get('diag-journal'));
    assert.equal(journal['Mode'], 'Journal v2 active — repeated outcomes are aggregated');
    assert.match(journal['Active since'], /10 Nov( 2026)? 10:00/);
    assert.equal(journal['State revision'], '7');
    assert.equal(journal['Initialised from'], 'V1:abc');
    assert.equal(journal['Current outcome'], 'HOLD · no candidate');
    assert.match(journal['Unchanged for'], /^1000 evaluations \(10 Nov( 2026)? 10:00 – 10 Nov( 2026)? 12:00\)$/);
    assert.equal(journal['Evidence'], '12 new · 988 repeated or cached');
    assert.match(journal['Identical since snapshot'], /^60 evaluations/);
    const worker = rows(get('diag-worker'));
    assert.match(worker['Worker pod-a'], /^Healthy/);
    assert.match(worker['Worker pod-b'], /^Failing.*3 consecutive failures \(RuntimeError\)/);
    assert.equal(get('worker-summary-status').textContent, '1 of 2 failing');
    const timeline = get('journal-events-body').children;
    assert.equal(timeline.length, 2);
    assert.equal(timeline[0].children[0].dateTime, '2026-11-10T11:00:00+00:00');
    assert.match(timeline[0].children[0].textContent, /11:00$/);
    assert.match(timeline[0].children[0].title, /stored as 2026-11-10T11:00:00\+00:00/, 'full time stays available');
    assert.equal(timeline[0].children[1].children[0].textContent, 'Worker reported an error');
    assert.equal(timeline[0].dataset.tone, 'bad');
    assert.match(timeline[0].children[1].children[1].textContent, /pod-b reported a failure/);
    assert.equal(get('journal-raw-events-disclosure').hidden, false);
    get('journal-raw-events-disclosure').open = true;
    get('journal-raw-events-disclosure').toggle();
    const raw = get('journal-raw-events').children;
    assert.equal(raw.length, 2);
    assert.ok(raw[1].children[0].children[2].textContent.length > 400, 'the complete event object remains available');
    assert.match(raw[1].children[0].children[2].textContent, /initialized_from/);
    // Latest panel shows the v2 evaluation; history keeps the v1 row.
    assert.equal(get('worker-current-decision').textContent, 'HOLD');
    assert.equal(get('worker-summary-evaluated-at').dateTime, '2026-11-10T12:00:00+00:00');
    assert.match(get('decision-age').textContent, /unchanged since 10 Nov( 2026)? 11:00/);
    assert.equal(get('decision-body').children.length, 1);
});

test('v2 journal with no evaluation yet never falls back to a v1 latest entry', async () => {
    const payload = copy(V2);
    payload.latest_decision = null;
    payload.journal.current_repetition = null;
    payload.journal.events = [];
    payload.journal.workers = [];
    payload.worker_health = {enabled: true, status: 'degraded', blocking_reason: 'NO_WORKER_HEARTBEAT'};
    const get = await render(payload);
    assert.equal(rows(get('diag-journal'))['Current outcome'], 'No Journal v2 evaluation yet');
    assert.equal(rows(get('diag-voltage'))['Latest battery voltage'], 'Not recorded');
    assert.match(rows(get('diag-worker'))['Error or blocking reason'], /\(NO_WORKER_HEARTBEAT\)$/);
    assert.equal(textOf(get('journal-events-body').children[0]), 'No significant events yet.');
    assert.equal(get('journal-raw-events-disclosure').hidden, true);
    assert.equal(get('worker-current-decision').textContent, 'No decision yet');
    assert.match(get('decision-consequence').textContent, /Older Journal v1 records are not used/);
    assert.equal(get('worker-summary-status').textContent, 'No heartbeat');
    assert.equal(get('decision-attention').dataset.tone, 'bad');
    assert.equal(get('decision-body').children[0].children[1].textContent, 'HOLD',
        'the v1 historical row remains in History without becoming the current decision');
});

test('a stability-only DECISION_CHANGED is not presented as a new decision', async () => {
    const payload = copy(V2);
    payload.journal.events = [
        {event_time: '2026-11-10T11:30:00+00:00', event_type: 'DECISION_CHANGED',
            payload: {from: {decision: 'HOLD', stability: 'NO_CANDIDATE'}, to: {decision: 'HOLD', stability: 'AWAITING_CONFIRMATION'}}},
        {event_time: '2026-11-10T11:00:00+00:00', event_type: 'DECISION_CHANGED',
            payload: {from: {decision: 'BLOCKED', stability: 'NO_CANDIDATE'}, to: {decision: 'HOLD', stability: 'NO_CANDIDATE'}}}];
    const get = await render(payload);
    const [stability, change] = get('journal-events-body').children.map(item => item.children[1]);
    assert.equal(stability.children[0].textContent, 'Stability changed');
    assert.equal(stability.children[1].textContent, 'Decision stayed HOLD; stability no candidate → awaiting confirmation.');
    assert.equal(change.children[0].textContent, 'Decision changed');
    assert.equal(change.children[1].textContent, 'BLOCKED → HOLD (no candidate).');
});

// ── Timeline grouping regressions (independent review finding B1) ──────────────────────────────
const at = minute => '2026-11-10T11:' + String(59 - minute).padStart(2, '0') + ':00+00:00';
const confirmation = (minute, from, to, fromPending, toPending) => ({event_time: at(minute), event_type: 'CONFIRMATION_CHANGED',
    payload: {from: {pending: fromPending === undefined ? 'HYPOTHETICAL_RAISE:26.5' : fromPending, stable_count: from},
        to: {pending: toPending === undefined ? 'HYPOTHETICAL_RAISE:26.5' : toPending, stable_count: to}}});
async function timelineFor(events) {
    const payload = copy(V2);
    payload.journal.events = events;
    const get = await render(payload);
    const entries = get('journal-events-body').children.filter(item => item.className === 'adl-event');
    return {get, entries, titles: entries.map(item => item.children[1].children[0].textContent),
        texts: entries.map(item => item.children[1].children[item.children[1].children.length - 1].textContent),
        counts: entries.map(item => item.children[1].children.length === 3 ? item.children[1].children[1].textContent : null)};
}

test('confirmation progress 1 -> 2 is described from the payload', async () => {
    const {titles, texts, entries} = await timelineFor([confirmation(0, 1, 2)]);
    assert.deepEqual(titles, ['Confirmation progress changed']);
    assert.equal(texts[0], 'Compatible observations 1 → 2; pending candidate hypothetical raise to 26.5 V.');
    assert.equal(entries[0].dataset.tone, 'info');
});

test('a confirmation reset 2 -> 0 is labelled as a reset and never merged into progress', async () => {
    const {titles, texts, counts, entries} = await timelineFor([confirmation(0, 0, 1), confirmation(1, 2, 0), confirmation(2, 1, 2)]);
    assert.deepEqual(titles, ['Confirmation progress changed', 'Confirmation reset', 'Confirmation progress changed'],
        'each distinct transition keeps its own entry, newest first');
    assert.deepEqual(counts, [null, null, null], 'no entry claims to stand for several events');
    assert.equal(texts[1], 'Compatible observations 2 → 0; pending candidate hypothetical raise to 26.5 V.');
    assert.equal(entries[1].dataset.tone, 'warn', 'a reset is visually distinct from progress');
});

test('different pending candidates stay distinguishable', async () => {
    const {titles, texts} = await timelineFor([
        confirmation(0, 1, 1, 'HYPOTHETICAL_RAISE:26.5', 'HYPOTHETICAL_LOWER:25.5'),
        confirmation(1, 1, 1, 'HYPOTHETICAL_RAISE:26.0', 'HYPOTHETICAL_RAISE:26.5'),
        confirmation(2, 0, 1, null, 'HYPOTHETICAL_RAISE:26.0')]);
    assert.deepEqual(titles, ['Pending candidate changed', 'Pending candidate changed', 'Confirmation progress changed']);
    assert.equal(texts[0], 'Pending candidate hypothetical raise to 26.5 V → hypothetical lower to 25.5 V.');
    assert.equal(texts[1], 'Pending candidate hypothetical raise to 26.0 V → hypothetical raise to 26.5 V.');
    assert.equal(texts[2], 'Compatible observations 0 → 1; pending candidate none → hypothetical raise to 26.0 V.');
});

test('battery-response events are never merged, even when their payloads look alike', async () => {
    const response = (minute, payload) => ({event_time: at(minute), event_type: 'BATTERY_RESPONSE_CHANGED', payload});
    const {titles, texts, counts} = await timelineFor([
        response(0, {interpretation: 'EMPIRICAL_RESPONSE_CHANGED_CAUSE_UNKNOWN'}),
        response(1, {interpretation: 'EMPIRICAL_RESPONSE_CHANGED_CAUSE_UNKNOWN'}),
        response(2, {interpretation: 'SLOPE_REVERSED'})]);
    assert.equal(titles.length, 3, 'each response change is its own entry');
    assert.deepEqual(counts, [null, null, null]);
    assert.equal(texts[0], 'Reported response changed; the cause is unknown.');
    assert.equal(texts[2], 'Reported response changed (SLOPE_REVERSED).');
});

test('only truly identical adjacent events are condensed, by an explicit content rule', async () => {
    const recovered = (minute, extra) => Object.assign({event_time: at(minute), event_type: 'WORKER_RECOVERED',
        worker_id: 'pod-b', state_revision: minute, event_id: 'e' + minute, payload: {worker_id: 'pod-b'}}, extra || {});
    let result = await timelineFor([recovered(0), recovered(1), recovered(2)]);
    assert.deepEqual(result.counts, ['×3'], 'same type, worker and payload: merged; time/id/revision are not content');
    assert.match(result.texts[0], /^Worker pod-b recovered\. Identical event recorded 3 times since /);
    result = await timelineFor([recovered(0), recovered(1, {payload: {worker_id: 'pod-b', previous_failures: 2}})]);
    assert.deepEqual(result.counts, [null, null], 'any payload difference keeps events separate');
    result = await timelineFor([recovered(0), recovered(1, {worker_id: 'pod-c'})]);
    assert.deepEqual(result.counts, [null, null], 'a different worker keeps events separate');
    result = await timelineFor([confirmation(0, 1, 2), confirmation(1, 1, 2)]);
    assert.deepEqual(result.counts, ['×2'], 'identical confirmation transitions may be condensed');
    result = await timelineFor([recovered(0), confirmation(1, 1, 2), recovered(2)]);
    assert.deepEqual(result.counts, [null, null, null], 'only adjacent events are merged');
});

test('a stability change while the decision stays HOLD is labelled as such inside a mixed timeline', async () => {
    const {titles, texts} = await timelineFor([
        {event_time: at(0), event_type: 'DECISION_CHANGED',
            payload: {from: {decision: 'HOLD', stability: 'AWAITING_CONFIRMATION'}, to: {decision: 'HOLD', stability: 'NO_CANDIDATE'}}},
        confirmation(1, 2, 0)]);
    assert.deepEqual(titles, ['Stability changed', 'Confirmation reset']);
    assert.equal(texts[0], 'Decision stayed HOLD; stability awaiting confirmation → no candidate.');
});

test('more than five meaningful events: newest five shown in order, the rest pointed to and fully preserved', async () => {
    const events = [confirmation(0, 1, 2),
        {event_time: at(1), event_type: 'EVIDENCE_BLOCKED', payload: {added: ['LINE_MODE_CHARGING_CONFOUNDS_VOLTAGE']}},
        {event_time: at(2), event_type: 'WORKER_RECOVERED', payload: {worker_id: 'pod-b'}},
        {event_time: at(3), event_type: 'WORKER_FAILED', payload: {worker_id: 'pod-b', error: 'TimeoutError'}},
        confirmation(4, 2, 0),
        {event_time: at(5), event_type: 'HYPOTHESIS_CREATED', payload: {candidate_voltage: 26.5, threshold: 'battery'}},
        {event_time: at(6), event_type: 'EVIDENCE_RECOVERED', payload: {removed: ['INSUFFICIENT_COMPARABLE_PAIRS']}},
        {event_time: at(7), event_type: 'BATTERY_RESPONSE_CHANGED', payload: {interpretation: 'EMPIRICAL_RESPONSE_CHANGED_CAUSE_UNKNOWN'}},
        {event_time: at(8), event_type: 'JOURNAL_V2_CUTOVER', payload: {initialized_from: 'V1:abc'}}];
    const {get, entries, titles} = await timelineFor(events);
    assert.equal(entries.length, 5, 'at most five entries by default');
    assert.deepEqual(titles, ['Confirmation progress changed', 'Evidence became blocked', 'Worker recovered',
        'Worker reported an error', 'Confirmation reset'], 'newest first; no type is silently filtered out');
    const pointer = get('journal-events-body').children[5];
    assert.equal(textOf(pointer), '4 other events are in the full event history.');
    get('journal-raw-events-disclosure').open = true;
    get('journal-raw-events-disclosure').toggle();
    const raw = get('journal-raw-events').children;
    assert.equal(raw.length, 9, 'every original event is kept in the full history');
    raw.forEach((item, index) => {
        const details = item.children[0];
        assert.equal(details.children[0].children[0].dateTime, events[index].event_time, 'original timestamp kept');
        assert.deepEqual(JSON.parse(details.children[2].textContent), events[index], 'original event object kept verbatim');
    });
    assert.equal(get('journal-raw-events-count').textContent, '(9)');
});

test('hypothesis events show the monitored threshold label for the stored threshold key', async () => {
    const {texts} = await timelineFor([{event_time: at(0), event_type: 'HYPOTHESIS_CREATED',
        payload: {candidate_voltage: 26.5, threshold: 'battery'}}]);
    assert.equal(texts[0], 'Hypothetical Back to Battery 26.5 V recorded for review; non-actionable.');
});

test('v1 journal payload (no journal key, or installed but inactive) is labelled v1', async () => {
    let get = await render({decisions: [], has_more: false, latest_decision: null, worker_health: {}});
    assert.equal(rows(get('diag-journal'))['Mode'], 'Journal v1: every evaluation is stored as a full entry');
    get = await render({decisions: [], has_more: false, latest_decision: null, worker_health: {},
        journal: {mode: 'v1', v2_installed: true}});
    assert.match(rows(get('diag-journal'))['Mode'], /Journal v2 installed but not active/);
    assert.match(textOf(get('journal-events-body').children[0]), /separate event timeline is unavailable/);
    assert.equal(get('journal-raw-events-disclosure').hidden, true);
});
