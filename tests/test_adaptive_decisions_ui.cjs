'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'app/web/static/adaptive_decisions.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
function element() {
    return {children: [], textContent: '', href: '', appendChild(child) {this.children.push(child);},
        replaceChildren() {this.children = [];}};
}
test('decision page shows blocked evidence and exports selected range', async () => {
    const elements = {'decision-status':element(), 'decision-body':element(), 'decision-export':element(),
        'worker-status':element(), 'worker-last-evaluation':element(), 'worker-last-success':element(),
        'worker-reason':element(), 'worker-window':element(), 'worker-evidence':element(),
        'worker-current':element(), 'worker-baseline':element(), 'worker-proposed':element(),
        'worker-stability':element(), 'worker-confidence':element()};
    const buttons = [1,2,7].map(days => ({dataset:{decisionDays:String(days)}, addEventListener(_, fn) {this.click=fn;}}));
    let start, urls=[];
    const document = {getElementById: id => elements[id], createElement: element,
        querySelectorAll: () => buttons, addEventListener: (_, fn) => {start=fn;}};
    const fetch = async url => {urls.push(url); return {ok:true, json:async () => ({worker_health:{
        enabled:true, status:'degraded', last_evaluation_at:'2026-01-01T00:00:00Z',
        last_successful_evaluation_at:'2026-01-01T00:00:00Z', blocking_reason:'DEVICE_TIMESTAMP_PROVENANCE_UNVERIFIED',
        observation_count:4, collection_count:8, effective_window_seconds:3600}, decisions:[{
        evaluated_at:'2026-01-01T00:00:00Z', decision:'INSUFFICIENT_DATA', threshold:'battery',
        evidence_level:'EXPLORATORY', explanation:'Measurement time is unverified',
        reason_codes:['DEVICE_TIMESTAMP_PROVENANCE_UNVERIFIED'], collection_count:4, effective_window_seconds:3600,
        historical_settings:{battery:26.0, provenance:'historical or illustrative; not current'},
        hardware_write_attempted:false},
        {evaluated_at:'2026-01-01T00:01:00Z', decision:'HOLD', evidence_level:'EXPLORATORY', reason_codes:['PROPOSAL_AWAITING_CONFIRMATION']},
        {evaluated_at:'2026-01-01T00:02:00Z', decision:'BLOCKED', evidence_level:'EXPLORATORY', reason_codes:['WORKER_EVALUATION_FAILED']}]})};};
    vm.runInNewContext(source, {document, fetch, JSON, String, Number});
    start(); await tick();
    assert.equal(elements['decision-body'].children.length, 3);
    const cells = elements['decision-body'].children[0].children;
    assert.equal(cells[1].textContent, 'INSUFFICIENT_DATA');
    assert.equal(cells[6].textContent, '26 V (historical; not current)');
    assert.match(cells[14].children[0].children[1].textContent, /DEVICE_TIMESTAMP_PROVENANCE_UNVERIFIED/);
    assert.equal(elements['decision-body'].children[1].children[1].textContent, 'HOLD');
    assert.equal(elements['decision-body'].children[2].children[1].textContent, 'BLOCKED');
    assert.equal(elements['worker-status'].textContent, 'DEGRADED (enabled)');
    assert.equal(elements['worker-last-success'].textContent, '2026-01-01T00:00:00Z');
    assert.match(elements['worker-window'].textContent, /4 unique observations/);
    assert.equal(elements['worker-baseline'].textContent, '26 V (historical; not current)');
    buttons[2].click(); await tick();
    assert.equal(elements['decision-export'].href, '/api/controls/decisions?days=7');
    assert.equal(urls[1], '/api/controls/decisions?days=7');
});
test('controls links to the read-only decision page', () => {
    const html = fs.readFileSync(path.join(root, 'app/web/templates/controls.html'), 'utf8');
    assert.match(html, /href="\/controls\/decisions"/);
});
test('decision page explains cycling, blocked voltage comparison and hypothesis support', async () => {
    const ids = ['decision-status','decision-body','decision-export','worker-status','worker-last-evaluation',
        'worker-last-success','worker-reason','worker-window','worker-evidence','worker-current','worker-baseline',
        'worker-proposed','worker-stability','worker-confidence','worker-cycling','worker-voltage',
        'worker-confounded','worker-states','worker-samples','worker-hypothesis','worker-hold-reasons'];
    const run = async (latest) => {
        const elements = Object.fromEntries(ids.map(id => [id, element()]));
        let start;
        const document = {getElementById: id => elements[id], createElement: element,
            querySelectorAll: () => [], addEventListener: (_, fn) => {start=fn;}};
        const fetch = async () => ({ok:true, json:async () => ({worker_health:{enabled:true, status:'degraded'}, decisions:[latest]})});
        vm.runInNewContext(source, {document, fetch, JSON, String, Number, Object, Array, Math});
        start(); await tick();
        return elements;
    };
    const elements = await run({evaluated_at:'2026-11-06T15:00:00Z', decision:'HOLD', confidence:null,
        reason_codes:['CYCLING_DETECTED','LINE_MODE_CHARGING_CONFOUNDS_VOLTAGE'],
        confidence_breakdown:{basis:'NO_VOLTAGE_ESTIMATE', cycling_detection:0.97, voltage_contrast:null},
        exploratory_voltage_contrast:{contrast_v:null, pair_rejections:{TIME_GAP_EXCEEDED:3, LOAD_NOT_COMPARABLE:0},
            primary_exclusions:{LINE_MODE_CHARGING_CONFOUNDED:56},
            charging_confounded_contrast:{median_difference_v:0.35, matched_pairs:43, median_line_charge_a:16.2}},
        decision_diagnostics:{
            cycling:{detected:true, reported_mode_changes:21, short_runs:20, complete_runs:20,
                criteria:{short_run_max_seconds:1200}, detection_confidence:0.97,
                transition_states:{'INVERT_TO_LINE:LINE_CHARGING':7}},
            voltage_comparison:{status:'BLOCKED', blocked_by:['LINE_MODE_CHARGING_CONFOUNDS_VOLTAGE','INSUFFICIENT_COMPARABLE_PAIRS'],
                comparable_pairs:0, required_pairs:3},
            evidence_classes:{LINE_CHARGING:{count:56, voltage_range_v:[25.8, 26.0]}, INVERT_DISCHARGING:{count:67, voltage_range_v:[25.4, 25.7]}},
            sample_counts:{raw_collections:127, analysed_observations:117, rejected_duplicate_cached_payload:8,
                rejected_missing_or_invalid_numeric:2, rejected_analysis_cap:0},
            hypothesis_support:{supported:false, unsupported_because:['NO_VOLTAGE_CONTRAST_ESTIMATE']},
            hold_reasons:['CYCLING_DETECTED','LINE_MODE_CHARGING_CONFOUNDS_VOLTAGE']}});
    assert.match(elements['worker-cycling'].textContent, /^DETECTED: 21 reported mode changes, 20 of 20 bounded runs shorter than 20 min/);
    assert.match(elements['worker-cycling'].textContent, /physical switch times unknown/);
    assert.match(elements['worker-voltage'].textContent, /^Blocked: LINE_MODE_CHARGING_CONFOUNDS_VOLTAGE, INSUFFICIENT_COMPARABLE_PAIRS \(0 of 3 required pairs\)/);
    assert.match(elements['worker-voltage'].textContent, /LINE_MODE_CHARGING_CONFOUNDED 56/);
    assert.match(elements['worker-voltage'].textContent, /TIME_GAP_EXCEEDED 3/);
    assert.doesNotMatch(elements['worker-voltage'].textContent, /LOAD_NOT_COMPARABLE/);
    assert.match(elements['worker-confounded'].textContent, /not sag or rebound; never used for proposals/);
    assert.match(elements['worker-states'].textContent, /LINE_CHARGING: 56 \(25.8–26 V\)/);
    assert.match(elements['worker-samples'].textContent, /^117 analysed of 127 collections; rejected: missing\/invalid fields 2, duplicate cached payload 8$/);
    assert.match(elements['worker-hypothesis'].textContent, /^Not supported: NO_VOLTAGE_CONTRAST_ESTIMATE\. Frequent switching alone is not threshold evidence\.$/);
    assert.match(elements['worker-confidence'].textContent, /^voltage contrast none \(no estimate\); cycling detection 0.97; basis NO_VOLTAGE_ESTIMATE/);
    assert.equal(elements['worker-hold-reasons'].textContent, 'CYCLING_DETECTED, LINE_MODE_CHARGING_CONFOUNDS_VOLTAGE');

    const legacy = await run({evaluated_at:'2026-11-06T14:00:00Z', decision:'HOLD', confidence:0.45, reason_codes:['COLLECTION_TIME_ONLY']});
    assert.equal(legacy['worker-cycling'].textContent, 'Not recorded by this algorithm version');
    assert.match(legacy['worker-confidence'].textContent, /^0.45 — COLLECTION_TIME_ONLY/);
});
