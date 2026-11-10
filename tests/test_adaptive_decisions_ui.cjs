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
    return {children: [], dataset: {}, textContent: '', href: '', appendChild(child) {this.children.push(child);},
        replaceChildren() {this.children = [];}, addEventListener(name, fn) {this[name] = fn;}};
}
test('decision page shows blocked evidence and exports selected range', async () => {
    const elements = {'decision-status':element(), 'decision-body':element(), 'decision-export':element(), 'decision-load-more':element(),
        'battery-grid-status':element(), 'battery-observation-body':element(), 'battery-behavior-body':element(), 'battery-load-more':element(),
        'worker-status':element(), 'worker-last-evaluation':element(), 'worker-last-success':element(),
        'worker-reason':element(), 'worker-window':element(), 'worker-evidence':element(),
        'worker-current':element(), 'worker-baseline':element(), 'worker-proposed':element(),
        'worker-stability':element(), 'worker-confidence':element(), 'worker-monitored-threshold':element(),
        'worker-fast-context':element(), 'worker-battery-response':element(), 'worker-daily-review':element(),
        'worker-virtual-thresholds':element(), 'worker-adaptive-hysteresis':element(),
        'worker-telemetry-provenance':element()};
    const buttons = [1,2,7].map(days => ({dataset:{decisionDays:String(days)}, addEventListener(_, fn) {this.click=fn;}}));
    let start, urls=[];
    const document = {getElementById: id => elements[id], createElement: element,
        querySelectorAll: () => buttons, addEventListener: (_, fn) => {start=fn;}};
    const fetch = async url => {urls.push(url); if (url.includes('/battery-observations')) return {ok:true,json:async()=>({observations:[],battery_behavior:[],has_more:false})}; return {ok:true, json:async () => ({latest_decision:{evaluated_at:'2026-01-01T00:00:00Z',decision:'INSUFFICIENT_DATA',threshold:null,monitored_threshold:{key:'battery',label:'Back to Battery'},current_settings:{battery:26.5,provenance:'unknown'},historical_settings:{battery:26.0},reason_codes:['DEVICE_TIMESTAMP_PROVENANCE_UNVERIFIED']},worker_health:{
        enabled:true, status:'degraded', last_evaluation_at:'2026-01-01T00:00:00Z',
        last_successful_evaluation_at:'2026-01-01T00:00:00Z', blocking_reason:'DEVICE_TIMESTAMP_PROVENANCE_UNVERIFIED',
        observation_count:4, collection_count:8, effective_window_seconds:3600}, decisions:[{
        evaluated_at:'2026-01-01T00:00:00Z', decision:'INSUFFICIENT_DATA', threshold:null,
        monitored_threshold:{key:'battery',label:'Back to Battery'}, current_settings:{battery:26.5,provenance:'unknown'},
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
    assert.equal(cells[2].textContent, 'INSUFFICIENT_DATA');
    assert.match(cells[3].textContent, /BU 25.5 \/ BB 26 V \(historical\)/);
    const detail = cells[12].children[0]; detail.open = true; detail.toggle();
    assert.match(detail.children[1].textContent, /DEVICE_TIMESTAMP_PROVENANCE_UNVERIFIED/);
    assert.equal(elements['decision-body'].children[1].children[2].textContent, 'HOLD');
    assert.equal(elements['decision-body'].children[2].children[2].textContent, 'BLOCKED');
    assert.equal(elements['worker-status'].textContent, 'DEGRADED (enabled)');
    assert.equal(elements['worker-last-success'].textContent, '2026-01-01T00:00:00Z');
    assert.match(elements['worker-window'].textContent, /4 unique observations/);
    assert.equal(elements['worker-baseline'].textContent, '26 V (historical; not current)');
    assert.equal(elements['worker-current'].textContent, 'UNKNOWN (not verified)');
    assert.equal(elements['worker-monitored-threshold'].textContent, 'Back to Battery');
    buttons[2].click(); await tick();
    assert.equal(elements['decision-export'].href, '/api/controls/decisions/export?days=7');
    assert.ok(urls.includes('/api/controls/decisions?days=7&limit=50'));
});
test('controls links to the read-only decision page', () => {
    const html = fs.readFileSync(path.join(root, 'app/web/templates/controls.html'), 'utf8');
    assert.match(html, /href="\/controls\/decisions"/);
});
test('decision page explains cycling, blocked voltage comparison and hypothesis support', async () => {
    const ids = ['decision-status','decision-body','decision-export','decision-load-more','battery-grid-status','battery-observation-body',
        'battery-behavior-body','battery-load-more','worker-status','worker-last-evaluation',
        'worker-last-success','worker-reason','worker-window','worker-evidence','worker-current','worker-baseline',
        'worker-proposed','worker-stability','worker-confidence','worker-cycling','worker-voltage',
        'worker-confounded','worker-states','worker-samples','worker-hypothesis','worker-hold-reasons',
        'worker-monitored-threshold','worker-fast-context','worker-battery-response','worker-daily-review',
        'worker-charging-context','worker-virtual-thresholds','worker-adaptive-hysteresis','worker-telemetry-provenance'];
    const run = async (latest) => {
        const elements = Object.fromEntries(ids.map(id => [id, element()]));
        let start;
        const document = {getElementById: id => elements[id], createElement: element,
            querySelectorAll: () => [], addEventListener: (_, fn) => {start=fn;}};
        const fetch = async url => url.includes('/battery-observations')
            ? ({ok:true,json:async()=>({observations:[],battery_behavior:[],has_more:false})})
            : ({ok:true, json:async () => ({worker_health:{enabled:true, status:'degraded'}, latest_decision:latest, decisions:[latest]})});
        vm.runInNewContext(source, {document, fetch, JSON, String, Number, Object, Array, Math});
        start(); await tick();
        return elements;
    };
    const elements = await run({evaluated_at:'2026-11-06T15:00:00Z', decision:'HOLD', confidence:null,
        monitored_threshold:{key:'battery',label:'Back to Battery'}, current_settings:{battery:null},
        battery_v:25.7,charge_a:2.0,discharge_a:8.5,
        historical_settings:{battery:26.0},
        telemetry_audit:{device_measurement_timestamp_available_in_inverter_metrics:false},
        daily_behavior_review:{status:'REVIEWED',review_date_utc:'2026-11-06',collection_count:20,
            median_voltage_slope_change_v_per_hour:.1,learning_state_reset:false},
        virtual_threshold_experiment:{status:'VIRTUAL_REPLAY_ONLY_BLOCKED_FROM_RECOMMENDATION',
            blockers:['BMS_AND_MANUFACTURER_LIMITS_UNKNOWN'], candidates:[{back_to_utility_v:25.0,
                back_to_battery_v:26.0,sampled_utility_boundary_crossings:1,sampled_battery_boundary_crossings:0},
                {back_to_utility_v:24.5,back_to_battery_v:25.5,
                    sampled_utility_boundary_crossings:0,sampled_battery_boundary_crossings:1}]},
        adaptive_hysteresis:{status:'VIRTUAL WIDTHS ONLY; RECOMMENDATION BLOCKED',virtual_hysteresis_widths_v:[1.0]},
        charging_context:{evidence_labels:['EXTERNAL_CHARGING_POSSIBLE','TOTAL_BATTERY_CURRENT_UNOBSERVED',
                'CHARGING_RELATIONSHIP_UNVERIFIED'],external_charging_status:'POSSIBLE; NOT DETECTED AT A SPECIFIC MOMENT',
            pv_source_coverage:'EASUN_VISIBLE_PV_ONLY; OTHER_ORIENTATIONS_UNOBSERVED',
            voltage_rises_without_easun_charge_reported:2},
        battery_response:{capacity_status:'CAPACITY_NOT_IDENTIFIABLE',throughput_status:'INSUFFICIENT_MEASUREMENT_COVERAGE',latest_voltage_slope_v_per_hour:-.2,
            charged_ah_proxy:.3,discharged_ah_proxy:.2,freshness:'COLLECTION_RECENT; DEVICE_FRESHNESS_UNVERIFIED',
            confidence:'LOW_UNVERIFIED_SOURCE',recent_intervals:[{charge_current_delta_a_reported:1.5,
                discharge_current_delta_a_reported:2.0}]},
        reason_codes:['CYCLING_DETECTED','LINE_MODE_CHARGING_CONFOUNDS_VOLTAGE'],
        confidence_breakdown:{basis:'NO_VOLTAGE_ESTIMATE', cycling_detection:0.97, voltage_contrast:null},
        exploratory_voltage_contrast:{contrast_v:null, pair_rejections:{TIME_GAP_EXCEEDED:3, LOAD_NOT_COMPARABLE:0},
            primary_exclusions:{LINE_MODE_CHARGING_CONFOUNDED:56},
            charging_confounded_contrast:{median_difference_v:0.35, matched_pairs:43, median_line_charge_a:16.2}},
        decision_diagnostics:{
            fast_context:{reported_mode_changes:2,distinct_consecutive_payloads:4,maximum_reported_observations:6,
                pv_range_w:[100,800],load_range_w:[500,1200],latest_reported_current_direction:'DISCHARGE_REPORTED',
                latest_collection_age_seconds:120,collection_recent:true},
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
    assert.match(elements['worker-telemetry-provenance'].textContent, /device timestamp not persisted/);
    assert.match(elements['worker-daily-review'].textContent, /learning state preserved/);
    assert.match(elements['worker-virtual-thresholds'].textContent, /all non-actionable/);
    assert.match(elements['worker-virtual-thresholds'].textContent, /no winner/);
    assert.match(elements['worker-virtual-thresholds'].textContent, /25\/26 V/);
    assert.match(elements['worker-virtual-thresholds'].textContent, /24\.5\/25\.5 V/);
    assert.match(elements['decision-body'].children[0].children[4].textContent, /2 exploratory pairs; no preferred pair/);
    assert.doesNotMatch(elements['decision-body'].children[0].children[4].textContent, /ranked replay/i);
    assert.match(elements['worker-adaptive-hysteresis'].textContent, /no recommendation/);
    assert.match(elements['worker-charging-context'].textContent, /EXTERNAL_CHARGING_POSSIBLE/);
    assert.match(elements['worker-charging-context'].textContent, /NOT DETECTED AT A SPECIFIC MOMENT/);
    assert.match(elements['worker-battery-response'].textContent, /battery 25.7 V/);
    assert.match(elements['worker-battery-response'].textContent, /reported charge\/discharge 2\/8.5 A/);
    assert.match(elements['worker-battery-response'].textContent, /latest current deltas charge\/discharge 1.5\/2 A/);
    assert.match(elements['worker-fast-context'].textContent, /load range 500–1200 W/);
    assert.match(elements['worker-fast-context'].textContent, /DISCHARGE_REPORTED/);

    const legacy = await run({evaluated_at:'2026-11-06T14:00:00Z', decision:'HOLD', confidence:0.45, reason_codes:['COLLECTION_TIME_ONLY']});
    assert.equal(legacy['worker-cycling'].textContent, 'Not recorded by this algorithm version');
    assert.match(legacy['worker-confidence'].textContent, /^0.45 — COLLECTION_TIME_ONLY/);
});
test('Load more appends a cursor page and changing range resets the cursor', async () => {
    const ids = ['decision-status','decision-body','decision-export','decision-load-more','battery-grid-status','battery-observation-body',
        'battery-behavior-body','battery-load-more','worker-status',
        'worker-last-evaluation','worker-last-success','worker-reason','worker-window','worker-evidence',
        'worker-current','worker-baseline','worker-proposed','worker-stability','worker-confidence',
        'worker-cycling','worker-voltage','worker-confounded','worker-states','worker-samples',
        'worker-hypothesis','worker-hold-reasons'];
    const elements = Object.fromEntries(ids.map(id => [id, element()]));
    const buttons = [1,2,7].map(days => ({dataset:{decisionDays:String(days)}, addEventListener(_, fn){this.click=fn;}}));
    let start; const urls=[];
    const document={getElementById:id=>elements[id],createElement:element,querySelectorAll:()=>buttons,addEventListener:(_,fn)=>{start=fn;}};
    const item=(id,time)=>({evaluation_id:id,evaluated_at:time,decision:'HOLD',reason_codes:[],hardware_write_attempted:false});
    const fetch=async url=>{
        urls.push(url); if(url.includes('/battery-observations'))return {ok:true,json:async()=>({observations:[],battery_behavior:[],has_more:false})};
        const page=urls.filter(x=>x.includes('/api/controls/decisions?')&&x.includes('limit=50')).length;
        const decisions=url.includes('days=2')?[item('newer-range-a','2026-01-03'),item('newer-range-b','2026-01-02')]:page===1?[item('new','2026-01-03'),item('older','2026-01-02')]:[item('oldest','2026-01-01')];
        return {ok:true,json:async()=>({decisions,worker_health:{enabled:false,status:'disabled'},has_more:page===1,next_cursor:page===1?'cursor-2':null})};
    };
    vm.runInNewContext(source,{document,fetch,JSON,String,Number,Object,Array,Math});
    start(); await tick();
    assert.equal(elements['decision-body'].children.length,2);
    elements['decision-load-more'].click(); await tick();
    assert.equal(elements['decision-body'].children.length,3);
    assert.ok(urls.some(url=>url.includes('/api/controls/decisions?')&&/cursor=cursor-2/.test(url)));
    buttons[1].click(); await tick();
    assert.equal(elements['decision-body'].children.length,2);
    assert.ok(urls.some(url=>url.includes('/api/controls/decisions?days=2&limit=50')));
    assert.ok(urls.some(url=>url.includes('/api/controls/decisions?days=2&limit=50')&&!url.includes('cursor=')));
});
test('failed Load more keeps its cursor usable and refreshes worker health on success', async () => {
    const ids=['decision-status','decision-body','decision-export','decision-load-more','battery-grid-status','battery-observation-body',
        'battery-behavior-body','battery-load-more','worker-status',
        'worker-last-evaluation','worker-last-success','worker-reason','worker-window','worker-evidence',
        'worker-current','worker-baseline','worker-proposed','worker-stability','worker-confidence',
        'worker-cycling','worker-voltage','worker-confounded','worker-states','worker-samples','worker-hypothesis','worker-hold-reasons'];
    const elements=Object.fromEntries(ids.map(id=>[id,element()]));let start;const urls=[];let calls=0;
    const document={getElementById:id=>elements[id],createElement:element,querySelectorAll:()=>[],addEventListener:(_,fn)=>{start=fn;}};
    const fetch=async url=>{urls.push(url);if(url.includes('/battery-observations'))return {ok:true,json:async()=>({observations:[],battery_behavior:[],has_more:false})};calls++;if(calls===2)return {ok:false,status:503};return {ok:true,json:async()=>({
        decisions:[{evaluation_id:'r'+calls,evaluated_at:'2026-01-01T00:00:00Z',decision:'HOLD',evidence_level:'OLD'}],
        latest_decision:{evaluated_at:'2026-01-02T00:00:00Z',evidence_level:'CURRENT-'+calls},
        worker_health:{enabled:true,status:calls===1?'healthy':'degraded',last_evaluation_at:'latest-'+calls},
        has_more:calls===1,next_cursor:calls===1?'stable-cursor':null})};};
    vm.runInNewContext(source,{document,fetch,JSON,String,Number,Object,Array,Math});start();await tick();
    elements['decision-load-more'].click();await tick();
    assert.equal(elements['decision-body'].children.length,1);
    assert.equal(elements['decision-load-more'].disabled,false);
    assert.match(elements['decision-status'].textContent,/Retry/);
    assert.equal(elements['worker-status'].textContent,'HEALTHY (enabled)');
    elements['decision-load-more'].click();await tick();
    assert.ok(urls.some(url=>url.includes('/api/controls/decisions?')&&/cursor=stable-cursor/.test(url)));
    assert.equal(elements['worker-status'].textContent,'DEGRADED (enabled)');
    assert.equal(elements['worker-last-evaluation'].textContent,'latest-3');
    assert.equal(elements['worker-evidence'].textContent,'CURRENT-3');
});
test('observation and behavior grids paginate independently and identify collection provenance', async () => {
    const ids=['decision-status','decision-body','decision-export','decision-load-more','battery-grid-status',
        'battery-observation-body','battery-behavior-body','battery-load-more','worker-status','worker-last-evaluation',
        'worker-last-success','worker-reason','worker-window','worker-evidence','worker-current','worker-baseline',
        'worker-proposed','worker-stability','worker-confidence','worker-cycling','worker-voltage','worker-confounded',
        'worker-states','worker-samples','worker-hypothesis','worker-hold-reasons','worker-charging-context'];
    const elements=Object.fromEntries(ids.map(id=>[id,element()]));
    const buttons=[1,2,7].map(value=>({dataset:{decisionDays:String(value)},addEventListener(_,fn){this.click=fn;}}));
    let start;const urls=[];
    const document={getElementById:id=>elements[id],createElement:element,querySelectorAll:()=>buttons,addEventListener:(_,fn)=>{start=fn;}};
    const observation=(time,quality)=>({collection_time:time,mode:'Line Mode',battery_voltage_v:25.6,
        easun_charge_current_a_reported:0,easun_discharge_current_a_reported:0,pv_power_w_reported:500,
        load_power_w_reported:700,voltage_change_since_previous_valid_v:.1,interval_seconds:300,
        external_charging_status:'NO_EASUN_CURRENT_REPORTED; EXTERNAL_CHARGING_POSSIBLE; TOTAL_BATTERY_CURRENT_UNOBSERVED',
        data_provenance:'COLLECTION_TIME_ONLY; DEVICE_MEASUREMENT_TIME_UNVERIFIED',data_quality:quality,missing_fields:[]});
    const behavior={interval_end_collection_time:'2026-01-02T00:00:00Z',operating_context:'Line Mode; PV 500 W; load 700 W',
        voltage_slope_v_per_hour:.2,observed_easun_charge_a_reported:0,observed_easun_discharge_a_reported:0,
        load_w_reported:700,pv_delta_w_reported:100,estimated_voltage_response:'VOLTAGE_RISE_OBSERVED; EXTERNAL_CHARGING_POSSIBLE',
        charging_interpretation:'NO_EASUN_CURRENT_REPORTED; EXTERNAL_CHARGING_POSSIBLE',
        capacity_or_response_proxy:'CAPACITY_NOT_IDENTIFIABLE; TOTAL BATTERY CURRENT UNOBSERVED',
        confidence:'LOW_UNVERIFIED_SOURCE',freshness:'COLLECTION_RECENT; DEVICE_FRESHNESS_UNVERIFIED',
        limitation:'Collection time only.'};
    const fetch=async url=>{
        urls.push(url);
        if(url.includes('/battery-observations')){
            if(url.includes('days=7'))return {ok:true,json:async()=>({observations:[observation('2026-01-01T00:00:00Z','PAYLOAD_CHANGED')],battery_behavior:[],has_more:false,next_cursor:null})};
            if(url.includes('cursor=battery-next'))return {ok:true,json:async()=>({observations:[observation('2026-01-01T00:05:00Z','REPEATED_CACHED_PAYLOAD')],battery_behavior:[],has_more:false,next_cursor:null})};
            return {ok:true,json:async()=>({observations:[observation('2026-01-02T00:00:00Z','PAYLOAD_CHANGED')],battery_behavior:[behavior],
                external_charging_status:'POSSIBLE; NOT DETECTED AT A SPECIFIC MOMENT',has_more:true,next_cursor:'battery-next'})};
        }
        return {ok:true,json:async()=>({decisions:[],worker_health:{enabled:false,status:'disabled'},has_more:false,next_cursor:null})};
    };
    vm.runInNewContext(source,{document,fetch,JSON,String,Number,Object,Array,Math});start();await tick();
    assert.equal(elements['battery-observation-body'].children.length,1);
    assert.equal(elements['battery-behavior-body'].children.length,1);
    assert.equal(elements['battery-observation-body'].children[0].children[0].textContent,'RECORDED_OBSERVATION');
    assert.equal(elements['battery-behavior-body'].children[0].children[0].textContent,'DERIVED_ESTIMATE');
    assert.match(elements['battery-observation-body'].children[0].children[11].textContent,/EXTERNAL_CHARGING_POSSIBLE/);
    assert.match(elements['battery-behavior-body'].children[0].children[9].textContent,/CAPACITY_NOT_IDENTIFIABLE/);
    // Unavailable throughput is stated, never rendered as "undefined" or "null".
    assert.match(elements['battery-behavior-body'].children[0].children[9].textContent,/EASUN Ah \+\/− unavailable\/unavailable/);
    assert.equal(elements['battery-observation-body'].children[0].children[10].textContent,'5 min');
    elements['battery-load-more'].click();await tick();
    assert.ok(urls.some(url=>url.includes('/api/controls/battery-observations?')&&/cursor=battery-next/.test(url)));
    assert.equal(elements['battery-observation-body'].children.length,2);
    assert.match(elements['battery-observation-body'].children[1].children[13].textContent,/REPEATED_CACHED_PAYLOAD/);
    buttons[2].click();await tick();
    assert.equal(elements['battery-observation-body'].children.length,1);
    assert.ok(urls.some(url=>url.includes('/api/controls/battery-observations?days=7&limit=50')));
});
test('initial battery-history failure exposes retry and does not pretend the grid is empty', async () => {
    const ids=['decision-status','decision-body','decision-export','decision-load-more','battery-grid-status',
        'battery-observation-body','battery-behavior-body','battery-load-more'];
    const elements=Object.fromEntries(ids.map(id=>[id,element()]));let start;let attempts=0;
    const document={getElementById:id=>elements[id],createElement:element,querySelectorAll:()=>[],addEventListener:(_,fn)=>{start=fn;}};
    const fetch=async url=>{
        if(url.includes('/battery-observations')){
            attempts++;if(attempts===1)return {ok:false,status:503};
            return {ok:true,json:async()=>({observations:[{collection_time:'2026-01-01T00:00:00Z',data_quality:'PAYLOAD_CHANGED'}],
                battery_behavior:[],has_more:false,next_cursor:null})};
        }
        return {ok:true,json:async()=>({decisions:[],worker_health:{enabled:false,status:'disabled'},has_more:false})};
    };
    vm.runInNewContext(source,{document,fetch,JSON,String,Number,Object,Array,Math});start();await tick();
    assert.match(elements['battery-grid-status'].textContent,/results were cleared/);
    assert.equal(elements['battery-observation-body'].children.length,0);
    assert.equal(elements['battery-load-more'].hidden,false);
    assert.equal(elements['battery-load-more'].textContent,'Retry observations');
    elements['battery-load-more'].click();await tick();
    assert.equal(elements['battery-observation-body'].children.length,1);
    assert.equal(elements['battery-load-more'].hidden,true);
});
