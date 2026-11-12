'use strict';
process.env.TZ = 'UTC';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'app/web/static/adaptive_decisions.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'app/web/templates/adaptive_decisions.html'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
function element() {
    return {children: [], dataset: {}, textContent: '', href: '', appendChild(child) {this.children.push(child);},
        replaceChildren() {this.children = [];}, addEventListener(name, fn) {this[name] = fn;}};
}
// Visible text of a fake element: its own textContent, or its children's when it has any.
function textOf(node) {
    if (!node) return '';
    return node.children && node.children.length ? node.children.map(textOf).join(' ') : String(node.textContent);
}
// Rows of a generated <dl class="adl-kv"> as {label: value}.
function rows(list) {
    return Object.fromEntries(list.children.map(row => [row.children[0].textContent, textOf(row.children[1])]));
}
function harness(options) {
    const elements = {};
    for (const [id, props] of Object.entries(options.preset || {})) elements[id] = Object.assign(element(), props);
    // decision-history exists only when preset, so History defaults to expanded like a page without it.
    const get = id => elements[id] || (id === 'decision-history' ? undefined : (elements[id] = element()));
    let start;
    const urls = [];
    const document = {getElementById: get, createElement: element, querySelectorAll: () => options.buttons || [],
        addEventListener: (_, fn) => {start = fn;}};
    const fetch = async url => {
        urls.push(url);
        if (url.includes('/battery-observations')) {
            return options.battery ? options.battery(url) : {ok: true, json: async () => ({observations: [], battery_behavior: [], has_more: false})};
        }
        return options.decisions(url);
    };
    vm.runInNewContext(source, {document, fetch, JSON, String, Number, Object, Array, Math});
    return {elements, get, urls, start: async () => { start(); await tick(); await tick(); }};
}
const ok = body => ({ok: true, json: async () => JSON.parse(JSON.stringify(body))});

test('decision page shows blocked evidence and exports selected range', async () => {
    const buttons = [1, 2, 7].map(days => ({dataset: {decisionDays: String(days)}, addEventListener(_, fn) {this.click = fn;}}));
    const page = harness({buttons, decisions: async () => ok({latest_decision: {evaluated_at: '2026-01-01T00:00:00Z',
        decision: 'INSUFFICIENT_DATA', threshold: null, monitored_threshold: {key: 'battery', label: 'Back to Battery'},
        current_settings: {battery: 26.5, provenance: 'unknown'}, historical_settings: {battery: 26.0},
        reason_codes: ['DEVICE_TIMESTAMP_PROVENANCE_UNVERIFIED']},
    worker_health: {enabled: true, status: 'degraded', last_evaluation_at: '2026-01-01T00:00:00Z',
        last_successful_evaluation_at: '2026-01-01T00:00:00Z', blocking_reason: 'DEVICE_TIMESTAMP_PROVENANCE_UNVERIFIED',
        observation_count: 4, collection_count: 8, effective_window_seconds: 3600},
    decisions: [{evaluated_at: '2026-01-01T00:00:00Z', decision: 'INSUFFICIENT_DATA', threshold: null,
        monitored_threshold: {key: 'battery', label: 'Back to Battery'}, current_settings: {battery: 26.5, provenance: 'unknown'},
        evidence_level: 'EXPLORATORY', explanation: 'Measurement time is unverified',
        reason_codes: ['DEVICE_TIMESTAMP_PROVENANCE_UNVERIFIED'], collection_count: 4, effective_window_seconds: 3600,
        historical_settings: {battery: 26.0, provenance: 'historical or illustrative; not current'}, hardware_write_attempted: false},
    {evaluated_at: '2026-01-01T00:01:00Z', decision: 'HOLD', evidence_level: 'EXPLORATORY', reason_codes: ['PROPOSAL_AWAITING_CONFIRMATION']},
    {evaluated_at: '2026-01-01T00:02:00Z', decision: 'BLOCKED', evidence_level: 'EXPLORATORY', reason_codes: ['WORKER_EVALUATION_FAILED']}]})});
    await page.start();
    const body = page.get('decision-body');
    assert.equal(body.children.length, 3);
    const cells = body.children[0].children;
    assert.equal(cells[0].children[0].dateTime, '2026-01-01T00:00:00Z', 'the original timestamp is kept on <time>');
    assert.match(cells[0].children[0].textContent, /^1 Jan( 2026)? 00:00$/, 'compact local time instead of raw ISO');
    assert.match(cells[0].children[0].title, /UTC\+00:00\); stored as 2026-01-01T00:00:00Z/);
    assert.equal(cells[1].textContent, 'INSUFFICIENT DATA');
    assert.match(cells[2].textContent, /^Limited · battery /);
    const detail = cells[3].children[0]; detail.open = true; detail.toggle();
    assert.match(detail.children[1].textContent, /DEVICE_TIMESTAMP_PROVENANCE_UNVERIFIED/);
    assert.match(detail.children[1].textContent, /"battery": 26/);
    assert.equal(body.children[1].children[1].textContent, 'HOLD');
    assert.equal(body.children[1].children[3].children[0].children[0].textContent, 'The hypothetical proposal requires more confirmation.');
    assert.equal(body.children[2].children[1].textContent, 'BLOCKED');
    const worker = rows(page.get('diag-worker'));
    assert.equal(worker['Reported status'], 'DEGRADED (enabled)');
    assert.equal(worker['Status'], 'Running · evidence limited', 'limited evidence is not reported as a worker failure');
    assert.match(worker['Last successful evaluation'], /00:00$/);
    const hypothesis = rows(page.get('diag-hypothesis'));
    assert.equal(hypothesis['Historical reference'], '26 V (historical; not current)');
    assert.equal(hypothesis['Current setting'], 'Not verified (no device read-back)');
    assert.equal(hypothesis['Monitored threshold'], 'Back to Battery');
    assert.equal(rows(page.get('diag-evidence'))['Observations'], '4 unique from 8 collections');
    buttons[2].click(); await tick();
    assert.equal(page.get('decision-export').href, '/api/controls/decisions/export?days=7');
    assert.ok(page.urls.includes('/api/controls/decisions?days=7&limit=50'));
});

test('controls links to the read-only decision page', () => {
    const controls = fs.readFileSync(path.join(root, 'app/web/templates/controls.html'), 'utf8');
    assert.match(controls, /href="\/controls\/decisions"/);
});

test('layout: one primary decision panel, three indicators, timeline, closed diagnostics and history', () => {
    const order = ['id="decision-primary"', 'id="worker-current-decision"', 'id="worker-summary-hold"',
        'class="adl-indicators"', 'id="journal-events-body"', 'id="technical-details"', 'id="decision-history"']
        .map(marker => html.indexOf(marker));
    order.forEach(index => assert.ok(index >= 0));
    assert.deepEqual(order, order.slice().sort((a, b) => a - b), 'decision first, diagnostics and history last');
    assert.equal((html.match(/class="adl-indicator"/g) || []).length, 3, 'at most three secondary indicators');
    assert.ok(html.indexOf('id="decision-history"') < html.indexOf('id="battery-observation-body"'));
    assert.ok(html.indexOf('id="decision-history"') < html.indexOf('id="decision-body"'));
    assert.ok(html.indexOf('id="decision-history"') < html.indexOf('data-decision-days'), 'range controls live inside History');
    assert.match(html, /<details class="adl-disclosure" id="technical-details">\s*<summary>Technical details/);
    assert.match(html, /<details class="adl-disclosure" id="decision-history">\s*<summary>History/);
    assert.doesNotMatch(html, /<details[^>]*\bopen(?:\s|>)/, 'all disclosure sections start collapsed');
    for (const group of ['diag-worker', 'diag-evidence', 'diag-cycling', 'diag-voltage', 'diag-hypothesis', 'diag-journal']) {
        assert.match(html, new RegExp('<dl class="adl-kv" id="' + group + '">'), group + ' diagnostic group');
    }
    assert.match(html, /DRY-RUN — NO HARDWARE WRITE/, 'safety marker also pinned by test_web_navigation_and_static.py');
    assert.match(html, /id="decision-attention" role="status"/, 'attention state is announced as status text');
    assert.match(html, /Export Journal v1 JSON/);
    assert.match(html, /Journal v2 state and events are not included/);
});

test('styles use the dark dashboard tokens, are scoped, and contain no light surfaces', () => {
    const style = html.slice(html.indexOf('<style>') + 7, html.indexOf('</style>'));
    assert.doesNotMatch(html, /class="[^"]*\bbox\b/, 'Bulma white .box cards are not used');
    assert.doesNotMatch(style, /background(?:-color)?:\s*(?:white|#[c-f][0-9a-f]|rgb\(2[0-9]{2})/i, 'no light background colours');
    for (const token of ['--bg', '--surface', '--surface2', '--border', '--text', '--text-dim', '--teal', '--amber', '--green', '--red']) {
        assert.ok(style.includes('var(' + token + ')'), token + ' is used');
    }
    const selectors = style.replace(/\/\*[\s\S]*?\*\//g, '').replace(/@media[^{]+\{/g, '').split('}')
        .map(rule => rule.split('{')[0].trim()).filter(Boolean);
    for (const selector of selectors) {
        for (const part of selector.split(',')) {
            assert.match(part.trim(), /^(\.adl\b|\.adl-|\[data-tone)/, 'unscoped selector: ' + part.trim());
        }
    }
    assert.match(style, /overflow-wrap:anywhere/, 'long values wrap instead of forcing page-level overflow');
    assert.match(style, /@media \(max-width: 520px\)/, 'narrow-screen layout exists');
});

test('history waits for expansion before rendering decisions or fetching observations', async () => {
    const page = harness({preset: {'decision-history': {open: false}}, decisions: async () => ok({
        latest_decision: {evaluated_at: '2026-11-12T10:00:00Z', decision: 'HOLD', evidence_level: 'EXPLORATORY',
            reason_codes: ['CYCLING_DETECTED']}, worker_health: {enabled: true, status: 'healthy'},
        decisions: [{evaluation_id: 'v1-row', evaluated_at: '2026-11-12T09:00:00Z', decision: 'HOLD',
            evidence_level: 'EXPLORATORY', reason_codes: ['CYCLING_DETECTED'], explanation: 'Full record text'}],
        has_more: false, journal: {mode: 'v1'}})});
    await page.start();
    assert.equal(page.get('worker-current-decision').textContent, 'HOLD');
    assert.equal(page.get('decision-consequence').textContent, 'No threshold change recommended.');
    assert.equal(page.get('decision-primary').dataset.tone, 'hold');
    assert.equal(page.get('decision-body').children.length, 0, 'historical rows are buffered outside the DOM');
    assert.equal(page.urls.filter(url => url.includes('/battery-observations')).length, 0,
        'battery history is not requested to build the summary');
    page.get('decision-history').open = true; page.get('decision-history').toggle();
    await tick(); await tick();
    assert.equal(page.get('decision-body').children.length, 1);
    assert.equal(page.urls.filter(url => url.includes('/battery-observations')).length, 1);
    const record = page.get('decision-body').children[0].children[3].children[0];
    record.open = true; record.toggle();
    assert.match(record.children[1].textContent, /Full record text/);
});

test('current threshold appears only with verified device read-back', async () => {
    const run = async settings => {
        const page = harness({decisions: async () => ok({latest_decision: {evaluated_at: '2026-11-12T12:00:00Z', decision: 'HOLD',
            evidence_level: 'EXPLORATORY', monitored_threshold: {key: 'battery', label: 'Back to Battery'}, current_settings: settings},
        worker_health: {enabled: true, status: 'healthy'}, decisions: []})});
        await page.start();
        return page;
    };
    let page = await run({battery: 26.4, provenance: 'VERIFIED_DEVICE_READBACK'});
    assert.equal(page.get('worker-summary-current-threshold').textContent, '26.4 V (verified read-back)');
    assert.equal(page.get('current-threshold-label').textContent, 'Back to Battery setting');
    page = await run({battery: 26.4, provenance: 'unknown'});
    assert.equal(page.get('worker-summary-current-threshold').textContent, 'Not verified');
    assert.equal(page.get('worker-summary-current-threshold').dataset.tone, 'muted');
});

const RICH_LATEST = {evaluation_id: 'latest', evaluated_at: '2026-11-12T10:00:00Z', decision: 'HOLD',
    evidence_level: 'EXPLORATORY', unique_observation_count: 37, collection_count: 45, effective_window_seconds: 10800,
    current_settings: {battery: 26.0, provenance: 'unknown'}, candidate_voltage: 26.5,
    confirmation_progress: {confirmed: 1, required: 3},
    reason_codes: ['CYCLING_DETECTED', 'LINE_MODE_CHARGING_CONFOUNDS_VOLTAGE', 'INSUFFICIENT_COMPARABLE_PAIRS',
        'DEVICE_TIMESTAMP_PROVENANCE_UNVERIFIED'],
    decision_diagnostics: {cycling: {detected: true, reported_mode_changes: 7},
        voltage_comparison: {status: 'BLOCKED', comparable_pairs: 0, required_pairs: 3},
        sample_counts: {rejected_duplicate_cached_payload: 8},
        hold_reasons: ['CYCLING_DETECTED', 'LINE_MODE_CHARGING_CONFOUNDS_VOLTAGE', 'INSUFFICIENT_COMPARABLE_PAIRS',
            'DEVICE_TIMESTAMP_PROVENANCE_UNVERIFIED']}};

// ── Confirmed hypothetical decisions (independent review finding B2) ─────────────────────────
// Fields follow the entry built by app/control/online_output_learning.py when a candidate is confirmed:
// decision = candidate direction, threshold = 'battery' (a key), proposed_voltage = candidate_voltage,
// stability = 'CONFIRMED', actionability = 'NON_ACTIONABLE', confirmation_progress {confirmed, required: 2}.
function confirmedEntry(direction, voltage, overrides) {
    return Object.assign({evaluation_id: 'confirmed-' + direction, evaluated_at: '2026-11-12T10:00:00Z',
        algorithm_version: 'online-exploratory-test', decision: 'HYPOTHETICAL_' + direction, threshold: 'battery',
        proposed_voltage: voltage, candidate_voltage: voltage, stability: 'CONFIRMED', candidate_status: 'CONFIRMED',
        actionability: 'NON_ACTIONABLE', confirmation_progress: {confirmed: 2, required: 2},
        cooldown_remaining_seconds: 3600, monitored_threshold: {key: 'battery', label: 'Back to Battery'},
        current_settings: {utility: null, battery: null, provenance: 'unknown'},
        historical_settings: {utility: 25.5, battery: 26.0, provenance: 'HISTORICAL_BASELINE; not verified current settings'},
        evidence_level: 'EXPLORATORY', reason_codes: [], hardware_write_attempted: false}, overrides || {});
}
async function renderConfirmed(entry) {
    const page = harness({decisions: async () => ok({latest_decision: entry, decisions: [entry],
        worker_health: {enabled: true, status: 'healthy'}, journal: {mode: 'v2', events: [], workers: []}})});
    await page.start();
    return page;
}
const WRITE_IMPLICATION = /\b(applied|apply|authori[sz]|will (be )?(set|written|change)|setting (was|has been) changed|inverter (is|was) (set|changed))\b/i;
function primaryPanelText(page) {
    return ['worker-current-decision', 'decision-consequence', 'worker-summary-hold', 'decision-proposal',
        'worker-summary-current-threshold', 'decision-attention-text'].map(id => page.get(id).textContent).join(' | ');
}

for (const [direction, voltage, word] of [['RAISE', 26.5, 'raise'], ['LOWER', 25.5, 'lower']]) {
    test('confirmed HYPOTHETICAL_' + direction + ' is shown as a non-actionable hypothetical decision', async () => {
        const page = await renderConfirmed(confirmedEntry(direction, voltage));
        assert.equal(page.get('worker-current-decision').textContent, 'HYPOTHETICAL ' + direction);
        assert.equal(page.get('decision-primary').dataset.tone, 'proposal');
        assert.equal(page.get('decision-consequence').textContent,
            'Hypothetical proposal for review only — non-actionable. This system does not write inverter settings.');
        assert.equal(page.get('decision-proposal').hidden, false);
        assert.equal(page.get('decision-proposal').textContent,
            'Confirmed hypothetical ' + word + ': Back to Battery ' + voltage + ' V — non-actionable; confirmation 2 of 2.');
        assert.equal(page.get('worker-summary-current-threshold').textContent, 'Not verified',
            'a confirmed hypothesis is never shown as the current inverter setting');
        assert.doesNotMatch(primaryPanelText(page), WRITE_IMPLICATION, 'no text implies a hardware write or authorization');
        const hypothesis = rows(page.get('diag-hypothesis'));
        assert.equal(hypothesis['Hypothetical proposal'], 'Back to Battery proposed: ' + voltage + ' V (hypothetical; non-actionable)');
        assert.equal(hypothesis['Confirmation'], '2 of 2 observations');
        assert.equal(hypothesis['Stability'], 'confirmed (CONFIRMED)');
        assert.equal(hypothesis['Current setting'], 'Not verified (no device read-back)');
        assert.equal(hypothesis['Cooldown remaining'], '60 min');
        assert.equal(page.get('decision-body').children[0].children[1].textContent, 'HYPOTHETICAL ' + direction);
        assert.doesNotMatch(Object.values(hypothesis).join(' | '), WRITE_IMPLICATION);
    });
}

test('confirmed hypothetical decision with missing candidate or confirmation fields is handled safely', async () => {
    let page = await renderConfirmed(confirmedEntry('LOWER', null, {candidate_voltage: null, confirmation_progress: null}));
    assert.equal(page.get('worker-current-decision').textContent, 'HYPOTHETICAL LOWER');
    assert.equal(page.get('decision-proposal').textContent,
        'Confirmed hypothetical lower: Back to Battery (voltage not recorded) — non-actionable; confirmation not recorded.');
    assert.doesNotMatch(page.get('decision-proposal').textContent, /undefined|null|NaN/);
    assert.equal(rows(page.get('diag-hypothesis'))['Hypothetical proposal'], 'Voltage not recorded (hypothetical; non-actionable)');
    assert.equal(rows(page.get('diag-hypothesis'))['Confirmation'], '—');
    page = await renderConfirmed(confirmedEntry('RAISE', 26.5, {threshold: undefined, stability: undefined,
        monitored_threshold: undefined, confirmation_progress: {confirmed: 2}}));
    assert.equal(page.get('decision-proposal').textContent,
        'Hypothetical raise: Back to Battery 26.5 V — non-actionable; confirmation not recorded.',
        'without stability CONFIRMED the line does not claim confirmation; partial progress is not invented');
    assert.doesNotMatch(primaryPanelText(page), /undefined|null|NaN/);
});

test('a pending candidate under HOLD is not presented as a confirmed decision', async () => {
    const page = await renderConfirmed(confirmedEntry('RAISE', 26.5, {decision: 'HOLD', proposed_voltage: null,
        stability: 'AWAITING_CONFIRMATION', confirmation_progress: {confirmed: 1, required: 2}}));
    assert.equal(page.get('worker-current-decision').textContent, 'HOLD');
    assert.equal(page.get('decision-proposal').textContent,
        'Hypothetical candidate: Back to Battery 26.5 V — non-actionable; confirmation 1 of 2.');
    assert.equal(rows(page.get('diag-hypothesis'))['Hypothetical proposal'],
        'Back to Battery candidate: 26.5 V (hypothetical; non-actionable)');
});

test('primary panel shows one dominant reason, not a concatenation, and three short indicators', async () => {
    const page = harness({preset: {'decision-history': {open: false}}, decisions: async () => ok({latest_decision: RICH_LATEST,
        decisions: [], worker_health: {enabled: true, status: 'degraded', blocking_reason: 'VERIFIED_OBSERVATION_SOURCE_UNAVAILABLE'}})});
    await page.start();
    const reason = page.get('worker-summary-hold').textContent;
    assert.equal(reason, 'Charging in Line mode can raise voltage, so it cannot be compared as battery sag. ' +
        '(0 of 3 comparable pairs found)', 'the voltage blocker outranks cycling and provenance caveats');
    assert.doesNotMatch(reason, /Frequent reported|Device measurement|CYCLING_DETECTED/);
    assert.equal(page.get('decision-more-reasons').hidden, false);
    assert.equal(page.get('decision-more-reasons').textContent, '3 more factors — see all reasons');
    assert.equal(page.get('decision-proposal').hidden, false);
    assert.equal(page.get('decision-proposal').textContent,
        'Hypothetical candidate: Back to Battery 26.5 V — non-actionable; confirmation 1 of 3.');
    assert.equal(page.get('indicator-mode-changes-value').textContent, '7');
    assert.match(page.get('indicator-mode-changes-note').textContent, /not verified physical switching/,
        'reported mode changes are distinguished from verified switching');
    assert.equal(page.get('indicator-mode-changes').dataset.tone, 'warn');
    assert.equal(page.get('indicator-observations-value').textContent, '37');
    assert.equal(page.get('indicator-observations-note').textContent, 'from 45 collections · 8 repeated payloads excluded');
    assert.equal(page.get('indicator-evidence-value').textContent, 'Limited');
    assert.equal(page.get('worker-summary-status').textContent, 'Running · evidence limited');
    assert.equal(page.get('worker-summary-status').dataset.tone, 'ok');
    assert.equal(page.get('decision-attention').dataset.tone, 'ok');
    assert.match(page.get('decision-attention-text').textContent, /^No action needed/);
    const reasons = page.get('worker-hold-reasons').children;
    assert.equal(reasons.length, 4, 'every recorded reason stays listed in Technical details');
    assert.equal(textOf(reasons[0].children[1]), 'main reason');
    assert.equal(reasons[0].children[2].textContent, 'LINE_MODE_CHARGING_CONFOUNDS_VOLTAGE');
    assert.ok(reasons.some(item => item.children.some(child => child.textContent === 'CYCLING_DETECTED')));
});

test('worker failure is flagged for attention and distinguished from degraded evidence', async () => {
    const page = harness({decisions: async () => ok({latest_decision: RICH_LATEST, decisions: [],
        worker_health: {enabled: true, status: 'degraded', blocking_reason: 'WORKER_EVALUATION_FAILED', worker_count: 2, failing_workers: 1},
        journal: {mode: 'v2', workers: [{worker_id: 'pod-a', status: 'healthy', last_heartbeat_at: '2026-11-12T10:00:00Z'},
            {worker_id: 'pod-b', status: 'failing', last_heartbeat_at: '2026-11-12T09:59:00Z', consecutive_failures: 3,
                last_error: 'RuntimeError'}], events: []}})});
    await page.start();
    assert.equal(page.get('worker-summary-status').textContent, '1 of 2 failing');
    assert.equal(page.get('worker-summary-status').dataset.tone, 'bad');
    assert.equal(page.get('decision-attention').dataset.tone, 'bad');
    assert.match(page.get('decision-attention-text').textContent, /^Attention: worker failure — pod-b \(3 consecutive failures: RuntimeError\)/);
    const worker = rows(page.get('diag-worker'));
    assert.match(worker['Worker pod-b'], /^Failing · heartbeat .* · 3 consecutive failures \(RuntimeError\)$/);
    assert.match(worker['Worker pod-a'], /^Healthy/);
});

test('stale or missing heartbeat is a worker problem; disabled is shown as disabled', async () => {
    const run = async health => {
        const page = harness({decisions: async () => ok({latest_decision: null, decisions: [], worker_health: health})});
        await page.start();
        return page;
    };
    let page = await run({enabled: true, status: 'degraded', blocking_reason: 'WORKER_EVALUATION_STALE'});
    assert.equal(page.get('worker-summary-status').textContent, 'Stale');
    assert.equal(page.get('decision-attention').dataset.tone, 'bad');
    page = await run({enabled: false, status: 'disabled', blocking_reason: 'ADAPTIVE_OUTPUT_LEARNING_DISABLED'});
    assert.equal(page.get('worker-summary-status').textContent, 'Disabled');
    assert.equal(page.get('worker-summary-hold').textContent, 'The adaptive learning worker is disabled.');
    assert.equal(page.get('worker-current-decision').textContent, 'No decision yet');
});

test('API failure shows an explicit unavailable state with retry, and keeps prior data on refresh failure', async () => {
    let calls = 0;
    const page = harness({decisions: async () => { calls++; return calls !== 2 ? {ok: false, status: 503}
        : ok({latest_decision: RICH_LATEST, decisions: [], worker_health: {enabled: true, status: 'healthy'}}); }});
    await page.start();
    assert.equal(page.get('worker-current-decision').textContent, 'Unavailable');
    assert.equal(page.get('decision-primary').dataset.tone, 'bad');
    assert.equal(page.get('decision-retry').hidden, false);
    page.get('decision-retry').click(); await tick(); await tick();
    assert.equal(page.get('worker-current-decision').textContent, 'HOLD');
    assert.equal(page.get('decision-retry').hidden, true);
    page.get('decision-retry').click(); await tick(); await tick();
    assert.equal(page.get('worker-current-decision').textContent, 'HOLD', 'a failed refresh keeps the last loaded decision');
    assert.match(page.get('decision-attention-text').textContent, /^Refresh failed — showing data loaded at /);
    assert.equal(page.get('decision-attention').dataset.tone, 'bad');
});

test('history repeats each explanation once and keeps unknown codes visible', async () => {
    const decisions = [1, 2, 3].map(index => ({evaluation_id: 'row-' + index, evaluated_at: '2026-11-12T0' + index + ':00:00Z',
        decision: 'HOLD', reason_codes: ['CYCLING_DETECTED', 'LINE_MODE_CHARGING_CONFOUNDS_VOLTAGE'],
        decision_diagnostics: {hold_reasons: ['CYCLING_DETECTED', 'LINE_MODE_CHARGING_CONFOUNDS_VOLTAGE']},
        explanation: 'Repeated raw technical explanation'}));
    decisions.push({evaluation_id: 'row-4', evaluated_at: '2026-11-12T04:00:00Z', decision: 'BLOCKED',
        reason_codes: ['FUTURE_REASON_CODE'], explanation: 'Unknown reason raw text'});
    const page = harness({preset: {'decision-history': {open: false}}, decisions: async () => ok({latest_decision: RICH_LATEST,
        decisions, worker_health: {enabled: true, status: 'degraded'}})});
    await page.start();
    assert.equal(page.get('decision-body').children.length, 0);
    page.get('decision-history').open = true; page.get('decision-history').toggle(); await tick();
    const reasons = page.get('decision-body').children.map(row => row.children[3].children[0].children[0].textContent);
    assert.match(reasons[0], /Charging in Line mode can raise voltage/);
    assert.match(reasons[1], /repeat 2/);
    assert.match(reasons[2], /repeat 3/);
    assert.match(reasons[3], /Additional recorded reason: FUTURE_REASON_CODE/, 'unknown codes remain visible with a safe fallback');
    const record = page.get('decision-body').children[1].children[3].children[0];
    record.open = true; record.toggle();
    assert.match(record.children[1].textContent, /Repeated raw technical explanation/);
});

test('technical details are grouped and keep every scientific caveat', async () => {
    const latest = {evaluated_at: '2026-11-06T15:00:00Z', decision: 'HOLD', confidence: null,
        monitored_threshold: {key: 'battery', label: 'Back to Battery'}, current_settings: {battery: null},
        battery_v: 25.7, charge_a: 2.0, discharge_a: 8.5, historical_settings: {battery: 26.0},
        telemetry_audit: {device_measurement_timestamp_available_in_inverter_metrics: false},
        daily_behavior_review: {status: 'REVIEWED', review_date_utc: '2026-11-06', collection_count: 20,
            median_voltage_slope_change_v_per_hour: .1, learning_state_reset: false},
        virtual_threshold_experiment: {status: 'VIRTUAL_REPLAY_ONLY_BLOCKED_FROM_RECOMMENDATION',
            blockers: ['BMS_AND_MANUFACTURER_LIMITS_UNKNOWN'], candidates: [{back_to_utility_v: 25.0, back_to_battery_v: 26.0,
                sampled_utility_boundary_crossings: 1, sampled_battery_boundary_crossings: 0},
            {back_to_utility_v: 24.5, back_to_battery_v: 25.5, sampled_utility_boundary_crossings: 0, sampled_battery_boundary_crossings: 1}]},
        adaptive_hysteresis: {status: 'VIRTUAL WIDTHS ONLY; RECOMMENDATION BLOCKED', virtual_hysteresis_widths_v: [1.0]},
        charging_context: {evidence_labels: ['EXTERNAL_CHARGING_POSSIBLE', 'TOTAL_BATTERY_CURRENT_UNOBSERVED'],
            external_charging_status: 'POSSIBLE; NOT DETECTED AT A SPECIFIC MOMENT',
            pv_source_coverage: 'EASUN_VISIBLE_PV_ONLY; OTHER_ORIENTATIONS_UNOBSERVED', voltage_rises_without_easun_charge_reported: 2},
        battery_response: {capacity_status: 'CAPACITY_NOT_IDENTIFIABLE', throughput_status: 'INSUFFICIENT_MEASUREMENT_COVERAGE',
            latest_voltage_slope_v_per_hour: -.2, charged_ah_proxy: .3, discharged_ah_proxy: .2,
            freshness: 'COLLECTION_RECENT; DEVICE_FRESHNESS_UNVERIFIED', confidence: 'LOW_UNVERIFIED_SOURCE',
            recent_intervals: [{charge_current_delta_a_reported: 1.5, discharge_current_delta_a_reported: 2.0}]},
        reason_codes: ['CYCLING_DETECTED', 'LINE_MODE_CHARGING_CONFOUNDS_VOLTAGE'],
        confidence_breakdown: {basis: 'NO_VOLTAGE_ESTIMATE', cycling_detection: 0.97, voltage_contrast: null},
        exploratory_voltage_contrast: {contrast_v: null, pair_rejections: {TIME_GAP_EXCEEDED: 3, LOAD_NOT_COMPARABLE: 0},
            primary_exclusions: {LINE_MODE_CHARGING_CONFOUNDED: 56},
            charging_confounded_contrast: {median_difference_v: 0.35, matched_pairs: 43, median_line_charge_a: 16.2}},
        decision_diagnostics: {
            fast_context: {reported_mode_changes: 2, distinct_consecutive_payloads: 4, maximum_reported_observations: 6,
                pv_range_w: [100, 800], load_range_w: [500, 1200], latest_reported_current_direction: 'DISCHARGE_REPORTED',
                latest_collection_age_seconds: 120, collection_recent: true},
            cycling: {detected: true, reported_mode_changes: 21, short_runs: 20, complete_runs: 20,
                criteria: {short_run_max_seconds: 1200}, detection_confidence: 0.97, transition_states: {'INVERT_TO_LINE:LINE_CHARGING': 7}},
            mode_history_coverage: {truncated: true, limit_runs: 50, omitted_older_runs: 4},
            voltage_comparison: {status: 'BLOCKED', blocked_by: ['LINE_MODE_CHARGING_CONFOUNDS_VOLTAGE', 'INSUFFICIENT_COMPARABLE_PAIRS'],
                comparable_pairs: 0, required_pairs: 3},
            evidence_classes: {LINE_CHARGING: {count: 56, voltage_range_v: [25.8, 26.0]}, INVERT_DISCHARGING: {count: 67, voltage_range_v: [25.4, 25.7]}},
            sample_counts: {raw_collections: 127, analysed_observations: 117, rejected_duplicate_cached_payload: 8,
                rejected_missing_or_invalid_numeric: 2, rejected_analysis_cap: 0},
            hypothesis_support: {supported: false, unsupported_because: ['NO_VOLTAGE_CONTRAST_ESTIMATE']},
            hold_reasons: ['CYCLING_DETECTED', 'LINE_MODE_CHARGING_CONFOUNDS_VOLTAGE']}};
    const page = harness({decisions: async () => ok({worker_health: {enabled: true, status: 'degraded'}, latest_decision: latest, decisions: [latest]})});
    await page.start();
    const cycling = rows(page.get('diag-cycling'));
    assert.equal(cycling['Status'], 'Detected');
    assert.equal(cycling['Reported mode changes'], '21 in collection order · physical switch times unknown');
    assert.equal(cycling['Short runs'], '20 of 20 bounded runs shorter than 20 min');
    assert.match(cycling['Detection confidence'], /heuristic, not a calibrated probability/);
    assert.match(cycling['Mode history'], /^Truncated: only the newest 50 runs retained \(4 older runs omitted\)/);
    assert.equal(cycling['Interpretation'], 'Frequent switching alone is not threshold evidence.');
    const voltage = rows(page.get('diag-voltage'));
    assert.equal(voltage['Comparison'], 'Blocked');
    assert.equal(voltage['Blocked by'], 'LINE_MODE_CHARGING_CONFOUNDS_VOLTAGE, INSUFFICIENT_COMPARABLE_PAIRS');
    assert.equal(voltage['Comparable pairs'], '0 of 3 required');
    assert.equal(voltage['Excluded observations'], 'LINE_MODE_CHARGING_CONFOUNDED 56');
    assert.equal(voltage['Rejected comparisons'], 'TIME_GAP_EXCEEDED 3', 'zero counts are omitted');
    assert.match(voltage['Charging-confounded difference'], /not sag or rebound; never used for proposals/);
    assert.match(voltage['Charging / discharging evidence'], /LINE_CHARGING: 56 \(25.8–26 V\)/);
    assert.equal(voltage['Latest battery voltage'], '25.7 V');
    assert.equal(voltage['Reported charge / discharge'], '2 / 8.5 A');
    assert.equal(voltage['Latest current deltas'], 'charge/discharge 1.5/2 A');
    assert.equal(voltage['Throughput (reported proxy)'], 'INSUFFICIENT_MEASUREMENT_COVERAGE: +0.3 Ah / −0.2 Ah');
    const evidence = rows(page.get('diag-evidence'));
    assert.equal(evidence['Samples analysed'], '117 of 127 collections');
    assert.equal(evidence['Rejected samples'], 'missing/invalid fields 2, duplicate cached payload 8');
    assert.equal(evidence['PV / load range'], '100–800 W / 500–1200 W');
    assert.equal(evidence['Reported current direction'], 'DISCHARGE_REPORTED');
    assert.match(evidence['External charging'], /NOT DETECTED AT A SPECIFIC MOMENT · EASUN current is not total battery current/);
    assert.match(evidence['Charging evidence labels'], /EXTERNAL_CHARGING_POSSIBLE/);
    assert.match(evidence['Telemetry provenance'], /device timestamp not persisted/);
    const hypothesis = rows(page.get('diag-hypothesis'));
    assert.equal(hypothesis['Hypothesis support'], 'Not supported: NO_VOLTAGE_CONTRAST_ESTIMATE. Frequent switching alone is not threshold evidence.');
    assert.equal(hypothesis['Confidence'], 'Voltage contrast none (no estimate) · cycling detection 0.97 · basis NO_VOLTAGE_ESTIMATE');
    assert.match(hypothesis['Virtual threshold pairs'], /2 exploratory virtual pairs; no winner; all non-actionable/);
    assert.match(hypothesis['Virtual threshold pairs'], /25\/26 V/, 'pair list sits in a nested disclosure');
    assert.match(hypothesis['Virtual threshold pairs'], /24\.5\/25\.5 V/);
    assert.match(hypothesis['Adaptive hysteresis'], /no recommendation/);
    assert.match(hypothesis['Daily review'], /learning state preserved/);
    const record = page.get('decision-body').children[0].children[3].children[0];
    record.open = true; record.toggle();
    assert.match(record.children[1].textContent, /"back_to_battery_v": 25.5/);
    const raw = page.get('latest-raw-disclosure');
    raw.open = true; raw.toggle();
    assert.match(page.get('latest-raw').textContent, /"median_line_charge_a": 16.2/, 'complete raw record stays reachable');

    const legacy = harness({decisions: async () => ok({worker_health: {enabled: true, status: 'degraded'},
        latest_decision: {evaluated_at: '2026-11-06T14:00:00Z', decision: 'HOLD', confidence: 0.45, reason_codes: ['COLLECTION_TIME_ONLY']},
        decisions: []})});
    await legacy.start();
    assert.equal(rows(legacy.get('diag-cycling'))['Cycling analysis'], 'Not recorded by this algorithm version');
    assert.equal(rows(legacy.get('diag-hypothesis'))['Confidence'], '0.45');
    assert.equal(rows(legacy.get('diag-hypothesis'))['Recorded reason codes'], 'COLLECTION_TIME_ONLY');
    assert.equal(legacy.get('indicator-mode-changes-value').textContent, '—');
});

test('Load more appends a cursor page and changing range resets the cursor', async () => {
    const buttons = [1, 2, 7].map(days => ({dataset: {decisionDays: String(days)}, addEventListener(_, fn) {this.click = fn;}}));
    const item = (id, time) => ({evaluation_id: id, evaluated_at: time, decision: 'HOLD', reason_codes: [], hardware_write_attempted: false});
    let page;
    page = harness({buttons, decisions: async url => {
        const count = page.urls.filter(x => x.includes('/api/controls/decisions?') && x.includes('limit=50')).length;
        const decisions = url.includes('days=2') ? [item('newer-range-a', '2026-01-03'), item('newer-range-b', '2026-01-02')]
            : count === 1 ? [item('new', '2026-01-03'), item('older', '2026-01-02')] : [item('oldest', '2026-01-01')];
        return ok({decisions, worker_health: {enabled: false, status: 'disabled'}, has_more: count === 1, next_cursor: count === 1 ? 'cursor-2' : null});
    }});
    await page.start();
    assert.equal(page.get('decision-body').children.length, 2);
    page.get('decision-load-more').click(); await tick();
    assert.equal(page.get('decision-body').children.length, 3);
    assert.ok(page.urls.some(url => url.includes('/api/controls/decisions?') && /cursor=cursor-2/.test(url)));
    buttons[1].click(); await tick();
    assert.equal(page.get('decision-body').children.length, 2);
    assert.ok(page.urls.some(url => url.includes('/api/controls/decisions?days=2&limit=50') && !url.includes('cursor=')));
});

test('failed Load more keeps its cursor usable and refreshes worker health on success', async () => {
    let calls = 0;
    const page = harness({decisions: async () => {
        calls++;
        if (calls === 2) return {ok: false, status: 503};
        return ok({decisions: [{evaluation_id: 'r' + calls, evaluated_at: '2026-01-01T00:00:00Z', decision: 'HOLD', evidence_level: 'OLD'}],
            latest_decision: {evaluated_at: '2026-01-02T00:00:00Z', evidence_level: 'CURRENT-' + calls, decision: 'HOLD'},
            worker_health: {enabled: true, status: calls === 1 ? 'healthy' : 'degraded', last_evaluation_at: 'latest-' + calls},
            has_more: calls === 1, next_cursor: calls === 1 ? 'stable-cursor' : null});
    }});
    await page.start();
    page.get('decision-load-more').click(); await tick();
    assert.equal(page.get('decision-body').children.length, 1);
    assert.equal(page.get('decision-load-more').disabled, false);
    assert.match(page.get('decision-status').textContent, /Retry/);
    assert.equal(page.get('worker-current-decision').textContent, 'HOLD', 'a failed cursor page does not blank the summary');
    assert.equal(rows(page.get('diag-worker'))['Reported status'], 'HEALTHY (enabled)');
    page.get('decision-load-more').click(); await tick();
    assert.ok(page.urls.some(url => url.includes('/api/controls/decisions?') && /cursor=stable-cursor/.test(url)));
    assert.equal(rows(page.get('diag-worker'))['Reported status'], 'DEGRADED (enabled)');
    assert.equal(rows(page.get('diag-worker'))['Last evaluation'], 'latest-3', 'unparseable times are shown verbatim');
    assert.equal(rows(page.get('diag-evidence'))['Evidence level'], 'Not recorded (CURRENT-3)');
});

test('observation and behavior grids paginate independently and identify collection provenance', async () => {
    const buttons = [1, 2, 7].map(value => ({dataset: {decisionDays: String(value)}, addEventListener(_, fn) {this.click = fn;}}));
    const observation = (time, quality) => ({collection_time: time, mode: 'Line Mode', battery_voltage_v: 25.6,
        easun_charge_current_a_reported: 0, easun_discharge_current_a_reported: 0, pv_power_w_reported: 500,
        load_power_w_reported: 700, voltage_change_since_previous_valid_v: .1, interval_seconds: 300,
        external_charging_status: 'NO_EASUN_CURRENT_REPORTED; EXTERNAL_CHARGING_POSSIBLE; TOTAL_BATTERY_CURRENT_UNOBSERVED',
        data_provenance: 'COLLECTION_TIME_ONLY; DEVICE_MEASUREMENT_TIME_UNVERIFIED', data_quality: quality, missing_fields: []});
    const behavior = {interval_end_collection_time: '2026-01-02T00:00:00Z', operating_context: 'Line Mode; PV 500 W; load 700 W',
        voltage_slope_v_per_hour: .2, observed_easun_charge_a_reported: 0, observed_easun_discharge_a_reported: 0,
        load_w_reported: 700, pv_delta_w_reported: 100, estimated_voltage_response: 'VOLTAGE_RISE_OBSERVED; EXTERNAL_CHARGING_POSSIBLE',
        charging_interpretation: 'NO_EASUN_CURRENT_REPORTED; EXTERNAL_CHARGING_POSSIBLE',
        capacity_or_response_proxy: 'CAPACITY_NOT_IDENTIFIABLE; TOTAL BATTERY CURRENT UNOBSERVED',
        confidence: 'LOW_UNVERIFIED_SOURCE', freshness: 'COLLECTION_RECENT; DEVICE_FRESHNESS_UNVERIFIED', limitation: 'Collection time only.'};
    const page = harness({buttons, decisions: async () => ok({decisions: [], worker_health: {enabled: false, status: 'disabled'}, has_more: false}),
        battery: async url => {
            if (url.includes('days=7')) return ok({observations: [observation('2026-01-01T00:00:00Z', 'PAYLOAD_CHANGED')], battery_behavior: [], has_more: false});
            if (url.includes('cursor=battery-next')) return ok({observations: [observation('2026-01-01T00:05:00Z', 'REPEATED_CACHED_PAYLOAD')], battery_behavior: [], has_more: false});
            return ok({observations: [observation('2026-01-02T00:00:00Z', 'PAYLOAD_CHANGED')], battery_behavior: [behavior],
                external_charging_status: 'POSSIBLE; NOT DETECTED AT A SPECIFIC MOMENT', has_more: true, next_cursor: 'battery-next'});
        }});
    await page.start();
    const observations = page.get('battery-observation-body'), behaviors = page.get('battery-behavior-body');
    assert.equal(observations.children.length, 1);
    assert.equal(behaviors.children.length, 1);
    assert.equal(observations.children[0].children[0].textContent, 'RECORDED_OBSERVATION');
    assert.equal(observations.children[0].children[1].children[0].dateTime, '2026-01-02T00:00:00Z');
    assert.equal(behaviors.children[0].children[0].textContent, 'DERIVED_ESTIMATE');
    assert.match(observations.children[0].children[11].textContent, /EXTERNAL_CHARGING_POSSIBLE/);
    assert.match(behaviors.children[0].children[9].textContent, /CAPACITY_NOT_IDENTIFIABLE/);
    assert.match(behaviors.children[0].children[9].textContent, /EASUN Ah \+\/− unavailable\/unavailable/);
    assert.equal(observations.children[0].children[10].textContent, '5 min');
    page.get('battery-load-more').click(); await tick();
    assert.ok(page.urls.some(url => url.includes('/api/controls/battery-observations?') && /cursor=battery-next/.test(url)));
    assert.equal(observations.children.length, 2);
    assert.match(observations.children[1].children[13].textContent, /REPEATED_CACHED_PAYLOAD/);
    buttons[2].click(); await tick();
    assert.equal(observations.children.length, 1);
    assert.ok(page.urls.some(url => url.includes('/api/controls/battery-observations?days=7&limit=50')));
});

test('range change while History is closed clears battery state without requesting it', async () => {
    const buttons = [1, 2, 7].map(value => ({dataset: {decisionDays: String(value)}, addEventListener(_, fn) {this.click = fn;}}));
    const page = harness({buttons, preset: {'decision-history': {open: false}},
        decisions: async () => ok({decisions: [], worker_health: {enabled: true, status: 'healthy'}, has_more: false})});
    await page.start();
    buttons[1].click(); await tick();
    assert.equal(page.urls.filter(url => url.includes('/battery-observations')).length, 0);
    assert.equal(page.get('battery-grid-status').textContent, 'Open History to load battery observations.');
    page.get('decision-history').open = true; page.get('decision-history').toggle(); await tick();
    assert.ok(page.urls.includes('/api/controls/battery-observations?days=2&limit=50'));
});

test('initial battery-history failure exposes retry and does not pretend the grid is empty', async () => {
    let attempts = 0;
    const page = harness({decisions: async () => ok({decisions: [], worker_health: {enabled: false, status: 'disabled'}, has_more: false}),
        battery: async () => {
            attempts++;
            if (attempts === 1) return {ok: false, status: 503};
            return ok({observations: [{collection_time: '2026-01-01T00:00:00Z', data_quality: 'PAYLOAD_CHANGED'}],
                battery_behavior: [], has_more: false, next_cursor: null});
        }});
    await page.start();
    assert.match(page.get('battery-grid-status').textContent, /results were cleared/);
    assert.equal(page.get('battery-observation-body').children.length, 0);
    assert.equal(page.get('battery-load-more').hidden, false);
    assert.equal(page.get('battery-load-more').textContent, 'Retry observations');
    page.get('battery-load-more').click(); await tick();
    assert.equal(page.get('battery-observation-body').children.length, 1);
    assert.equal(page.get('battery-load-more').hidden, true);
});
