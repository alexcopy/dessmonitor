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
