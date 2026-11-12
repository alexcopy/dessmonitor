(function () {
    'use strict';
    var days = 1;
    var nextCursor = null;
    var loading = false;
    var generation = 0;
    var seen = new Set();
    var pendingReset = false;
    var historyReasonOccurrences = Object.create(null);
    var historyExpanded = true;
    var deferredDecisionItems = [];
    var rawJournalEvents = [];
    var rawJournalEventsRendered = false;
    var batteryCursor = null;
    var batteryLoading = false;
    var batteryGeneration = 0;
    var batterySeenObservations = new Set();
    var batterySeenBehavior = new Set();
    var batteryPendingReset = false;
    var batteryLoadedDays = null;
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
    var reasonExplanations = {
        CYCLING_DETECTED: 'Frequent reported Line/Invert changes make a safe threshold change hard to assess.',
        LINE_MODE_CHARGING_CONFOUNDS_VOLTAGE: 'Charging in Line mode can raise voltage, so it cannot be compared as battery sag.',
        INSUFFICIENT_COMPARABLE_PAIRS: 'There are too few comparable observations to estimate battery voltage response.',
        INSUFFICIENT_HYPOTHESIS_EVIDENCE: 'The available evidence does not support a threshold recommendation.',
        NO_VOLTAGE_CONTRAST_ESTIMATE: 'No reliable voltage comparison is available.',
        EXPLORATORY_ANALYSIS_UNAVAILABLE: 'The exploratory analysis is unavailable.',
        DEVICE_TIMESTAMP_PROVENANCE_UNVERIFIED: 'Device measurement times have not been verified.',
        DEVICE_OBSERVATION_TIME_INVALID: 'Device observation times are invalid or unavailable.',
        VERIFIED_OBSERVATION_SOURCE_UNAVAILABLE: 'A verified observation source is unavailable.',
        VERIFIED_OBSERVATION_PROVENANCE_UNAVAILABLE: 'The observation source cannot be verified.',
        VERIFIED_SAMPLE_INVALID: 'The verified observation sample is invalid.',
        SAMPLE_SOURCE_NOT_VERIFIED: 'The observation source is not verified.',
        SAG_LEARNER_CONFIGURATION_UNAVAILABLE: 'The verified learning configuration is unavailable.',
        SAG_LEARNER_CONFIGURATION_INVALID: 'The verified learning configuration is invalid.',
        RECEIPT_TIME_UNAVAILABLE: 'Receipt times are unavailable.',
        BMS_LIMITS_UNKNOWN: 'Battery management limits are unknown.',
        CURRENT_THRESHOLD_SETTINGS_UNKNOWN: 'Current inverter threshold settings are unknown.',
        NO_RECENT_COLLECTIONS: 'There are no recent observations.',
        LATEST_COLLECTION_STALE: 'The latest observation is too old to rely on.',
        INVALID_BATTERY_VOLTAGE: 'The latest battery voltage is invalid.',
        WORKING_MODE_UNKNOWN: 'The latest inverter operating mode is unknown.',
        MODE_HISTORY_TRUNCATED: 'Some older operating mode history is unavailable.',
        MODE_HISTORY_OBSERVATION_GAP: 'There is a gap in the operating mode observations.',
        QUANTITATIVE_HISTORY_TRUNCATED: 'Some older measurements were omitted from the analysis.',
        COLLECTION_TIME_ONLY: 'Evidence uses collection times rather than verified device measurement times.',
        HISTORICAL_BASELINE_ONLY: 'The comparison uses a historical reference, not a current setting.',
        HYPOTHESIS_NOT_SAFETY_QUALIFIED: 'The hypothesis has not passed safety qualification.',
        LATEST_OBSERVATION_NOT_COMPARABLE: 'The latest observation cannot be compared reliably.',
        HISTORICAL_BASELINE_UNKNOWN: 'The historical reference voltage is unknown.',
        HISTORICAL_BASELINE_OUT_OF_RANGE: 'The historical reference voltage is outside the supported range.',
        CATALOGUE_OR_HYSTERESIS_BLOCK: 'Inverter catalogue or switching limits prevent a safe comparison.',
        COOLDOWN_STATE_UNVERIFIED: 'The cooldown state cannot be verified.',
        DRY_RUN_COOLDOWN: 'A dry-run cooldown is active.',
        PROPOSAL_AWAITING_CONFIRMATION: 'The hypothetical proposal requires more confirmation.',
        PROPOSAL_AWAITING_COMPATIBLE_CONFIRMATION: 'A hypothetical proposal is waiting for compatible observations.',
        WORKER_EVALUATION_FAILED: 'The latest worker evaluation failed.',
        ADAPTIVE_OUTPUT_LEARNING_DISABLED: 'The adaptive learning worker is disabled.',
        NO_WORKER_HEARTBEAT: 'No worker heartbeat has been recorded.',
        NO_PERSISTED_WORKER_EVALUATION: 'No evaluation has been stored yet.',
        DATABASE_URL_UNAVAILABLE: 'The decision journal database is unavailable.',
        FEWER_THAN_6_UNIQUE_COMPLETE_OBSERVATIONS: 'There are fewer than six complete, unique observations.',
        LATEST_COLLECTION_INCOMPLETE_STALE_OR_CONFLICTING: 'The latest observation is incomplete, stale, or conflicting.',
        TOO_FEW_LINE_NOT_CHARGING_OBSERVATIONS: 'There are too few Line mode observations without reported charging.',
        TOO_FEW_INVERT_DISCHARGING_OBSERVATIONS: 'There are too few Invert mode observations with reported discharge.',
        PAIRS_DISAGREE_OUTLIERS_REJECTED: 'Comparable voltage pairs disagree or were rejected as outliers.'
    };
    function explainReason(code) {
        if (!code) return '';
        if (reasonExplanations[code]) return reasonExplanations[code];
        return 'Additional recorded reason: ' + String(code) + '.';
    }
    function reasonList(item) {
        var diagnostics = item && item.decision_diagnostics;
        var codes = diagnostics && Array.isArray(diagnostics.hold_reasons) ? diagnostics.hold_reasons : [];
        if (!codes.length && item && Array.isArray(item.reason_codes)) codes = item.reason_codes;
        var unique = [];
        codes.forEach(function (code) { if (code && unique.indexOf(code) === -1) unique.push(code); });
        return unique;
    }
    function decisionReasonSummary(item) {
        var codes = reasonList(item);
        var primaryReason = codes.length ? explainReason(codes[0]) : '';
        var signature = primaryReason || 'NO_REASON_RECORDED';
        var occurrence = (historyReasonOccurrences[signature] || 0) + 1;
        historyReasonOccurrences[signature] = occurrence;
        if (occurrence > 1) return 'Same reason as an earlier evaluation (repeat ' + occurrence + ').';
        return primaryReason || 'No specific reason was recorded for this evaluation.';
    }
    function renderSummary(data) {
        var health = data.worker_health || {};
        var latest = data.latest_decision || null;
        var journal = data.journal || {};
        var diagnostics = latest && latest.decision_diagnostics || {};
        var cycling = diagnostics.cycling;
        var samples = diagnostics.sample_counts || {};
        var workerLabel = health.status ? String(health.status).charAt(0).toUpperCase() +
            String(health.status).slice(1) + (health.enabled === true ? '; worker enabled' :
                health.enabled === false ? '; worker disabled' : '')
            : health.enabled === false ? 'Disabled' : 'Unknown';
        setText('worker-summary-status', workerLabel);
        var evaluatedAt = latest && (latest.evaluated_at || latest.snapshot_evaluated_at);
        if (!evaluatedAt && health.last_evaluation_at) {
            evaluatedAt = journal.mode === 'v2' && !latest
                ? health.last_evaluation_at + ' (worker evaluation; no Journal v2 decision snapshot)'
                : health.last_evaluation_at;
        }
        setText('worker-summary-evaluated-at', evaluatedAt || 'Unavailable');
        var decision = latest && latest.decision;
        var decisionText = !latest ? journal.mode === 'v2'
            ? 'No Journal v2 evaluation recorded' : 'No evaluation recorded'
            : decision === 'HOLD' ? 'HOLD — no threshold change recommended'
                : decision === 'INSUFFICIENT_DATA' ? 'Insufficient evidence — no change proposed'
                    : decision === 'BLOCKED' ? 'Blocked — no change proposed'
                        : String(decision || 'Unknown decision').replace(/_/g, ' ');
        var proposed = latest && (latest.proposed_voltage != null ? latest.proposed_voltage : latest.candidate_voltage);
        if (proposed != null) {
            decisionText += '; non-actionable hypothetical proposal: ' +
                (latest.threshold || monitoredThreshold(latest)) + ' ' + proposed + ' V';
        }
        setText('worker-current-decision', decisionText);
        var cyclingText = 'Unknown — no cycling assessment recorded.';
        if (cycling) {
            cyclingText = (cycling.detected ? 'Detected' : 'Not established') + ' — ' +
                (cycling.reported_mode_changes == null ? 'mode changes not recorded' :
                    cycling.reported_mode_changes + ' reported Line/Invert changes') +
                ' in collection order; actual switch times are unknown.';
        }
        setText('worker-summary-cycling', cyclingText);
        var level = latest && latest.evidence_level || health.evidence_level;
        var uniqueCount = latest && latest.unique_observation_count != null ? latest.unique_observation_count
            : health.observation_count;
        var collectionCount = latest && latest.collection_count != null ? latest.collection_count
            : health.collection_count;
        var evidenceText = !latest ? 'Unavailable — no evaluation evidence recorded.'
            : level === 'VERIFIED' ? 'Verified evidence is available for this evaluation.'
                : level === 'EXPLORATORY' ? 'Limited — exploratory collection-time evidence; device measurement time is unverified.'
                    : 'Unavailable — evidence quality was not recorded.';
        if (uniqueCount != null) evidenceText += ' ' + uniqueCount + ' unique observations';
        if (collectionCount != null) evidenceText += (uniqueCount != null ? ' from ' : ' ') + collectionCount + ' collections';
        if (samples.rejected_duplicate_cached_payload) {
            evidenceText += '; repeated cached payloads excluded';
        }
        setText('worker-summary-evidence', evidenceText);
        var settings = latest && latest.current_settings;
        var thresholdKey = latest && latest.monitored_threshold && latest.monitored_threshold.key || 'battery';
        var currentValue = settings && settings[thresholdKey];
        setText('worker-summary-current-threshold', currentValue != null &&
            settings.provenance === 'VERIFIED_DEVICE_READBACK'
            ? monitoredThreshold(latest) + ': ' + currentValue + ' V (verified device read-back).'
            : 'Unknown — no verified device read-back.');
        var holdReasons = reasonList(latest);
        if (!holdReasons.length && (health.blocking_reason || health.last_error)) {
            holdReasons.push(health.blocking_reason || health.last_error);
        }
        var holdText;
        if (!latest) {
            var unavailable = holdReasons.length ? explainReason(holdReasons[0]) + ' ' : '';
            holdText = unavailable + (journal.mode === 'v2'
                ? 'No Journal v2 decision is available; historical Journal v1 records are not used as the current decision.'
                : 'No latest evaluation is available.');
        }
        else if (decision === 'HOLD' || decision === 'INSUFFICIENT_DATA' || decision === 'BLOCKED' || holdReasons.length) {
            holdText = holdReasons.length ? holdReasons.slice(0, 3).map(explainReason).join(' ')
                : 'The recorded decision is ' + String(decision).replace(/_/g, ' ').toLowerCase() +
                    ', but no specific hold or blocking reason was recorded.';
        } else {
            holdText = 'The current evaluation did not record a HOLD or blocking reason.';
        }
        setText('worker-summary-hold', holdText);
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
        renderSummary(data);
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
        var evidence = item.evidence_level || 'Evidence quality unavailable';
        evidence += ' — battery ' + batteryVoltageText(item);
        appendCells(tr, [item.evaluated_at, item.decision, evidence]);
        var reason = document.createElement('td'), detail = document.createElement('details');
        detail.className = 'decision-record-details';
        var summary = document.createElement('summary');
        summary.textContent = decisionReasonSummary(item);
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

    var eventLabels = {
        BASELINE_RECORDED: 'Baseline recorded',
        JOURNAL_V2_CUTOVER: 'Journal v2 started',
        DECISION_CHANGED: 'Decision changed',
        HYPOTHESIS_CREATED: 'Hypothetical proposal recorded',
        HYPOTHESIS_REJECTED: 'Hypothetical proposal cleared',
        HYPOTHESIS_UPDATED: 'Hypothetical proposal updated',
        CONFIRMATION_CHANGED: 'Confirmation progress changed',
        HYPOTHESIS_CONFIRMED: 'Hypothesis confirmed for review',
        EVIDENCE_BLOCKED: 'Evidence became blocked',
        EVIDENCE_RECOVERED: 'Evidence blockers cleared',
        BATTERY_OBSERVATION_CHANGED: 'Battery observation status changed',
        BATTERY_RESPONSE_CHANGED: 'Battery response estimate changed',
        WORKER_FAILED: 'Worker reported an error',
        WORKER_RECOVERED: 'Worker recovered'
    };
    function eventSummary(event) {
        var payload = event && event.payload || {};
        var type = event && event.event_type;
        if (type === 'DECISION_CHANGED' && payload.to && payload.to.decision) {
            return 'Outcome is now ' + String(payload.to.decision).replace(/_/g, ' ') + '.';
        }
        if (type === 'HYPOTHESIS_CREATED' && payload.candidate_voltage != null) {
            return 'A hypothetical ' + (payload.threshold || 'threshold') + ' value of ' +
                payload.candidate_voltage + ' V was recorded for review.';
        }
        if (type === 'HYPOTHESIS_REJECTED') return 'The previous hypothetical proposal is no longer active.';
        if (type === 'HYPOTHESIS_UPDATED') return 'A hypothetical proposal value changed.';
        if (type === 'CONFIRMATION_CHANGED') return 'The number of compatible observations changed.';
        if (type === 'HYPOTHESIS_CONFIRMED') return 'Confirmation was recorded; this remains non-actionable.';
        if (type === 'EVIDENCE_BLOCKED') {
            var added = Array.isArray(payload.added) ? payload.added : [];
            return added.length ? 'New blocker: ' + added.slice(0, 2).map(explainReason).join(' ')
                : 'New evidence blockers were recorded.';
        }
        if (type === 'EVIDENCE_RECOVERED') {
            var removed = Array.isArray(payload.removed) ? payload.removed : [];
            return removed.length ? 'Cleared: ' + removed.slice(0, 2).map(explainReason).join(' ')
                : 'One or more evidence blockers cleared.';
        }
        if (type === 'BATTERY_OBSERVATION_CHANGED') return 'A reported battery measurement changed validity status.';
        if (type === 'BATTERY_RESPONSE_CHANGED') return 'Reported response changed; the cause is unknown.';
        if (type === 'WORKER_FAILED') return 'The worker reported a failure; check Technical details for health information.';
        if (type === 'WORKER_RECOVERED') return 'The worker reported a successful recovery.';
        if (type === 'JOURNAL_V2_CUTOVER') return 'Existing v1 records remain available in History.';
        if (type === 'BASELINE_RECORDED') return 'The starting decision state was recorded.';
        return 'A material decision-journal change was recorded.';
    }

    function renderEventItem(event) {
        var item = document.createElement('li');
        var heading = document.createElement('div');
        heading.className = 'decision-event-heading';
        var time = document.createElement('time');
        time.textContent = event.event_time || 'Time unavailable';
        var title = document.createElement('strong');
        title.textContent = eventLabels[event.event_type] || 'Decision event';
        heading.appendChild(time); heading.appendChild(title);
        var description = document.createElement('p');
        description.textContent = eventSummary(event);
        var details = document.createElement('details');
        var summary = document.createElement('summary');
        summary.textContent = 'Original event payload';
        var pre = document.createElement('pre');
        details.addEventListener('toggle', function () {
            if (details.open && !pre.dataset.generated) {
                pre.textContent = JSON.stringify(event.payload || {}, null, 2);
                pre.dataset.generated = 'true';
            }
        });
        details.appendChild(summary); details.appendChild(pre);
        item.appendChild(heading); item.appendChild(description); item.appendChild(details);
        return item;
    }

    function renderRawJournalEvents(body) {
        if (!body) return;
        body.replaceChildren();
        rawJournalEvents.forEach(function (event) {
            var item = document.createElement('li');
            var details = document.createElement('details');
            var summary = document.createElement('summary');
            summary.textContent = (eventLabels[event.event_type] || 'Decision event') + ' — ' +
                (event.event_time || 'Time unavailable');
            var pre = document.createElement('pre');
            pre.textContent = JSON.stringify(event, null, 2);
            details.appendChild(summary); details.appendChild(pre); item.appendChild(details);
            body.appendChild(item);
        });
        rawJournalEventsRendered = true;
    }

    function renderJournal(journal, latest) {
        var body = document.getElementById('journal-events-body');
        var rawBody = document.getElementById('journal-raw-events');
        if (body) body.replaceChildren();
        if (rawBody) rawBody.replaceChildren();
        rawJournalEvents = [];
        rawJournalEventsRendered = false;
        if (!journal || journal.mode !== 'v2') {
            setText('journal-mode', 'Journal v1: every evaluation is stored as a full entry' +
                (journal && journal.v2_installed ? '; Journal v2 installed but not active' : ''));
            setText('decision-history-source', 'These rows are the retained Journal v1 evaluation history.');
            setText('journal-repetition', 'Not tracked in Journal v1');
            setText('journal-workers', 'Reported in worker health above');
            if (body) {
                var v1Note = document.createElement('li');
                v1Note.textContent = 'Journal v1 stores full evaluations; its separate event timeline is unavailable. Open History for those records.';
                body.appendChild(v1Note);
            }
            return;
        }
        setText('decision-history-source', 'These are retained Journal v1 evaluations; they do not replace the latest Journal v2 snapshot shown above.');
        var state = journal.state || {};
        setText('journal-mode', 'Journal v2 active since ' + (journal.changed_at || '—') + '; state revision ' +
            (state.revision == null ? '—' : state.revision) + '; last evaluation ' + (state.last_evaluated_at || '—') +
            '; initialised from ' + (state.initialized_from || '—') + '. ' +
            (latest && latest.snapshot_evaluated_at ? 'Latest diagnostics snapshot ' + latest.snapshot_evaluated_at +
                (latest.evaluations_since_snapshot ? '; ' + latest.evaluations_since_snapshot +
                    ' identical evaluations since (last ' + latest.last_evaluated_at + ')' : '') + '. ' : '') +
            (journal.history_note || ''));
        var repetition = journal.current_repetition;
        var outcome = repetition && repetition.outcome || {};
        setText('journal-repetition', repetition
            ? (outcome.decision || '—') + ' / ' + (outcome.stability || '—') + ' unchanged for ' +
                repetition.evaluation_count + ' evaluations (first ' + repetition.first_seen_at + ', last ' +
                repetition.last_seen_at + '); new evidence ' + repetition.new_evidence_count +
                ', repeated or cached evidence ' + repetition.repeated_evidence_count
            : 'No Journal v2 evaluation yet');
        var workers = Array.isArray(journal.workers) ? journal.workers : [];
        setText('journal-workers', workers.length ? workers.map(function (worker) {
            return worker.worker_id + ': ' + String(worker.status).toUpperCase() + ', heartbeat ' + worker.last_heartbeat_at +
                (worker.status === 'failing' ? ', ' + worker.consecutive_failures + ' consecutive failures (' +
                    (worker.last_error || 'unknown error') + ')' : '');
        }).join('; ') : 'No worker heartbeat recorded');
        rawJournalEvents = Array.isArray(journal.events) ? journal.events : [];
        var rawDisclosure = document.getElementById('journal-raw-events-disclosure');
        if (rawDisclosure && rawDisclosure.open) renderRawJournalEvents(rawBody);
        if (!body) return;
        var events = rawJournalEvents;
        events.slice(0, 8).forEach(function (event) { body.appendChild(renderEventItem(event)); });
        if (events.length > 8) {
            var earlier = document.createElement('li');
            var disclosure = document.createElement('details');
            var summary = document.createElement('summary');
            summary.textContent = 'Show ' + (events.length - 8) + ' earlier significant events';
            var earlierEvents = document.createElement('ol');
            disclosure.addEventListener('toggle', function () {
                if (disclosure.open && !earlierEvents.dataset.generated) {
                    events.slice(8).forEach(function (event) { earlierEvents.appendChild(renderEventItem(event)); });
                    earlierEvents.dataset.generated = 'true';
                }
            });
            disclosure.appendChild(summary); disclosure.appendChild(earlierEvents); earlier.appendChild(disclosure);
            body.appendChild(earlier);
        }
        if (!body.children.length) {
            var empty = document.createElement('li');
            empty.textContent = 'No significant events yet.';
            body.appendChild(empty);
        }
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
    function clearBatteryHistory() {
        batteryGeneration++; batteryCursor = null; batteryLoadedDays = null;
        batterySeenObservations = new Set(); batterySeenBehavior = new Set();
        var observationsBody = document.getElementById('battery-observation-body');
        var behaviorBody = document.getElementById('battery-behavior-body');
        var status = document.getElementById('battery-grid-status');
        var more = document.getElementById('battery-load-more');
        if (observationsBody) observationsBody.replaceChildren();
        if (behaviorBody) behaviorBody.replaceChildren();
        if (status) status.textContent = 'Open History to load battery observations.';
        if (more) { more.hidden = true; more.disabled = true; }
    }
    async function loadBatteryHistory(reset) {
        var status = document.getElementById('battery-grid-status');
        var observationsBody = document.getElementById('battery-observation-body');
        var behaviorBody = document.getElementById('battery-behavior-body');
        if (batteryLoading) { if (reset) batteryPendingReset = true; return; }
        if (reset) {
            batteryGeneration++; batteryCursor = null;
            batteryLoadedDays = null;
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
            batteryLoadedDays = requested;
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
    function appendDecisionItems(items) {
        var body = document.getElementById('decision-body');
        (Array.isArray(items) ? items : []).forEach(function (item) {
            var key = String(item.evaluation_id || item.evaluated_at) + ':' + String(item.evaluated_at);
            if (!seen.has(key)) { seen.add(key); body.appendChild(renderDecision(item)); }
        });
    }
    function renderDeferredDecisionItems() {
        if (!historyExpanded || !deferredDecisionItems.length) return;
        appendDecisionItems(deferredDecisionItems);
        deferredDecisionItems = [];
        var body = document.getElementById('decision-body');
        var status = document.getElementById('decision-status');
        status.textContent = body.children.length ? body.children.length + ' decisions loaded' : 'No decisions in this period.';
    }
    async function load(reset) {
        var status = document.getElementById('decision-status');
        var body = document.getElementById('decision-body');
        if (loading) { if (reset) pendingReset = true; return; }
        if (reset) {
            generation++; nextCursor = null; seen = new Set(); historyReasonOccurrences = Object.create(null);
            deferredDecisionItems = [];
            body.replaceChildren();
        }
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
            if (!cursor) renderJournal(data.journal, data.latest_decision);
            if (historyExpanded) appendDecisionItems(data.decisions);
            else deferredDecisionItems = deferredDecisionItems.concat(data.decisions);
            nextCursor = data.next_cursor || null;
            var more = document.getElementById('decision-load-more'); more.hidden = !data.has_more; more.disabled = false;
            status.textContent = body.children.length ? body.children.length + ' decisions loaded'
                : deferredDecisionItems.length ? 'Open History to display the ' + deferredDecisionItems.length + ' loaded evaluations.'
                    : 'No decisions in this period.';
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
        var historyDetails = document.getElementById('decision-history');
        historyExpanded = !historyDetails || !!historyDetails.open;
        if (historyDetails) {
            historyDetails.addEventListener('toggle', function () {
                historyExpanded = !!historyDetails.open;
                if (historyExpanded) {
                    renderDeferredDecisionItems();
                    if (batteryLoadedDays !== days) loadBatteryHistory(true);
                }
            });
        }
        var rawEventsDetails = document.getElementById('journal-raw-events-disclosure');
        if (rawEventsDetails) {
            rawEventsDetails.addEventListener('toggle', function () {
                if (rawEventsDetails.open && !rawJournalEventsRendered) {
                    renderRawJournalEvents(document.getElementById('journal-raw-events'));
                }
            });
        }
        document.querySelectorAll('[data-decision-days]').forEach(function (button) {
            button.addEventListener('click', function () {
                days = Number(button.dataset.decisionDays); load(true);
                if (historyExpanded) loadBatteryHistory(true);
                else clearBatteryHistory();
            });
        });
        document.getElementById('decision-load-more').addEventListener('click', function () {
            this.disabled = true; load(false);
        });
        document.getElementById('battery-load-more').addEventListener('click', function () {
            this.disabled = true; loadBatteryHistory(false);
        });
        load(true);
        if (historyExpanded) loadBatteryHistory(true);
    });
}());
