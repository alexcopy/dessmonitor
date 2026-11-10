(function () {
    'use strict';
    var days = 1;
    var nextCursor = null;
    var loading = false;
    var generation = 0;
    var seen = new Set();
    var pendingReset = false;
    var batteryCursor = null;
    var batteryLoading = false;
    var batteryGeneration = 0;
    var batterySeenObservations = new Set();
    var batterySeenBehavior = new Set();
    var batteryPendingReset = false;
    function setText(id, value) {
        var node = document.getElementById(id);
        if (node) node.textContent = value == null || value === '' ? '—' : String(value);
    }
    function duration(seconds) {
        if (seconds == null) return '—';
        return Math.max(0, Math.ceil(Number(seconds) / 60)) + ' min';
    }
    function currentThreshold(item) {
        var settings = item && item.current_settings;
        var key = item && item.monitored_threshold && item.monitored_threshold.key || 'battery';
        var value = settings ? settings[key] : null;
        return value == null || settings.provenance !== 'VERIFIED_DEVICE_READBACK'
            ? 'UNKNOWN (not verified)' : String(value) + ' V (verified read-back)';
    }
    function historicalReference(item) {
        var settings = item && item.historical_settings;
        var key = item && item.monitored_threshold && item.monitored_threshold.key || 'battery';
        var value = settings ? settings[key] : null;
        return value == null ? 'Unavailable' : String(value) + ' V (historical; not current)';
    }
    // Same rule as app/battery_intervals.py battery_voltage_status: only a finite
    // number strictly above 0 V is a battery measurement.
    function voltageStatus(raw) {
        if (raw == null) return 'MISSING';
        if (typeof raw === 'string' && /^(NaN|-?Infinity)$/.test(raw)) return 'INVALID_NON_FINITE_VOLTAGE';
        if (typeof raw !== 'number') return 'INVALID_NON_NUMERIC_VOLTAGE';
        if (!isFinite(raw)) return 'INVALID_NON_FINITE_VOLTAGE';
        if (raw === 0) return 'INVALID_ZERO_VOLTAGE';
        return raw < 0 ? 'INVALID_NEGATIVE_VOLTAGE' : 'VALID';
    }
    function reportedVoltage(raw) {
        if (typeof raw === 'number' && isFinite(raw)) return (Number.isInteger(raw) ? raw.toFixed(1) : String(raw)) + ' V';
        return JSON.stringify(raw);
    }
    // Text for a decision entry's latest battery voltage. Uses the API status when
    // present and re-derives it from battery_v for legacy entries, so a stored
    // 0 V is never shown as a measurement.
    function batteryVoltageText(item) {
        if (!item || typeof item !== 'object' || (!('battery_v' in item) && !('battery_v_status' in item))) return 'Unavailable';
        var hasStatus = typeof item.battery_v_status === 'string';
        var raw = hasStatus ? item.battery_v_reported_raw : item.battery_v;
        var status = hasStatus ? item.battery_v_status : voltageStatus(item.battery_v);
        if (status === 'VALID' && voltageStatus(item.battery_v) !== 'VALID') {
            raw = item.battery_v; status = voltageStatus(raw);
        }
        if (status === 'VALID') return item.battery_v + ' V';
        if (status === 'MISSING') return 'Unavailable (not reported)';
        return 'Invalid (reported ' + reportedVoltage(raw) + '; ' + status + '; excluded from calculations)';
    }
    function monitoredThreshold(item) {
        return item && item.monitored_threshold && item.monitored_threshold.label || 'Back to Battery';
    }
    function renderHealth(data) {
        var health = data.worker_health || {};
        var latest = data.latest_decision || null;
        setText('worker-status', (health.status || 'degraded').toUpperCase() +
            (health.enabled ? ' (enabled)' : ' (disabled)'));
        setText('worker-last-evaluation', health.last_evaluation_at);
        setText('worker-last-success', health.last_successful_evaluation_at);
        setText('worker-reason', health.last_error || health.blocking_reason || 'No persisted error or blocking reason');
        var count = health.observation_count != null ? health.observation_count : latest && latest.unique_observation_count;
        var collections = health.collection_count != null ? health.collection_count : latest && latest.collection_count;
        var windowSeconds = health.effective_window_seconds != null ? health.effective_window_seconds : latest && latest.effective_window_seconds;
        setText('worker-window', count == null ? '—' : count + ' unique observations / ' +
            (collections == null ? '—' : collections + ' collections') + ' / ' + duration(windowSeconds));
        setText('worker-evidence', latest ? latest.evidence_level : health.evidence_level);
        setText('worker-current', currentThreshold(latest));
        setText('worker-baseline', historicalReference(latest));
        setText('worker-monitored-threshold', monitoredThreshold(latest));
        var behavior = latest && latest.battery_response && typeof latest.battery_response === 'object'
            ? latest.battery_response : null;
        var intervals = behavior && Array.isArray(behavior.recent_intervals) ? behavior.recent_intervals : [];
        var lastInterval = intervals.length ? intervals[intervals.length - 1] : null;
        // Entry-level telemetry is shown whenever an entry exists, independently of
        // battery_response (legacy entries may lack it); same formatter as history rows.
        var latestTelemetry = latest ? 'battery ' + batteryVoltageText(latest) + '; reported charge/discharge ' +
            (latest.charge_a == null ? '—' : latest.charge_a) + '/' +
            (latest.discharge_a == null ? '—' : latest.discharge_a) + ' A' : null;
        setText('worker-battery-response', !latest ? 'Not recorded' : !behavior
            ? latestTelemetry + '; battery response diagnostics not recorded'
            : behavior.capacity_status + '; ' + latestTelemetry + '; dV/dt ' +
            (behavior.latest_voltage_slope_v_per_hour == null ? 'unknown' : behavior.latest_voltage_slope_v_per_hour + ' V/h') +
            (lastInterval ? '; latest current deltas charge/discharge ' +
                lastInterval.charge_current_delta_a_reported + '/' + lastInterval.discharge_current_delta_a_reported + ' A' : '') +
            '; throughput ' + behavior.throughput_status + ': +' +
            (behavior.charged_ah_proxy == null ? 'unavailable' : behavior.charged_ah_proxy + ' Ah') + ' / −' +
            (behavior.discharged_ah_proxy == null ? 'unavailable' : behavior.discharged_ah_proxy + ' Ah') +
            ' (reported proxy); ' + behavior.freshness + '; ' + behavior.confidence);
        var charging = latest && latest.charging_context;
        setText('worker-charging-context', charging ? (charging.evidence_labels || []).join(', ') +
            '; external charging ' + charging.external_charging_status +
            '; EASUN current is not total battery current; PV coverage: ' + charging.pv_source_coverage +
            '; observed voltage rises without EASUN charge: ' + charging.voltage_rises_without_easun_charge_reported : 'Not recorded');
        var fast = latest && latest.decision_diagnostics && latest.decision_diagnostics.fast_context;
        setText('worker-fast-context', fast ? fast.reported_mode_changes + ' reported mode changes / ' +
            fast.distinct_consecutive_payloads + ' distinct payloads (max ' + fast.maximum_reported_observations +
            ') in 20 min; PV range ' + (fast.pv_range_w ? fast.pv_range_w.join('–') + ' W' : 'unknown') +
            '; load range ' + (fast.load_range_w ? fast.load_range_w.join('–') + ' W' : 'unknown') +
            '; reported current direction ' + (fast.latest_reported_current_direction || 'unknown') +
            '; latest collection ' + duration(fast.latest_collection_age_seconds) + ' old; ' +
            (fast.collection_recent ? 'recent collection' : 'stale or unavailable') +
            '; device freshness not verified' : 'Not recorded');
        var daily = latest && latest.daily_behavior_review;
        setText('worker-daily-review', daily ? daily.status + ' for ' + (daily.review_date_utc || 'latest review') +
            '; ' + (daily.collection_count == null ? '—' : daily.collection_count) +
            ' collections in 24 h; recent-vs-prior median dV/dt change ' +
            (daily.median_voltage_slope_change_v_per_hour == null ? 'unavailable' :
                daily.median_voltage_slope_change_v_per_hour + ' V/h') + '; ' +
            (daily.learning_state_reset ? 'learning state reset' : 'learning state preserved') : 'Waiting for daily review');
        var experiment = latest && latest.virtual_threshold_experiment;
        var candidates = experiment && Array.isArray(experiment.candidates) ? experiment.candidates : [];
        setText('worker-virtual-thresholds', experiment ? experiment.status + '; ' + candidates.length +
            ' exploratory virtual pairs; no winner; all non-actionable. ' + candidates.map(function (candidate) {
                return candidate.back_to_utility_v + '/' + candidate.back_to_battery_v +
                    ' V (sampled crossings ' + ((candidate.sampled_utility_boundary_crossings == null ||
                    candidate.sampled_battery_boundary_crossings == null) ? 'unavailable' :
                    candidate.sampled_utility_boundary_crossings + '/' + candidate.sampled_battery_boundary_crossings) + ')';
            }).join('; ') + (experiment.blockers && experiment.blockers.length ? '; blocked: ' + experiment.blockers.join(', ') : '')
            : 'Not recorded');
        var hysteresis = latest && latest.adaptive_hysteresis;
        setText('worker-adaptive-hysteresis', hysteresis ? hysteresis.status + '; live width unknown; virtual widths ' +
            ((hysteresis.virtual_hysteresis_widths_v || []).join(', ') || 'unavailable') + ' V; no recommendation' : 'Not recorded');
        var audit = latest && latest.telemetry_audit;
        setText('worker-telemetry-provenance', audit ?
            'Collection time only; device timestamp not persisted; reported current semantics and SOC accuracy unverified; battery temperature, external charger current, controller identity, and array orientation unavailable. PV-side power and AC-output power are separate reports.' : 'Not recorded');
        var proposed = latest && latest.proposed_voltage != null ? latest.proposed_voltage : latest && latest.candidate_voltage;
        var proposalLabel = latest && latest.proposed_voltage != null ? 'proposed' : 'candidate';
        setText('worker-proposed', proposed != null
            ? (latest.threshold || 'threshold') + ' ' + proposalLabel + ': ' + proposed + ' V (hypothetical; non-actionable)'
            : 'No proposal');
        var progress = latest && latest.confirmation_progress;
        var confirmation = progress ? progress.confirmed + ' / ' + progress.required + ' observations; ' + (latest.stability || 'unknown') : '—';
        var cooldown = latest ? latest.cooldown_remaining_seconds : health.cooldown_remaining_seconds;
        setText('worker-stability', confirmation + '; ' + duration(cooldown) + ' cooldown remaining');
        var reasons = latest && Array.isArray(latest.reason_codes) ? latest.reason_codes.join(', ') : '';
        var breakdown = latest && latest.confidence_breakdown;
        var confidence;
        if (breakdown) {
            confidence = 'voltage contrast ' + (breakdown.voltage_contrast == null ? 'none (no estimate)' : breakdown.voltage_contrast) +
                '; cycling detection ' + (breakdown.cycling_detection == null ? '—' : breakdown.cycling_detection) +
                '; basis ' + (breakdown.basis || '—');
        } else {
            var value = latest && latest.confidence != null ? latest.confidence : health.confidence;
            confidence = value == null ? '—' : String(value);
        }
        setText('worker-confidence', confidence + (reasons ? ' — ' + reasons : ''));
        renderDiagnostics(latest);
    }
    function joinCounts(counts) {
        if (!counts || typeof counts !== 'object') return '';
        return Object.keys(counts).filter(function (key) { return counts[key]; })
            .map(function (key) { return key + ' ' + counts[key]; }).join(', ');
    }
    function renderDiagnostics(latest) {
        var diagnostics = latest && latest.decision_diagnostics;
        var ids = ['worker-cycling', 'worker-voltage', 'worker-confounded', 'worker-states',
            'worker-samples', 'worker-hypothesis', 'worker-hold-reasons'];
        if (!diagnostics || typeof diagnostics !== 'object') {
            ids.forEach(function (id) { setText(id, 'Not recorded by this algorithm version'); });
            return;
        }
        var cycling = diagnostics.cycling || {};
        var modeCoverage = diagnostics.mode_history_coverage || {};
        var quantCoverage = diagnostics.quantitative_history_coverage || {};
        setText('worker-cycling', (cycling.detected ? 'DETECTED' : 'Not established') + ': ' +
            cycling.reported_mode_changes + ' reported mode changes, ' + cycling.short_runs + ' of ' +
            cycling.complete_runs + ' bounded runs shorter than ' +
            Math.round(((cycling.criteria || {}).short_run_max_seconds || 0) / 60) + ' min; detection confidence ' +
            cycling.detection_confidence + '; ' + joinCounts(cycling.transition_states) +
            ' (collection order only; physical switch times unknown)' +
            (modeCoverage.truncated ? '; MODE HISTORY TRUNCATED: only the newest ' + modeCoverage.limit_runs +
                ' runs retained (' + modeCoverage.omitted_older_runs + ' older runs omitted); no transition counted across the gap' : '') +
            (modeCoverage.observation_gaps ? '; HISTORY INTERRUPTED: ' + modeCoverage.observation_gaps +
                ' observation gap(s) over ' + Math.round((modeCoverage.observation_gap_limit_s || 600) / 60) +
                ' min (longest ' + Math.round((modeCoverage.max_observation_gap_s || 0) / 60) + ' min); ' +
                (modeCoverage.uncertain_mode_changes_across_gaps || 0) +
                ' uncertain mode change(s) across unobserved intervals, not counted as transitions' : ''));
        var voltage = diagnostics.voltage_comparison || {};
        var contrast = latest.exploratory_voltage_contrast || {};
        var voltageText = voltage.status === 'ESTIMATED'
            ? 'Estimated (exploratory): ' + contrast.contrast_v + ' V Line-not-charging minus Invert-discharging from ' +
                voltage.comparable_pairs + ' pairs; not verified sag'
            : 'Blocked: ' + (voltage.blocked_by || []).join(', ') + ' (' + (voltage.comparable_pairs || 0) +
                ' of ' + voltage.required_pairs + ' required pairs)';
        var rejections = joinCounts(contrast.pair_rejections);
        var exclusions = joinCounts(contrast.primary_exclusions);
        setText('worker-voltage', voltageText + (exclusions ? '; excluded observations: ' + exclusions : '') +
            (rejections ? '; rejected comparisons: ' + rejections : ''));
        var confounded = contrast.charging_confounded_contrast;
        setText('worker-confounded', confounded && confounded.median_difference_v != null
            ? 'Median ' + confounded.median_difference_v + ' V over ' + confounded.matched_pairs +
                ' pairs (Line charging at median ' + confounded.median_line_charge_a +
                ' A minus Invert discharging) — charging-confounded description, not sag or rebound; never used for proposals'
            : 'No matched Line-charging comparison');
        var classes = diagnostics.evidence_classes || {};
        setText('worker-states', Object.keys(classes).map(function (key) {
            var item = classes[key] || {};
            var range = Array.isArray(item.voltage_range_v) ? item.voltage_range_v.join('–') + ' V' : '—';
            return key + ': ' + item.count + ' (' + range + ')';
        }).join('; ') || 'No complete observations');
        var samples = diagnostics.sample_counts || {};
        setText('worker-samples', samples.analysed_observations + ' analysed of ' + samples.raw_collections +
            ' collections; rejected: ' + (joinCounts({
                'outside window': samples.rejected_outside_window_or_invalid_time,
                'unknown mode': samples.rejected_unknown_mode,
                'missing/invalid fields': samples.rejected_missing_or_invalid_numeric,
                'duplicate cached payload': samples.rejected_duplicate_cached_payload,
                'raw row bound': samples.rejected_raw_row_bound,
                'analysis cap': samples.rejected_analysis_cap}) || 'none') +
            (quantCoverage.raw_row_truncated
                ? '; HISTORY TRUNCATED: only the newest ' + quantCoverage.raw_row_limit +
                    ' raw rows analysed; older observations omitted (partial three-hour window)'
                : ''));
        var hypothesis = diagnostics.hypothesis_support || {};
        setText('worker-hypothesis', hypothesis.supported
            ? 'Supported: ' + hypothesis.candidate + ' (hypothetical; non-actionable)'
            : 'Not supported: ' + ((hypothesis.unsupported_because || []).join(', ') || 'no candidate') +
                '. Frequent switching alone is not threshold evidence.');
        setText('worker-hold-reasons', (diagnostics.hold_reasons || []).join(', ') || 'None');
    }
    function renderDecision(item) {
        var tr = document.createElement('tr');
        var historical = item.historical_settings || {};
        var experiment = item.virtual_threshold_experiment || {};
        var virtualPairs = Array.isArray(experiment.candidates) ? experiment.candidates : [];
        var layers = ['RECORDED_OBSERVATION'];
        if (item.battery_response || item.exploratory_voltage_contrast) layers.push('DERIVED_ESTIMATE');
        if (virtualPairs.length) layers.push('VIRTUAL_REPLAY');
        if (item.candidate_voltage != null) layers.push('HYPOTHETICAL_CANDIDATE');
        else layers.push('BLOCKED_INSUFFICIENT_EVIDENCE');
        var hypotheticalPair = item.candidate_voltage != null
            ? 'BU ' + (historical.utility == null ? '25.5' : historical.utility) + ' / BB ' + item.candidate_voltage + ' V (candidate)'
            : virtualPairs.length ? virtualPairs.length + ' exploratory pairs; no preferred pair'
                : 'None; blocked';
        var response = item.battery_response || {};
        var batteryEvidence = 'Battery ' + batteryVoltageText(item) + '; EASUN charge/discharge ' +
            (item.charge_a == null ? '—' : item.charge_a) + '/' + (item.discharge_a == null ? '—' : item.discharge_a) +
            ' A; dV/dt ' + (response.latest_voltage_slope_v_per_hour == null ? '—' : response.latest_voltage_slope_v_per_hour + ' V/h') +
            '; EASUN Ah +/− ' + (response.charged_ah_proxy == null ? '—' : response.charged_ah_proxy) + '/' +
            (response.discharged_ah_proxy == null ? '—' : response.discharged_ah_proxy) + '; CAPACITY_NOT_IDENTIFIABLE';
        var contrast = item.exploratory_voltage_contrast || {};
        var recoveries = response.recent_intervals || [];
        var recoveryCount = recoveries.filter(function (interval) {
            return interval.voltage_recovery_with_reported_discharge_or_load_reduction;
        }).length;
        var cycling = item.recent_reported_mode_changes == null ? '—' : item.recent_reported_mode_changes + ' collection-order transitions';
        var external = (item.charging_context && item.charging_context.evidence_labels || [
            'EXTERNAL_CHARGING_POSSIBLE', 'TOTAL_BATTERY_CURRENT_UNOBSERVED', 'CHARGING_RELATIONSHIP_UNVERIFIED']).join(', ');
        var prediction = 'NOT_IDENTIFIABLE / UNKNOWN; no validated pair-selection method';
        var confidence = item.confidence == null ? '—' : item.confidence + ' (' +
            ((item.confidence_breakdown || {}).basis || item.evidence_level || 'unqualified') + ')';
        [layers.join(' / '), item.evaluated_at, item.decision,
            'BU ' + (historical.utility == null ? '25.5' : historical.utility) + ' / BB ' +
                (historical.battery == null ? 'UNKNOWN' : historical.battery) + ' V (historical); live settings UNKNOWN',
            hypotheticalPair, batteryEvidence, cycling,
            'Exploratory contrast ' + (contrast.contrast_v == null ? 'unavailable' : contrast.contrast_v + ' V') +
                '; not verified sag; recovery associations ' + recoveryCount,
            external, prediction, confidence, item.candidate_status || item.stability || 'BLOCKED_INSUFFICIENT_EVIDENCE']
            .forEach(function (value) {
                var td = document.createElement('td'); td.textContent = value == null ? '—' : String(value); tr.appendChild(td);
            });
        var reason = document.createElement('td'), detail = document.createElement('details');
        var summary = document.createElement('summary');
        summary.textContent = item.explanation || (Array.isArray(item.reason_codes) ? item.reason_codes.join(', ') : 'Evidence unavailable');
        var pre = document.createElement('pre');
        detail.addEventListener('toggle', function () {
            if (detail.open && !pre.dataset.generated) {
                pre.textContent = 'Latest battery voltage: ' + batteryVoltageText(item) + '\n' + JSON.stringify(item, null, 2);
                pre.dataset.generated = 'true';
            }
        });
        detail.appendChild(summary); detail.appendChild(pre); reason.appendChild(detail); tr.appendChild(reason);
        return tr;
    }

    function appendCells(row, values) {
        values.forEach(function (value) {
            var cell = document.createElement('td');
            cell.textContent = value == null || value === '' ? '—' : String(value);
            row.appendChild(cell);
        });
    }
    function batteryInterval(seconds) {
        // Collection intervals can be sub-second; never round them up to a minute.
        if (seconds == null) return '—';
        var value = Number(seconds);
        return value < 60 ? value + ' s' : duration(value);
    }
    function renderBatteryObservation(item) {
        var row = document.createElement('tr');
        appendCells(row, [item.data_kind || 'RECORDED_OBSERVATION', item.collection_time, item.mode,
            item.battery_voltage_v != null ? item.battery_voltage_v
                : (item.invalid_fields || []).indexOf('battery_voltage') >= 0
                    ? 'Invalid (reported ' + reportedVoltage(item.battery_voltage_v_reported_raw) + '; excluded from calculations)'
                    : null,
            item.battery_soc_reported,
            item.easun_charge_current_a_reported, item.easun_discharge_current_a_reported,
            item.pv_power_w_reported, item.load_power_w_reported,
            item.voltage_change_since_previous_valid_v,
            item.interval_seconds == null ? (item.interval_status || null) : batteryInterval(item.interval_seconds),
            item.external_charging_status, item.data_provenance,
            (item.data_quality || '') + (item.missing_fields && item.missing_fields.length
                ? '; missing: ' + item.missing_fields.join(', ') : '')]);
        return row;
    }
    function renderBatteryBehavior(item) {
        var row = document.createElement('tr');
        appendCells(row, [item.data_kind || 'DERIVED_ESTIMATE', item.interval_end_collection_time, item.operating_context,
            item.voltage_slope_v_per_hour == null ? (item.slope_status || null) : item.voltage_slope_v_per_hour + ' V/h',
            'charge/discharge ' + item.observed_easun_charge_a_reported + '/' + item.observed_easun_discharge_a_reported + ' A',
            item.reported_soc_percent_unverified == null ? null : item.reported_soc_percent_unverified + ' % (unverified)',
            'load ' + item.load_w_reported + ' W; PV Δ ' + item.pv_delta_w_reported + ' W',
            item.estimated_voltage_response, item.charging_interpretation,
            item.capacity_or_response_proxy + '; EASUN Ah +/− ' +
                (item.easun_charge_ah_proxy == null ? 'unavailable' : item.easun_charge_ah_proxy) + '/' +
                (item.easun_discharge_ah_proxy == null ? 'unavailable' : item.easun_discharge_ah_proxy) +
                (item.easun_throughput_status && item.easun_throughput_status !== 'COMPLETE'
                    ? ' (' + item.easun_throughput_status + '; covered ' + batteryInterval(item.easun_throughput_covered_seconds) +
                      ' of ' + batteryInterval(item.easun_throughput_elapsed_seconds) +
                      ((item.easun_throughput_exclusions || []).length ? '; ' + item.easun_throughput_exclusions.join(', ') : '') + ')'
                    : ''),
            item.confidence, item.freshness, item.limitation]);
        return row;
    }
    async function loadBatteryHistory(reset) {
        var status = document.getElementById('battery-grid-status');
        var observationsBody = document.getElementById('battery-observation-body');
        var behaviorBody = document.getElementById('battery-behavior-body');
        if (batteryLoading) { if (reset) batteryPendingReset = true; return; }
        if (reset) {
            batteryGeneration++; batteryCursor = null;
            batterySeenObservations = new Set(); batterySeenBehavior = new Set();
            observationsBody.replaceChildren(); behaviorBody.replaceChildren();
            var resetButton = document.getElementById('battery-load-more');
            resetButton.hidden = true; resetButton.disabled = true;
        }
        var requested = days, requestGeneration = batteryGeneration, cursor = reset ? null : batteryCursor;
        batteryLoading = true;
        status.textContent = cursor ? 'Loading older battery observations…' : 'Loading battery observations…';
        try {
            var url = '/api/controls/battery-observations?days=' + days + '&limit=50' +
                (cursor ? '&cursor=' + encodeURIComponent(cursor) : '');
            var response = await fetch(url, {cache: 'no-store'});
            if (!response.ok) throw new Error('unavailable');
            var data = await response.json();
            if (requestGeneration !== batteryGeneration || requested !== days) return;
            if (!data || !Array.isArray(data.observations) || !Array.isArray(data.battery_behavior)) throw new Error('invalid');
            data.observations.forEach(function (item) {
                var key = String(item.collection_time || '');
                if (key && !batterySeenObservations.has(key)) {
                    batterySeenObservations.add(key); observationsBody.appendChild(renderBatteryObservation(item));
                }
            });
            data.battery_behavior.forEach(function (item) {
                var key = String(item.interval_end_collection_time || '');
                if (key && !batterySeenBehavior.has(key)) {
                    batterySeenBehavior.add(key); behaviorBody.appendChild(renderBatteryBehavior(item));
                }
            });
            batteryCursor = data.next_cursor || null;
            var more = document.getElementById('battery-load-more');
            more.hidden = !data.has_more; more.disabled = false; more.textContent = 'Load more observations';
            status.textContent = observationsBody.children.length
                ? observationsBody.children.length + ' collection records; ' + behaviorBody.children.length +
                    ' derived response intervals. ' + (data.external_charging_status || 'External charging remains unobserved.')
                : 'No battery observations in this period.';
        } catch (_) {
            if (requestGeneration === batteryGeneration && requested === days) {
                status.textContent = reset ? 'Could not load this range; observation results were cleared.'
                    : 'Battery history unavailable; previous rows retained. Retry Load more observations.';
                var moreButton = document.getElementById('battery-load-more');
                moreButton.hidden = false; moreButton.disabled = false;
                moreButton.textContent = reset || !batteryCursor ? 'Retry observations' : 'Retry load more observations';
            }
        } finally {
            batteryLoading = false;
            if (batteryPendingReset) { batteryPendingReset = false; loadBatteryHistory(true); }
        }
    }
    async function load(reset) {
        var status = document.getElementById('decision-status');
        var body = document.getElementById('decision-body');
        if (loading) { if (reset) pendingReset = true; return; }
        if (reset) { generation++; nextCursor = null; seen = new Set(); body.replaceChildren(); }
        var requested = days, requestGeneration = generation, cursor = reset ? null : nextCursor;
        loading = true;
        status.textContent = cursor ? 'Loading older decisions…' : 'Loading decisions…';
        document.getElementById('decision-export').href = '/api/controls/decisions/export?days=' + days;
        try {
            var url = '/api/controls/decisions?days=' + days + '&limit=50' + (cursor ? '&cursor=' + encodeURIComponent(cursor) : '');
            var response = await fetch(url, {cache: 'no-store'});
            if (!response.ok) throw new Error('unavailable');
            var data = await response.json();
            if (requestGeneration !== generation || requested !== days) return;
            if (!data || !Array.isArray(data.decisions)) throw new Error('invalid');
            renderHealth(data);
            data.decisions.forEach(function (item) {
                var key = String(item.evaluation_id || item.evaluated_at) + ':' + String(item.evaluated_at);
                if (!seen.has(key)) { seen.add(key); body.appendChild(renderDecision(item)); }
            });
            nextCursor = data.next_cursor || null;
            var more = document.getElementById('decision-load-more'); more.hidden = !data.has_more; more.disabled = false;
            status.textContent = body.children.length ? body.children.length + ' decisions loaded' : 'No decisions in this period.';
        } catch (_) {
            if (requestGeneration === generation && requested === days) {
                status.textContent = reset ? 'Could not load this range; previous results were cleared.' : 'Decision journal unavailable; previous results retained. Retry Load more.';
                var moreButton = document.getElementById('decision-load-more');
                if (!reset && nextCursor) moreButton.disabled = false;
            }
        } finally {
            loading = false;
            if (pendingReset) { pendingReset = false; load(true); }
        }
    }
    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('[data-decision-days]').forEach(function (button) {
            button.addEventListener('click', function () {
                days = Number(button.dataset.decisionDays); load(true); loadBatteryHistory(true);
            });
        });
        document.getElementById('decision-load-more').addEventListener('click', function () {
            this.disabled = true; load(false);
        });
        document.getElementById('battery-load-more').addEventListener('click', function () {
            this.disabled = true; loadBatteryHistory(false);
        });
        load(true);
        loadBatteryHistory(true);
    });
}());
