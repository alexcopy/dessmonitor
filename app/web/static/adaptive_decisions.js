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
    var currentLatest = null;
    var summaryRendered = false;
    var summaryLoadedAt = null;

    // ── DOM helpers ───────────────────────────────────────────────────────
    function byId(id) { return document.getElementById(id); }
    function setText(id, value) {
        var node = byId(id);
        if (node) node.textContent = value == null || value === '' ? '—' : String(value);
    }
    function setTone(id, tone) {
        var node = byId(id);
        if (node && node.dataset) node.dataset.tone = tone;
    }
    function setHidden(id, hidden) {
        var node = byId(id);
        if (node) node.hidden = !!hidden;
    }
    function make(tag, className, text) {
        var node = document.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = String(text);
        return node;
    }

    // ── Time formatting: compact local text, full local + UTC offset + stored value on hover ──
    var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    function pad(value) { return value < 10 ? '0' + value : String(value); }
    function parseTime(value) {
        if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(value)) return null;
        var parsed = new Date(value);
        return isNaN(parsed.getTime()) ? null : parsed;
    }
    function timeZoneName() {
        try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (_) { return ''; }
    }
    function shortTime(value) {
        var parsed = parseTime(value);
        if (!parsed) return value == null || value === '' ? '—' : String(value);
        var now = new Date();
        var clock = pad(parsed.getHours()) + ':' + pad(parsed.getMinutes());
        if (parsed.toDateString() === now.toDateString()) return clock;
        var date = parsed.getDate() + ' ' + MONTHS[parsed.getMonth()];
        if (parsed.getFullYear() !== now.getFullYear()) date += ' ' + parsed.getFullYear();
        return date + ' ' + clock;
    }
    function fullTime(value) {
        var parsed = parseTime(value);
        if (!parsed) return value == null ? '' : String(value);
        var offset = -parsed.getTimezoneOffset();
        var zone = timeZoneName();
        return parsed.getFullYear() + '-' + pad(parsed.getMonth() + 1) + '-' + pad(parsed.getDate()) + ' ' +
            pad(parsed.getHours()) + ':' + pad(parsed.getMinutes()) + ':' + pad(parsed.getSeconds()) + ' ' +
            (zone ? zone + ' ' : '') + '(UTC' + (offset < 0 ? '−' : '+') + pad(Math.floor(Math.abs(offset) / 60)) + ':' +
            pad(Math.abs(offset) % 60) + '); stored as ' + value;
    }
    function age(value) {
        var parsed = parseTime(value);
        if (!parsed) return '';
        var seconds = (Date.now() - parsed.getTime()) / 1000;
        if (seconds < -60) return 'in the future (clock mismatch)';
        if (seconds < 60) return 'just now';
        if (seconds < 3600) return Math.floor(seconds / 60) + ' min ago';
        if (seconds < 172800) return Math.floor(seconds / 3600) + ' h ago';
        return Math.floor(seconds / 86400) + ' days ago';
    }
    function applyTime(node, value, fallback) {
        if (!node) return;
        if (!parseTime(value)) {
            node.textContent = value == null || value === '' ? (fallback || '—') : String(value);
            node.title = '';
            return;
        }
        node.textContent = shortTime(value);
        node.dateTime = String(value);
        node.title = fullTime(value);
    }
    function timeElement(value, fallback) {
        var node = make('time');
        applyTime(node, value, fallback);
        return node;
    }
    function duration(seconds) {
        if (seconds == null) return '—';
        return Math.max(0, Math.ceil(Number(seconds) / 60)) + ' min';
    }
    function humanize(code) {
        return code == null || code === '' ? '—' : String(code).replace(/_/g, ' ').toLowerCase();
    }

    // ── Key/value diagnostic rows ─────────────────────────────────────────
    // Each row is [label, value, options]; options: time, code, tone, detail, detailLabel.
    function fillRows(id, rows) {
        var list = byId(id);
        if (!list) return;
        list.replaceChildren();
        rows.forEach(function (row) {
            if (!row) return;
            var options = row[2] || {};
            var wrapper = make('div');
            var term = make('dt', null, row[0]);
            var value = make('dd');
            if (options.tone && value.dataset) value.dataset.tone = options.tone;
            if (options.time) value.appendChild(timeElement(row[1], options.fallback));
            else value.appendChild(make('span', options.code ? 'adl-code' : null,
                row[1] == null || row[1] === '' ? '—' : row[1]));
            if (options.detail) {
                var more = make('details', 'adl-more');
                var summary = make('summary', null, options.detailLabel || 'Show all');
                var pre = make('pre', null, options.detail);
                more.appendChild(summary); more.appendChild(pre); value.appendChild(more);
            }
            wrapper.appendChild(term); wrapper.appendChild(value); list.appendChild(wrapper);
        });
    }

    // ── Settings and battery voltage (shared validity rules) ──────────────
    function monitoredThreshold(item) {
        return item && item.monitored_threshold && item.monitored_threshold.label || 'Back to Battery';
    }
    // Entries store the threshold key ('battery'); show the monitored threshold's label for that key.
    function thresholdName(key, item) {
        var monitored = item && item.monitored_threshold;
        if (monitored && monitored.key === key && monitored.label) return monitored.label;
        if (key === 'battery') return 'Back to Battery';
        return key == null || key === '' ? monitoredThreshold(item) : String(key);
    }
    // Pending candidate keys look like HYPOTHETICAL_RAISE:26.5 (online_output_learning).
    function pendingText(value) {
        if (value == null || value === '') return 'none';
        var match = /^HYPOTHETICAL_(RAISE|LOWER):(-?\d+(?:\.\d+)?)$/.exec(String(value));
        return match ? 'hypothetical ' + match[1].toLowerCase() + ' to ' + match[2] + ' V' : String(value);
    }
    function verifiedThreshold(item) {
        var settings = item && item.current_settings;
        var key = item && item.monitored_threshold && item.monitored_threshold.key || 'battery';
        var value = settings ? settings[key] : null;
        return value != null && settings.provenance === 'VERIFIED_DEVICE_READBACK' ? value : null;
    }
    function currentThreshold(item) {
        var value = verifiedThreshold(item);
        return value == null ? 'Not verified (no device read-back)' : String(value) + ' V (verified device read-back)';
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

    // ── Reason codes ──────────────────────────────────────────────────────
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
        WORKER_EVALUATION_STALE: 'The worker has not completed an evaluation for more than three minutes.',
        WORKER_EVALUATION_TIME_INVALID: 'The worker evaluation time is invalid.',
        NO_V2_EVALUATION_YET: 'Journal v2 has not stored an evaluation yet.',
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
    // Presentation order only: the most fundamental blocker is shown as the main reason.
    // It never changes which codes the algorithm recorded; all of them stay listed in
    // Technical details. Unknown codes rank after known blockers, before caveats.
    var REASON_PRIORITY = [
        'DATABASE_URL_UNAVAILABLE', 'ADAPTIVE_OUTPUT_LEARNING_DISABLED', 'WORKER_EVALUATION_FAILED',
        'NO_WORKER_HEARTBEAT', 'WORKER_EVALUATION_STALE', 'WORKER_EVALUATION_TIME_INVALID',
        'NO_PERSISTED_WORKER_EVALUATION', 'NO_V2_EVALUATION_YET',
        'NO_RECENT_COLLECTIONS', 'LATEST_COLLECTION_STALE', 'LATEST_COLLECTION_INCOMPLETE_STALE_OR_CONFLICTING',
        'INVALID_BATTERY_VOLTAGE', 'WORKING_MODE_UNKNOWN', 'FEWER_THAN_6_UNIQUE_COMPLETE_OBSERVATIONS',
        'VERIFIED_OBSERVATION_SOURCE_UNAVAILABLE', 'VERIFIED_OBSERVATION_PROVENANCE_UNAVAILABLE', 'VERIFIED_SAMPLE_INVALID',
        'SAG_LEARNER_CONFIGURATION_UNAVAILABLE', 'SAG_LEARNER_CONFIGURATION_INVALID',
        'LINE_MODE_CHARGING_CONFOUNDS_VOLTAGE', 'INSUFFICIENT_COMPARABLE_PAIRS',
        'TOO_FEW_LINE_NOT_CHARGING_OBSERVATIONS', 'TOO_FEW_INVERT_DISCHARGING_OBSERVATIONS',
        'PAIRS_DISAGREE_OUTLIERS_REJECTED', 'LATEST_OBSERVATION_NOT_COMPARABLE', 'NO_VOLTAGE_CONTRAST_ESTIMATE',
        'INSUFFICIENT_HYPOTHESIS_EVIDENCE', 'EXPLORATORY_ANALYSIS_UNAVAILABLE',
        'BMS_LIMITS_UNKNOWN', 'CATALOGUE_OR_HYSTERESIS_BLOCK', 'HISTORICAL_BASELINE_UNKNOWN',
        'HISTORICAL_BASELINE_OUT_OF_RANGE', 'HYPOTHESIS_NOT_SAFETY_QUALIFIED',
        'PROPOSAL_AWAITING_CONFIRMATION', 'PROPOSAL_AWAITING_COMPATIBLE_CONFIRMATION', 'DRY_RUN_COOLDOWN',
        'COOLDOWN_STATE_UNVERIFIED', '*UNKNOWN*', 'CYCLING_DETECTED',
        'CURRENT_THRESHOLD_SETTINGS_UNKNOWN', 'HISTORICAL_BASELINE_ONLY', 'MODE_HISTORY_TRUNCATED',
        'MODE_HISTORY_OBSERVATION_GAP', 'QUANTITATIVE_HISTORY_TRUNCATED', 'DEVICE_OBSERVATION_TIME_INVALID',
        'SAMPLE_SOURCE_NOT_VERIFIED', 'RECEIPT_TIME_UNAVAILABLE', 'DEVICE_TIMESTAMP_PROVENANCE_UNVERIFIED',
        'COLLECTION_TIME_ONLY'
    ];
    function reasonRank(code) {
        var index = REASON_PRIORITY.indexOf(code);
        return index >= 0 ? index : reasonExplanations[code] ? REASON_PRIORITY.length : REASON_PRIORITY.indexOf('*UNKNOWN*');
    }
    function reasonList(item) {
        var diagnostics = item && item.decision_diagnostics;
        var codes = diagnostics && Array.isArray(diagnostics.hold_reasons) ? diagnostics.hold_reasons : [];
        if (!codes.length && item && Array.isArray(item.reason_codes)) codes = item.reason_codes;
        var unique = [];
        codes.forEach(function (code) { if (code && unique.indexOf(code) === -1) unique.push(code); });
        return unique;
    }
    function primaryReason(codes) {
        var best = null;
        codes.forEach(function (code, index) {
            var rank = reasonRank(code);
            if (!best || rank < best.rank) best = {code: code, rank: rank, index: index};
        });
        return best ? best.code : null;
    }
    function voltagePairsNote(latest) {
        var voltage = latest && latest.decision_diagnostics && latest.decision_diagnostics.voltage_comparison;
        if (!voltage || voltage.status === 'ESTIMATED' || voltage.required_pairs == null) return '';
        return ' (' + (voltage.comparable_pairs || 0) + ' of ' + voltage.required_pairs + ' comparable pairs found)';
    }
    function decisionReasonSummary(item) {
        var codes = reasonList(item);
        var main = primaryReason(codes);
        var text = main ? explainReason(main) : '';
        var signature = text || 'NO_REASON_RECORDED';
        var occurrence = (historyReasonOccurrences[signature] || 0) + 1;
        historyReasonOccurrences[signature] = occurrence;
        if (occurrence > 1) return 'Same reason as an earlier evaluation (repeat ' + occurrence + ').';
        return text || 'No specific reason was recorded for this evaluation.';
    }

    // ── Worker state: separates real worker failures from limited evidence ──
    var WORKER_PROBLEM_CODES = {
        WORKER_EVALUATION_FAILED: 'Failing', WORKER_EVALUATION_STALE: 'Stale', NO_WORKER_HEARTBEAT: 'No heartbeat',
        WORKER_EVALUATION_TIME_INVALID: 'Time invalid', DATABASE_URL_UNAVAILABLE: 'Unavailable'
    };
    var WORKER_WAITING_CODES = {NO_PERSISTED_WORKER_EVALUATION: 1, NO_V2_EVALUATION_YET: 1};
    function workerState(health, journal) {
        var workers = journal && journal.mode === 'v2' && Array.isArray(journal.workers) ? journal.workers : [];
        var failing = workers.filter(function (worker) { return worker && worker.status === 'failing'; });
        var failingCount = failing.length || Number(health.failing_workers) || 0;
        var blocking = health.blocking_reason;
        if (health.enabled === false || health.status === 'disabled') {
            return {label: 'Disabled', tone: 'muted', attentionTone: 'warn',
                attention: 'Adaptive learning is disabled, so no new evaluations are recorded.'};
        }
        if (failingCount || blocking === 'WORKER_EVALUATION_FAILED' ||
            (health.last_error && health.status !== 'healthy')) {
            var total = workers.length || Number(health.worker_count) || 0;
            var names = failing.map(function (worker) {
                return worker.worker_id + (worker.consecutive_failures ? ' (' + worker.consecutive_failures +
                    ' consecutive failures' + (worker.last_error ? ': ' + worker.last_error : '') + ')' : '');
            });
            return {label: failingCount && total > 1 ? failingCount + ' of ' + total + ' failing' : 'Failing', tone: 'bad',
                attentionTone: 'bad', attention: 'Attention: worker failure — ' + (names.length ? names.join('; ')
                    : health.last_error ? String(health.last_error) : explainReason(blocking || 'WORKER_EVALUATION_FAILED')) +
                    '. Check Technical details → Worker health.'};
        }
        if (WORKER_PROBLEM_CODES[blocking]) {
            return {label: WORKER_PROBLEM_CODES[blocking], tone: 'bad', attentionTone: 'bad',
                attention: 'Attention: ' + explainReason(blocking)};
        }
        if (WORKER_WAITING_CODES[blocking]) {
            return {label: 'Waiting for first evaluation', tone: 'warn', attentionTone: 'warn',
                attention: 'Waiting for the first stored evaluation; check again after the next evaluation cycle.'};
        }
        if (health.status === 'healthy' || health.status === 'degraded') {
            return {label: health.status === 'healthy' ? 'Running' : 'Running · evidence limited', tone: 'ok',
                attentionTone: 'ok', attention: 'No action needed — the worker is running and only observes.'};
        }
        return {label: 'Unknown', tone: 'warn', attentionTone: 'warn', attention: 'Worker status was not reported.'};
    }

    // ── Primary decision panel and indicators ─────────────────────────────
    function decisionView(latest, journal) {
        var v2 = journal && journal.mode === 'v2';
        if (!latest) {
            return {label: 'No decision yet', tone: 'muted', consequence: v2
                ? 'Older Journal v1 records are not used as the current Journal v2 decision.'
                : 'No evaluation has been stored.'};
        }
        var decision = latest.decision;
        if (decision === 'HOLD') return {label: 'HOLD', tone: 'hold', consequence: 'No threshold change recommended.'};
        if (decision === 'INSUFFICIENT_DATA') {
            return {label: 'INSUFFICIENT DATA', tone: 'warn', consequence: 'Not enough evidence to evaluate a change; nothing proposed.'};
        }
        if (decision === 'BLOCKED') return {label: 'BLOCKED', tone: 'warn', consequence: 'Evaluation blocked; nothing proposed.'};
        if (!decision) return {label: 'UNKNOWN', tone: 'muted', consequence: 'The evaluation did not record a decision.'};
        return {label: String(decision).replace(/_/g, ' '), tone: 'proposal',
            consequence: 'Hypothetical proposal for review only — non-actionable. This system does not write inverter settings.'};
    }
    // One line describing a hypothetical candidate or confirmed hypothetical decision. Always non-actionable;
    // missing voltage or confirmation fields are stated, never invented.
    function proposalText(latest) {
        if (!latest) return '';
        var proposed = latest.proposed_voltage != null ? latest.proposed_voltage : latest.candidate_voltage;
        var direction = /^HYPOTHETICAL_(RAISE|LOWER)$/.exec(String(latest.decision || ''));
        if (proposed == null && !direction) return '';
        var progress = latest.confirmation_progress;
        var confirmed = latest.stability === 'CONFIRMED';
        return (confirmed ? 'Confirmed hypothetical ' : 'Hypothetical ') + (direction ? direction[1].toLowerCase() : 'candidate') +
            ': ' + thresholdName(latest.threshold, latest) + ' ' + (proposed == null ? '(voltage not recorded)' : proposed + ' V') +
            ' — non-actionable' + (progress && progress.confirmed != null && progress.required != null
                ? '; confirmation ' + progress.confirmed + ' of ' + progress.required : '; confirmation not recorded') + '.';
    }
    function renderSummary(data) {
        var health = data.worker_health || {};
        var latest = data.latest_decision || null;
        var journal = data.journal || {};
        var view = decisionView(latest, journal);
        setTone('decision-primary', view.tone);
        setText('worker-current-decision', view.label);
        setText('decision-consequence', view.consequence);

        var codes = reasonList(latest);
        if (!codes.length && !latest && (health.blocking_reason || health.last_error)) {
            codes = [health.blocking_reason || health.last_error];
        }
        var main = primaryReason(codes);
        var reasonText;
        if (main) {
            reasonText = explainReason(main) + (/PAIRS|CONFOUNDS/.test(main) ? voltagePairsNote(latest) : '');
        } else if (!latest) {
            reasonText = 'No evaluation is available to explain.';
        } else if (latest.decision === 'HOLD' || latest.decision === 'BLOCKED' || latest.decision === 'INSUFFICIENT_DATA') {
            reasonText = 'No specific hold or blocking reason was recorded.';
        } else {
            reasonText = 'No hold or blocking reason was recorded.';
        }
        setText('worker-summary-hold', reasonText);
        var more = byId('decision-more-reasons');
        if (more) {
            more.hidden = codes.length < 2;
            more.textContent = codes.length === 2 ? '1 more factor — see all reasons'
                : (codes.length - 1) + ' more factors — see all reasons';
        }

        var proposal = byId('decision-proposal');
        if (proposal) {
            var text = proposalText(latest);
            proposal.hidden = !text;
            proposal.textContent = text;
        }

        var evaluatedAt = latest && (latest.evaluated_at || latest.snapshot_evaluated_at) || health.last_evaluation_at;
        var latestSeen = latest && latest.last_evaluated_at || evaluatedAt;
        applyTime(byId('worker-summary-evaluated-at'), latestSeen, 'Unavailable');
        setText('decision-age', parseTime(latestSeen) ? age(latestSeen) +
            (latest && latest.evaluations_since_snapshot ? ' · unchanged since ' + shortTime(latest.snapshot_evaluated_at) : '')
            : (journal.mode === 'v2' && !latest ? 'No Journal v2 decision snapshot' : ''));

        var worker = workerState(health, journal);
        setText('worker-summary-status', worker.label);
        setTone('worker-summary-status', worker.tone);
        setText('decision-attention-text', worker.attention);
        setTone('decision-attention', worker.attentionTone);
        setHidden('decision-retry', true);

        setText('current-threshold-label', monitoredThreshold(latest) + ' setting');
        var threshold = verifiedThreshold(latest);
        setText('worker-summary-current-threshold', threshold == null ? 'Not verified' : threshold + ' V (verified read-back)');
        setTone('worker-summary-current-threshold', threshold == null ? 'muted' : 'ok');
        setText('worker-monitored-threshold', monitoredThreshold(latest));
        renderIndicators(latest, health);
    }
    function renderIndicators(latest, health) {
        var diagnostics = latest && latest.decision_diagnostics || {};
        var cycling = diagnostics.cycling;
        var changes = cycling && cycling.reported_mode_changes;
        var windowSeconds = latest && latest.effective_window_seconds != null ? latest.effective_window_seconds
            : health.effective_window_seconds;
        setText('indicator-mode-changes-value', changes == null ? '—' : changes);
        setText('indicator-mode-changes-label', changes === 1 ? 'reported mode change' : 'reported mode changes');
        setText('indicator-mode-changes-note', changes == null ? 'Not recorded by this evaluation'
            : (cycling.detected ? 'Frequent switching reported' : 'Line/Invert changes') +
                (windowSeconds != null ? ' in ' + duration(windowSeconds) : '') + ' · not verified physical switching');
        setTone('indicator-mode-changes', changes == null ? 'muted' : cycling.detected ? 'warn' : 'neutral');

        var unique = latest && latest.unique_observation_count != null ? latest.unique_observation_count : health.observation_count;
        var collections = latest && latest.collection_count != null ? latest.collection_count : health.collection_count;
        var duplicates = diagnostics.sample_counts && diagnostics.sample_counts.rejected_duplicate_cached_payload;
        setText('indicator-observations-value', unique == null ? '—' : unique);
        setText('indicator-observations-note', unique == null ? 'Not recorded' : (collections != null
            ? 'from ' + collections + ' collections' : 'collection count not recorded') +
            (duplicates ? ' · ' + duplicates + ' repeated payloads excluded' : ''));
        setTone('indicator-observations', unique == null ? 'muted' : 'neutral');

        var level = latest ? latest.evidence_level : null;
        setText('indicator-evidence-value', level === 'VERIFIED' ? 'Verified' : level === 'EXPLORATORY' ? 'Limited'
            : latest ? 'Unknown' : '—');
        setText('indicator-evidence-note', level === 'VERIFIED' ? 'Verified device evidence'
            : level === 'EXPLORATORY' ? 'Exploratory · collection time only, device time unverified'
                : latest ? 'Evidence quality was not recorded' : 'No evaluation evidence recorded');
        setTone('indicator-evidence', level === 'VERIFIED' ? 'ok' : level === 'EXPLORATORY' ? 'warn' : 'muted');
    }
    function renderUnavailable() {
        if (summaryRendered) {
            setText('decision-attention-text', 'Refresh failed — showing data loaded at ' +
                shortTime(summaryLoadedAt) + '. The decision journal API is unavailable.');
            setTone('decision-attention', 'bad');
        } else {
            setTone('decision-primary', 'bad');
            setText('worker-current-decision', 'Unavailable');
            setText('decision-consequence', 'The decision journal could not be loaded, so the current decision is unknown.');
            setText('worker-summary-hold', 'The decision API returned an error or invalid data.');
            setHidden('decision-more-reasons', true);
            setHidden('decision-proposal', true);
            setText('worker-summary-status', 'Unknown'); setTone('worker-summary-status', 'warn');
            setText('decision-attention-text', 'Attention: decision data unavailable.');
            setTone('decision-attention', 'bad');
        }
        setHidden('decision-retry', false);
    }

    // ── Technical details ─────────────────────────────────────────────────
    function joinCounts(counts) {
        if (!counts || typeof counts !== 'object') return '';
        return Object.keys(counts).filter(function (key) { return counts[key]; })
            .map(function (key) { return key + ' ' + counts[key]; }).join(', ');
    }
    function renderReasons(latest, health) {
        var list = byId('worker-hold-reasons');
        if (!list) return;
        list.replaceChildren();
        var codes = reasonList(latest);
        if (!codes.length && !latest && health.blocking_reason) codes = [health.blocking_reason];
        var main = primaryReason(codes);
        codes.slice().sort(function (a, b) { return reasonRank(a) - reasonRank(b); }).forEach(function (code) {
            var item = make('li');
            item.appendChild(make('span', null, explainReason(code)));
            if (code === main) item.appendChild(make('span', 'adl-tag', 'main reason'));
            item.appendChild(make('code', null, code));
            list.appendChild(item);
        });
        if (!codes.length) list.appendChild(make('li', 'adl-dim', 'No hold or blocking reason recorded.'));
    }
    function renderWorkerGroup(data) {
        var health = data.worker_health || {};
        var journal = data.journal || {};
        var worker = workerState(health, journal);
        var problem = health.last_error || health.blocking_reason;
        var rows = [
            ['Status', worker.label, {tone: worker.tone}],
            ['Reported status', String(health.status || 'unknown').toUpperCase() +
                (health.enabled === true ? ' (enabled)' : health.enabled === false ? ' (disabled)' : ''), {code: true}],
            ['Last evaluation', health.last_evaluation_at, {time: true}],
            ['Last successful evaluation', health.last_successful_evaluation_at, {time: true}],
            ['Error or blocking reason', problem ? explainReason(problem) + ' (' + problem + ')' : 'None recorded'],
            ['Status source', health.status_source, {code: true}]
        ];
        var workers = journal.mode === 'v2' && Array.isArray(journal.workers) ? journal.workers : [];
        workers.forEach(function (item) {
            rows.push(['Worker ' + item.worker_id, String(item.status || 'unknown').charAt(0).toUpperCase() +
                String(item.status || 'unknown').slice(1) + ' · heartbeat ' + shortTime(item.last_heartbeat_at) +
                (item.status === 'failing' ? ' · ' + item.consecutive_failures + ' consecutive failures (' +
                    (item.last_error || 'unknown error') + ')' : ''),
                {tone: item.status === 'failing' ? 'bad' : item.status === 'healthy' ? 'ok' : null}]);
        });
        if (journal.mode === 'v2' && !workers.length) rows.push(['Workers', 'No worker heartbeat recorded', {tone: 'bad'}]);
        fillRows('diag-worker', rows);
    }
    function renderEvidenceGroup(latest, health) {
        if (!latest) { fillRows('diag-evidence', [['Evidence', 'Not recorded — no evaluation available']]); return; }
        var diagnostics = latest.decision_diagnostics || {};
        var samples = diagnostics.sample_counts;
        var quant = diagnostics.quantitative_history_coverage || {};
        var level = latest.evidence_level;
        var unique = latest.unique_observation_count != null ? latest.unique_observation_count : health.observation_count;
        var collections = latest.collection_count != null ? latest.collection_count : health.collection_count;
        var windowSeconds = latest.effective_window_seconds != null ? latest.effective_window_seconds : health.effective_window_seconds;
        var rows = [
            ['Evidence level', (level === 'EXPLORATORY' ? 'Limited — exploratory' : level === 'VERIFIED' ? 'Verified'
                : 'Not recorded') + (level ? ' (' + level + ')' : ''), {tone: level === 'VERIFIED' ? 'ok' : level ? 'warn' : null}],
            ['Observations', (unique == null ? '—' : unique + ' unique') + ' from ' +
                (collections == null ? '—' : collections) + ' collections'],
            ['Effective window', duration(windowSeconds)]
        ];
        if (samples) {
            rows.push(['Samples analysed', samples.analysed_observations + ' of ' + samples.raw_collections + ' collections']);
            rows.push(['Rejected samples', joinCounts({
                'outside window': samples.rejected_outside_window_or_invalid_time,
                'unknown mode': samples.rejected_unknown_mode,
                'missing/invalid fields': samples.rejected_missing_or_invalid_numeric,
                'duplicate cached payload': samples.rejected_duplicate_cached_payload,
                'raw row bound': samples.rejected_raw_row_bound,
                'analysis cap': samples.rejected_analysis_cap}) || 'None']);
        }
        if (quant.raw_row_truncated) {
            rows.push(['History coverage', 'Truncated: only the newest ' + quant.raw_row_limit +
                ' raw rows analysed; older observations omitted (partial three-hour window)', {tone: 'warn'}]);
        }
        var fast = diagnostics.fast_context;
        if (fast) {
            rows.push(['Last 20 minutes', fast.reported_mode_changes + ' reported mode changes · ' +
                fast.distinct_consecutive_payloads + ' distinct payloads (max ' + fast.maximum_reported_observations + ')']);
            rows.push(['PV / load range', (fast.pv_range_w ? fast.pv_range_w.join('–') + ' W' : 'unknown') + ' / ' +
                (fast.load_range_w ? fast.load_range_w.join('–') + ' W' : 'unknown')]);
            rows.push(['Reported current direction', fast.latest_reported_current_direction || 'unknown', {code: true}]);
            rows.push(['Latest collection', duration(fast.latest_collection_age_seconds) + ' old · ' +
                (fast.collection_recent ? 'recent' : 'stale or unavailable') + ' · device freshness not verified']);
        }
        var charging = latest.charging_context;
        if (charging) {
            rows.push(['External charging', charging.external_charging_status +
                ' · EASUN current is not total battery current']);
            rows.push(['PV coverage', charging.pv_source_coverage, {code: true}]);
            rows.push(['Voltage rises without EASUN charge', charging.voltage_rises_without_easun_charge_reported]);
            rows.push(['Charging evidence labels', (charging.evidence_labels || []).join(', ') || 'None', {code: true}]);
        } else {
            rows.push(['Charging context', 'Not recorded']);
        }
        rows.push(['Telemetry provenance', latest.telemetry_audit
            ? 'Collection time only; device timestamp not persisted; reported current semantics and SOC accuracy unverified; ' +
              'battery temperature, external charger current, controller identity and array orientation unavailable. ' +
              'PV-side power and AC-output power are separate reports.' : 'Not recorded']);
        fillRows('diag-evidence', rows);
    }
    function renderCyclingGroup(latest) {
        var diagnostics = latest && latest.decision_diagnostics;
        if (!diagnostics || typeof diagnostics !== 'object' || !diagnostics.cycling) {
            fillRows('diag-cycling', [['Cycling analysis', 'Not recorded by this algorithm version']]);
            return;
        }
        var cycling = diagnostics.cycling;
        var coverage = diagnostics.mode_history_coverage || {};
        var notes = [];
        if (coverage.truncated) {
            notes.push('Truncated: only the newest ' + coverage.limit_runs + ' runs retained (' + coverage.omitted_older_runs +
                ' older runs omitted); no transition counted across the gap');
        }
        if (coverage.observation_gaps) {
            notes.push('Interrupted: ' + coverage.observation_gaps + ' observation gap(s) over ' +
                Math.round((coverage.observation_gap_limit_s || 600) / 60) + ' min (longest ' +
                Math.round((coverage.max_observation_gap_s || 0) / 60) + ' min); ' +
                (coverage.uncertain_mode_changes_across_gaps || 0) +
                ' uncertain mode change(s) across unobserved intervals, not counted as transitions');
        }
        var transitions = joinCounts(cycling.transition_states);
        fillRows('diag-cycling', [
            ['Status', cycling.detected ? 'Detected' : 'Not established', {tone: cycling.detected ? 'warn' : null}],
            ['Reported mode changes', cycling.reported_mode_changes + ' in collection order · physical switch times unknown'],
            ['Short runs', cycling.short_runs + ' of ' + cycling.complete_runs + ' bounded runs shorter than ' +
                Math.round(((cycling.criteria || {}).short_run_max_seconds || 0) / 60) + ' min'],
            ['Detection confidence', cycling.detection_confidence + ' (heuristic, not a calibrated probability)'],
            ['Transitions', transitions || 'None recorded', {code: true}],
            ['Mode history', notes.length ? notes.join('. ') : 'No truncation or observation gaps recorded',
                {tone: notes.length ? 'warn' : null}],
            ['Interpretation', 'Frequent switching alone is not threshold evidence.']
        ]);
    }
    function renderVoltageGroup(latest) {
        var rows = [];
        if (latest) {
            rows.push(['Latest battery voltage', batteryVoltageText(latest)]);
            rows.push(['Reported charge / discharge', (latest.charge_a == null ? '—' : latest.charge_a) + ' / ' +
                (latest.discharge_a == null ? '—' : latest.discharge_a) + ' A']);
        } else {
            rows.push(['Latest battery voltage', 'Not recorded']);
        }
        var diagnostics = latest && latest.decision_diagnostics;
        if (diagnostics && typeof diagnostics === 'object' && diagnostics.voltage_comparison) {
            var voltage = diagnostics.voltage_comparison;
            var contrast = latest.exploratory_voltage_contrast || {};
            if (voltage.status === 'ESTIMATED') {
                rows.push(['Comparison', 'Estimated (exploratory): ' + contrast.contrast_v +
                    ' V Line-not-charging minus Invert-discharging; not verified sag', {tone: 'ok'}]);
            } else {
                rows.push(['Comparison', 'Blocked', {tone: 'warn'}]);
                rows.push(['Blocked by', (voltage.blocked_by || []).join(', ') || '—', {code: true}]);
            }
            rows.push(['Comparable pairs', (voltage.comparable_pairs || 0) + ' of ' + voltage.required_pairs + ' required']);
            var exclusions = joinCounts(contrast.primary_exclusions);
            var rejections = joinCounts(contrast.pair_rejections);
            if (exclusions) rows.push(['Excluded observations', exclusions, {code: true}]);
            if (rejections) rows.push(['Rejected comparisons', rejections, {code: true}]);
            var confounded = contrast.charging_confounded_contrast;
            rows.push(['Charging-confounded difference', confounded && confounded.median_difference_v != null
                ? 'Median ' + confounded.median_difference_v + ' V over ' + confounded.matched_pairs +
                    ' pairs (Line charging at median ' + confounded.median_line_charge_a +
                    ' A minus Invert discharging) — description only, not sag or rebound; never used for proposals'
                : 'No matched Line-charging comparison']);
            var classes = diagnostics.evidence_classes || {};
            rows.push(['Charging / discharging evidence', Object.keys(classes).map(function (key) {
                var item = classes[key] || {};
                var range = Array.isArray(item.voltage_range_v) ? item.voltage_range_v.join('–') + ' V' : '—';
                return key + ': ' + item.count + ' (' + range + ')';
            }).join('; ') || 'No complete observations', {code: true}]);
        } else if (latest) {
            rows.push(['Comparison', 'Not recorded by this algorithm version']);
        }
        var behavior = latest && latest.battery_response && typeof latest.battery_response === 'object'
            ? latest.battery_response : null;
        if (behavior) {
            var intervals = Array.isArray(behavior.recent_intervals) ? behavior.recent_intervals : [];
            var last = intervals.length ? intervals[intervals.length - 1] : null;
            rows.push(['Capacity', behavior.capacity_status, {code: true}]);
            rows.push(['Voltage slope', behavior.latest_voltage_slope_v_per_hour == null ? 'Unknown'
                : behavior.latest_voltage_slope_v_per_hour + ' V/h']);
            if (last) {
                rows.push(['Latest current deltas', 'charge/discharge ' + last.charge_current_delta_a_reported + '/' +
                    last.discharge_current_delta_a_reported + ' A']);
            }
            rows.push(['Throughput (reported proxy)', behavior.throughput_status + ': +' +
                (behavior.charged_ah_proxy == null ? 'unavailable' : behavior.charged_ah_proxy + ' Ah') + ' / −' +
                (behavior.discharged_ah_proxy == null ? 'unavailable' : behavior.discharged_ah_proxy + ' Ah')]);
            rows.push(['Freshness / confidence', behavior.freshness + ' · ' + behavior.confidence, {code: true}]);
        } else if (latest) {
            rows.push(['Battery response', 'Diagnostics not recorded']);
        }
        fillRows('diag-voltage', rows);
    }
    function renderHypothesisGroup(latest, health) {
        var proposed = latest && latest.proposed_voltage != null ? latest.proposed_voltage : latest && latest.candidate_voltage;
        var proposalLabel = latest && latest.proposed_voltage != null ? 'proposed' : 'candidate';
        var progress = latest && latest.confirmation_progress;
        var cooldown = latest ? latest.cooldown_remaining_seconds : health.cooldown_remaining_seconds;
        var rows = [
            ['Monitored threshold', monitoredThreshold(latest)],
            ['Current setting', currentThreshold(latest), {tone: verifiedThreshold(latest) == null ? null : 'ok'}],
            ['Historical reference', historicalReference(latest)],
            ['Hypothetical proposal', proposed != null ? thresholdName(latest.threshold, latest) + ' ' + proposalLabel + ': ' +
                proposed + ' V (hypothetical; non-actionable)'
                : latest && /^HYPOTHETICAL_(RAISE|LOWER)$/.test(String(latest.decision || ''))
                    ? 'Voltage not recorded (hypothetical; non-actionable)' : 'No proposal'],
            ['Confirmation', progress ? progress.confirmed + ' of ' + progress.required + ' observations' : '—'],
            ['Stability', latest && latest.stability ? humanize(latest.stability) + ' (' + latest.stability + ')' : '—'],
            ['Cooldown remaining', duration(cooldown)]
        ];
        var hypothesis = latest && latest.decision_diagnostics && latest.decision_diagnostics.hypothesis_support;
        if (hypothesis) {
            rows.push(['Hypothesis support', hypothesis.supported
                ? 'Supported: ' + hypothesis.candidate + ' (hypothetical; non-actionable)'
                : 'Not supported: ' + ((hypothesis.unsupported_because || []).join(', ') || 'no candidate') +
                    '. Frequent switching alone is not threshold evidence.']);
        }
        var breakdown = latest && latest.confidence_breakdown;
        var confidence;
        if (breakdown) {
            confidence = 'Voltage contrast ' + (breakdown.voltage_contrast == null ? 'none (no estimate)' : breakdown.voltage_contrast) +
                ' · cycling detection ' + (breakdown.cycling_detection == null ? '—' : breakdown.cycling_detection) +
                ' · basis ' + (breakdown.basis || '—');
        } else {
            var value = latest && latest.confidence != null ? latest.confidence : health.confidence;
            confidence = value == null ? '—' : String(value);
        }
        rows.push(['Confidence', confidence]);
        rows.push(['Recorded reason codes', latest && Array.isArray(latest.reason_codes) && latest.reason_codes.length
            ? latest.reason_codes.join(', ') : 'None', {code: true}]);
        var experiment = latest && latest.virtual_threshold_experiment;
        if (experiment) {
            var candidates = Array.isArray(experiment.candidates) ? experiment.candidates : [];
            rows.push(['Virtual threshold pairs', candidates.length + ' exploratory virtual pairs; no winner; all non-actionable' +
                (experiment.blockers && experiment.blockers.length ? '; blocked: ' + experiment.blockers.join(', ') : ''), {
                detailLabel: 'Status and pairs',
                detail: experiment.status + '\n' + candidates.map(function (candidate) {
                    return candidate.back_to_utility_v + '/' + candidate.back_to_battery_v + ' V (sampled crossings ' +
                        ((candidate.sampled_utility_boundary_crossings == null ||
                            candidate.sampled_battery_boundary_crossings == null) ? 'unavailable' :
                            candidate.sampled_utility_boundary_crossings + '/' + candidate.sampled_battery_boundary_crossings) + ')';
                }).join('\n')}]);
        } else {
            rows.push(['Virtual threshold pairs', 'Not recorded']);
        }
        var hysteresis = latest && latest.adaptive_hysteresis;
        rows.push(['Adaptive hysteresis', hysteresis ? hysteresis.status + '; live width unknown; virtual widths ' +
            ((hysteresis.virtual_hysteresis_widths_v || []).join(', ') || 'unavailable') + ' V; no recommendation' : 'Not recorded']);
        var daily = latest && latest.daily_behavior_review;
        rows.push(['Daily review', daily ? daily.status + ' for ' + (daily.review_date_utc || 'latest review') + ' · ' +
            (daily.collection_count == null ? '—' : daily.collection_count) + ' collections in 24 h · recent-vs-prior median dV/dt change ' +
            (daily.median_voltage_slope_change_v_per_hour == null ? 'unavailable' : daily.median_voltage_slope_change_v_per_hour + ' V/h') +
            ' · ' + (daily.learning_state_reset ? 'learning state reset' : 'learning state preserved') : 'Waiting for daily review']);
        fillRows('diag-hypothesis', rows);
    }
    function renderJournalGroup(journal, latest) {
        if (!journal || journal.mode !== 'v2') {
            fillRows('diag-journal', [
                ['Mode', 'Journal v1: every evaluation is stored as a full entry' +
                    (journal && journal.v2_installed ? '; Journal v2 installed but not active' : '')],
                ['Repetitions', 'Not tracked in Journal v1']
            ]);
            return;
        }
        var state = journal.state || {};
        var repetition = journal.current_repetition;
        var outcome = repetition && repetition.outcome || {};
        var rows = [
            ['Mode', 'Journal v2 active — repeated outcomes are aggregated'],
            ['Active since', journal.changed_at, {time: true}],
            ['State revision', state.revision == null ? '—' : state.revision],
            ['Initialised from', state.initialized_from || '—', {code: true}],
            ['Last state evaluation', state.last_evaluated_at, {time: true}]
        ];
        if (repetition) {
            rows.push(['Current outcome', (outcome.decision || '—') + ' · ' + humanize(outcome.stability)]);
            rows.push(['Unchanged for', repetition.evaluation_count + ' evaluations (' + shortTime(repetition.first_seen_at) +
                ' – ' + shortTime(repetition.last_seen_at) + ')']);
            rows.push(['Evidence', repetition.new_evidence_count + ' new · ' + repetition.repeated_evidence_count +
                ' repeated or cached']);
        } else {
            rows.push(['Current outcome', 'No Journal v2 evaluation yet']);
        }
        if (latest && latest.snapshot_evaluated_at) {
            rows.push(['Diagnostics snapshot', latest.snapshot_evaluated_at, {time: true}]);
            if (latest.evaluations_since_snapshot) {
                rows.push(['Identical since snapshot', latest.evaluations_since_snapshot + ' evaluations (last ' +
                    shortTime(latest.last_evaluated_at) + ')']);
            }
        }
        if (journal.history_note) rows.push(['Note', journal.history_note]);
        fillRows('diag-journal', rows);
    }
    function renderLatestRaw() {
        var pre = byId('latest-raw');
        var disclosure = byId('latest-raw-disclosure');
        if (!pre) return;
        pre.textContent = '';
        pre.dataset.generated = '';
        if (disclosure && disclosure.open) {
            pre.textContent = currentLatest ? JSON.stringify(currentLatest, null, 2) : 'No latest evaluation.';
            pre.dataset.generated = 'true';
        }
    }
    function renderHealth(data) {
        var health = data.worker_health || {};
        var latest = data.latest_decision || null;
        currentLatest = latest;
        renderSummary(data);
        renderReasons(latest, health);
        renderWorkerGroup(data);
        renderEvidenceGroup(latest, health);
        renderCyclingGroup(latest);
        renderVoltageGroup(latest);
        renderHypothesisGroup(latest, health);
        renderJournalGroup(data.journal, latest);
        renderLatestRaw();
        summaryRendered = true;
        summaryLoadedAt = new Date().toISOString();
    }

    // ── Journal v2 event timeline ─────────────────────────────────────────
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
        BATTERY_OBSERVATION_CHANGED: 'Battery reading validity changed',
        BATTERY_RESPONSE_CHANGED: 'Battery response estimate changed',
        WORKER_FAILED: 'Worker reported an error',
        WORKER_RECOVERED: 'Worker recovered'
    };
    var eventTones = {
        WORKER_FAILED: 'bad', WORKER_RECOVERED: 'ok', EVIDENCE_BLOCKED: 'warn', EVIDENCE_RECOVERED: 'ok',
        HYPOTHESIS_CREATED: 'proposal', HYPOTHESIS_UPDATED: 'proposal', HYPOTHESIS_CONFIRMED: 'proposal',
        HYPOTHESIS_REJECTED: 'info', DECISION_CHANGED: 'info', BATTERY_OBSERVATION_CHANGED: 'warn',
        JOURNAL_V2_CUTOVER: 'info', BASELINE_RECORDED: 'info'
    };
    // The default timeline shows the newest entries in order; nothing is filtered out by type.
    var DEFAULT_TIMELINE_LIMIT = 5;
    function confirmationChange(payload) {
        var from = payload && payload.from || {}, to = payload && payload.to || {};
        var reset = typeof from.stable_count === 'number' && typeof to.stable_count === 'number' && to.stable_count < from.stable_count;
        return {from: from, to: to, reset: reset, pendingChanged: (from.pending == null ? null : from.pending) !== (to.pending == null ? null : to.pending),
            countChanged: from.stable_count !== to.stable_count};
    }
    function outcomeText(outcome) {
        if (!outcome || typeof outcome !== 'object') return '—';
        return String(outcome.decision || '—').replace(/_/g, ' ') + ' · ' + humanize(outcome.stability);
    }
    function eventLabel(event) {
        var payload = event && event.payload || {};
        if (event && event.event_type === 'DECISION_CHANGED' && payload.from && payload.to &&
            payload.from.decision === payload.to.decision) {
            return 'Stability changed';
        }
        if (event && event.event_type === 'CONFIRMATION_CHANGED') {
            var change = confirmationChange(payload);
            if (change.reset) return 'Confirmation reset';
            if (change.pendingChanged && !change.countChanged) return 'Pending candidate changed';
        }
        return eventLabels[event && event.event_type] || 'Decision event';
    }
    function eventSummary(event) {
        var payload = event && event.payload || {};
        var type = event && event.event_type;
        if (type === 'DECISION_CHANGED') {
            var from = payload.from || {}, to = payload.to || {};
            if (from.decision === to.decision && to.decision) {
                return 'Decision stayed ' + String(to.decision).replace(/_/g, ' ') + '; stability ' +
                    humanize(from.stability) + ' → ' + humanize(to.stability) + '.';
            }
            return to.decision ? String(from.decision || '—').replace(/_/g, ' ') + ' → ' +
                String(to.decision).replace(/_/g, ' ') + ' (' + humanize(to.stability) + ').' : 'The recorded outcome changed.';
        }
        if (type === 'HYPOTHESIS_CREATED') {
            return payload.candidate_voltage != null ? 'Hypothetical ' + thresholdName(payload.threshold, null) + ' ' +
                payload.candidate_voltage + ' V recorded for review; non-actionable.' : 'A hypothetical proposal was recorded.';
        }
        if (type === 'HYPOTHESIS_REJECTED') {
            return payload.previous_candidate_voltage != null ? 'The hypothetical ' + payload.previous_candidate_voltage +
                ' V candidate is no longer active.' : 'The previous hypothetical proposal is no longer active.';
        }
        if (type === 'HYPOTHESIS_UPDATED') {
            return payload.from != null && payload.to != null ? 'Candidate ' + payload.from + ' V → ' + payload.to +
                ' V; non-actionable.' : 'A hypothetical proposal value changed.';
        }
        if (type === 'CONFIRMATION_CHANGED') {
            var change = confirmationChange(payload);
            var parts = [];
            if (change.countChanged) {
                parts.push('Compatible observations ' + (change.from.stable_count == null ? 'not recorded' : change.from.stable_count) +
                    ' → ' + (change.to.stable_count == null ? 'not recorded' : change.to.stable_count));
            }
            if (change.pendingChanged) {
                parts.push('pending candidate ' + pendingText(change.from.pending) + ' → ' + pendingText(change.to.pending));
            } else if (change.to.pending != null) {
                parts.push('pending candidate ' + pendingText(change.to.pending));
            }
            return parts.length ? parts.join('; ').replace(/^./, function (c) { return c.toUpperCase(); }) + '.'
                : 'The confirmation state changed.';
        }
        if (type === 'HYPOTHESIS_CONFIRMED') return 'Confirmation was recorded; this remains non-actionable.';
        if (type === 'EVIDENCE_BLOCKED') {
            var added = Array.isArray(payload.added) ? payload.added : [];
            return added.length ? added.slice(0, 2).map(explainReason).join(' ') +
                (added.length > 2 ? ' (+' + (added.length - 2) + ' more)' : '') : 'New evidence blockers were recorded.';
        }
        if (type === 'EVIDENCE_RECOVERED') {
            var removed = Array.isArray(payload.removed) ? payload.removed : [];
            return removed.length ? 'Cleared: ' + removed.slice(0, 2).map(explainReason).join(' ') +
                (removed.length > 2 ? ' (+' + (removed.length - 2) + ' more)' : '') : 'One or more evidence blockers cleared.';
        }
        if (type === 'BATTERY_OBSERVATION_CHANGED') {
            return payload.to ? 'Battery voltage reading ' + humanize(payload.from || 'unknown') + ' → ' + humanize(payload.to) + '.'
                : 'A reported battery measurement changed validity status.';
        }
        if (type === 'BATTERY_RESPONSE_CHANGED') {
            return payload.interpretation && payload.interpretation !== 'EMPIRICAL_RESPONSE_CHANGED_CAUSE_UNKNOWN'
                ? 'Reported response changed (' + payload.interpretation + ').' : 'Reported response changed; the cause is unknown.';
        }
        if (type === 'WORKER_FAILED') {
            return 'Worker ' + (payload.worker_id || event.worker_id || '') + ' reported a failure' +
                (payload.error ? ': ' + payload.error : '') + '.';
        }
        if (type === 'WORKER_RECOVERED') return 'Worker ' + (payload.worker_id || event.worker_id || '') + ' recovered.';
        if (type === 'JOURNAL_V2_CUTOVER') return 'Repeated outcomes are now aggregated; Journal v1 records remain in History.';
        if (type === 'BASELINE_RECORDED') return 'Starting outcome: ' + outcomeText(payload.to) + '.';
        return 'A material decision-journal change was recorded.';
    }
    // Equivalence rule for condensing: two ADJACENT events are merged only when their event_type,
    // worker_id and complete payload (compared as canonical JSON with sorted keys) are identical.
    // event_time, event_id and state_revision are per-occurrence identity, not content. Types whose
    // payload cannot establish that two occurrences are the same state change are never merged.
    var NEVER_GROUPED = {BATTERY_RESPONSE_CHANGED: true, JOURNAL_V2_CUTOVER: true, BASELINE_RECORDED: true};
    function canonical(value) {
        if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
        if (value && typeof value === 'object') {
            return '{' + Object.keys(value).sort().map(function (key) {
                return JSON.stringify(key) + ':' + canonical(value[key]);
            }).join(',') + '}';
        }
        return JSON.stringify(value === undefined ? null : value);
    }
    function equivalenceKey(event) {
        if (!event || NEVER_GROUPED[event.event_type] || !event.payload || typeof event.payload !== 'object') return null;
        return canonical([event.event_type, event.worker_id == null ? null : event.worker_id, event.payload]);
    }
    function groupEvents(events) {
        var groups = [];
        events.forEach(function (event) {
            var key = equivalenceKey(event);
            var last = groups[groups.length - 1];
            if (key !== null && last && last.key === key) { last.events.push(event); return; }
            var label = eventLabel(event);
            var tone = event.event_type === 'CONFIRMATION_CHANGED'
                ? (confirmationChange(event.payload).reset ? 'warn' : 'info') : eventTones[event.event_type] || 'neutral';
            groups.push({key: key, events: [event], label: label, summary: eventSummary(event), tone: tone});
        });
        return groups;
    }
    function renderEventGroup(group) {
        var newest = group.events[0];
        var oldest = group.events[group.events.length - 1];
        var item = make('li', 'adl-event');
        if (item.dataset) item.dataset.tone = group.tone;
        item.appendChild(timeElement(newest.event_time, 'Time unavailable'));
        var body = make('div', 'adl-event-body');
        var title = make('span', 'adl-event-title', group.label);
        body.appendChild(title);
        var count = group.events.length;
        if (count > 1) body.appendChild(make('span', 'adl-event-count', '×' + count));
        var summary = count > 1 ? group.summary + ' Identical event recorded ' + count + ' times since ' +
            shortTime(oldest.event_time) + '.' : group.summary;
        body.appendChild(make('p', 'adl-event-text', summary));
        item.appendChild(body);
        return item;
    }
    function renderRawJournalEvents(body) {
        if (!body) return;
        body.replaceChildren();
        rawJournalEvents.forEach(function (event) {
            var item = make('li');
            var details = make('details');
            var summary = make('summary');
            summary.appendChild(timeElement(event.event_time, 'Time unavailable'));
            summary.appendChild(make('span', null, eventLabel(event)));
            details.appendChild(summary);
            details.appendChild(make('p', 'adl-note', 'Recorded ' + (fullTime(event.event_time) || 'time unavailable') +
                '. ' + eventSummary(event)));
            details.appendChild(make('pre', null, JSON.stringify(event, null, 2)));
            item.appendChild(details);
            body.appendChild(item);
        });
        rawJournalEventsRendered = true;
    }
    function renderJournal(journal, latest) {
        var body = byId('journal-events-body');
        var rawBody = byId('journal-raw-events');
        var rawDisclosure = byId('journal-raw-events-disclosure');
        if (body) body.replaceChildren();
        if (rawBody) rawBody.replaceChildren();
        rawJournalEvents = [];
        rawJournalEventsRendered = false;
        if (!journal || journal.mode !== 'v2') {
            setText('decision-history-source', 'These rows are the retained Journal v1 evaluation history.');
            setText('journal-events-count', 'Journal v1');
            if (rawDisclosure) rawDisclosure.hidden = true;
            if (body) {
                body.appendChild(make('li', 'adl-empty', 'Journal v1 stores full evaluations; its separate event timeline ' +
                    'is unavailable. Stored evaluations are under History.'));
            }
            return;
        }
        setText('decision-history-source', 'These are retained Journal v1 evaluations; they do not replace the latest ' +
            'Journal v2 snapshot shown above.');
        rawJournalEvents = Array.isArray(journal.events) ? journal.events : [];
        var groups = groupEvents(rawJournalEvents);
        var visible = groups.slice(0, DEFAULT_TIMELINE_LIMIT);
        var hiddenEvents = rawJournalEvents.length - visible.reduce(function (sum, group) { return sum + group.events.length; }, 0);
        var count = byId('journal-events-count');
        if (count) count.textContent = rawJournalEvents.length ? rawJournalEvents.length + ' events returned · newest first' : '';
        setText('journal-raw-events-count', '(' + rawJournalEvents.length + ')');
        if (rawDisclosure) {
            rawDisclosure.hidden = !rawJournalEvents.length;
            if (rawDisclosure.open) renderRawJournalEvents(rawBody);
        }
        if (!body) return;
        visible.forEach(function (group) { body.appendChild(renderEventGroup(group)); });
        if (!visible.length) body.appendChild(make('li', 'adl-empty', 'No significant events yet.'));
        else if (hiddenEvents > 0) {
            body.appendChild(make('li', 'adl-empty', hiddenEvents + (hiddenEvents === 1 ? ' other event is' : ' other events are') +
                ' in the full event history.'));
        }
    }

    // ── History tables (deferred until History opens) ────────────────────
    function appendCells(row, values) {
        values.forEach(function (value) {
            var cell = make('td');
            cell.textContent = value == null || value === '' ? '—' : String(value);
            row.appendChild(cell);
        });
    }
    function appendTimeCell(row, value) {
        var cell = make('td');
        cell.appendChild(timeElement(value));
        row.appendChild(cell);
    }
    function renderDecision(item) {
        var tr = make('tr');
        appendTimeCell(tr, item.evaluated_at);
        var level = item.evidence_level;
        var evidence = (level === 'EXPLORATORY' ? 'Limited' : level === 'VERIFIED' ? 'Verified' : level || 'Evidence unavailable') +
            ' · battery ' + batteryVoltageText(item);
        appendCells(tr, [item.decision == null ? null : String(item.decision).replace(/_/g, ' '), evidence]);
        var reason = make('td'), detail = make('details', 'adl-record');
        var summary = make('summary', null, decisionReasonSummary(item));
        var pre = make('pre');
        detail.addEventListener('toggle', function () {
            if (detail.open && !pre.dataset.generated) {
                pre.textContent = 'Latest battery voltage: ' + batteryVoltageText(item) + '\n' + JSON.stringify(item, null, 2);
                pre.dataset.generated = 'true';
            }
        });
        detail.appendChild(summary); detail.appendChild(pre); reason.appendChild(detail); tr.appendChild(reason);
        return tr;
    }
    function batteryInterval(seconds) {
        // Collection intervals can be sub-second; never round them up to a minute.
        if (seconds == null) return '—';
        var value = Number(seconds);
        return value < 60 ? value + ' s' : duration(value);
    }
    function renderBatteryObservation(item) {
        var row = make('tr');
        appendCells(row, [item.data_kind || 'RECORDED_OBSERVATION']);
        appendTimeCell(row, item.collection_time);
        appendCells(row, [item.mode,
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
        var row = make('tr');
        appendCells(row, [item.data_kind || 'DERIVED_ESTIMATE']);
        appendTimeCell(row, item.interval_end_collection_time);
        appendCells(row, [item.operating_context,
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
        var observationsBody = byId('battery-observation-body');
        var behaviorBody = byId('battery-behavior-body');
        var status = byId('battery-grid-status');
        var more = byId('battery-load-more');
        if (observationsBody) observationsBody.replaceChildren();
        if (behaviorBody) behaviorBody.replaceChildren();
        if (status) status.textContent = 'Open History to load battery observations.';
        if (more) { more.hidden = true; more.disabled = true; }
    }
    async function loadBatteryHistory(reset) {
        var status = byId('battery-grid-status');
        var observationsBody = byId('battery-observation-body');
        var behaviorBody = byId('battery-behavior-body');
        if (batteryLoading) { if (reset) batteryPendingReset = true; return; }
        if (reset) {
            batteryGeneration++; batteryCursor = null;
            batteryLoadedDays = null;
            batterySeenObservations = new Set(); batterySeenBehavior = new Set();
            observationsBody.replaceChildren(); behaviorBody.replaceChildren();
            var resetButton = byId('battery-load-more');
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
            var more = byId('battery-load-more');
            more.hidden = !data.has_more; more.disabled = false; more.textContent = 'Load more observations';
            status.textContent = observationsBody.children.length
                ? observationsBody.children.length + ' collection records; ' + behaviorBody.children.length +
                    ' derived response intervals. ' + (data.external_charging_status || 'External charging remains unobserved.')
                : 'No battery observations in this period.';
        } catch (_) {
            if (requestGeneration === batteryGeneration && requested === days) {
                status.textContent = reset ? 'Could not load this range; observation results were cleared.'
                    : 'Battery history unavailable; previous rows retained. Retry Load more observations.';
                var moreButton = byId('battery-load-more');
                moreButton.hidden = false; moreButton.disabled = false;
                moreButton.textContent = reset || !batteryCursor ? 'Retry observations' : 'Retry load more observations';
            }
        } finally {
            batteryLoading = false;
            if (batteryPendingReset) { batteryPendingReset = false; loadBatteryHistory(true); }
        }
    }
    function appendDecisionItems(items) {
        var body = byId('decision-body');
        (Array.isArray(items) ? items : []).forEach(function (item) {
            var key = String(item.evaluation_id || item.evaluated_at) + ':' + String(item.evaluated_at);
            if (!seen.has(key)) { seen.add(key); body.appendChild(renderDecision(item)); }
        });
    }
    function renderDeferredDecisionItems() {
        if (!historyExpanded || !deferredDecisionItems.length) return;
        appendDecisionItems(deferredDecisionItems);
        deferredDecisionItems = [];
        var body = byId('decision-body');
        var status = byId('decision-status');
        status.textContent = body.children.length ? body.children.length + ' evaluations loaded' : 'No evaluations in this period.';
    }
    async function load(reset) {
        var status = byId('decision-status');
        var body = byId('decision-body');
        if (loading) { if (reset) pendingReset = true; return; }
        if (reset) {
            generation++; nextCursor = null; seen = new Set(); historyReasonOccurrences = Object.create(null);
            deferredDecisionItems = [];
            body.replaceChildren();
        }
        var requested = days, requestGeneration = generation, cursor = reset ? null : nextCursor;
        loading = true;
        status.textContent = cursor ? 'Loading older evaluations…' : 'Loading evaluations…';
        byId('decision-export').href = '/api/controls/decisions/export?days=' + days;
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
            var more = byId('decision-load-more'); more.hidden = !data.has_more; more.disabled = false;
            status.textContent = body.children.length ? body.children.length + ' evaluations loaded'
                : deferredDecisionItems.length ? 'Open History to display the ' + deferredDecisionItems.length + ' loaded evaluations.'
                    : 'No evaluations in this period.';
        } catch (_) {
            if (requestGeneration === generation && requested === days) {
                status.textContent = reset ? 'Could not load this range; previous results were cleared.' : 'Decision journal unavailable; previous results retained. Retry Load more.';
                var moreButton = byId('decision-load-more');
                if (!reset && nextCursor) moreButton.disabled = false;
                if (!cursor) renderUnavailable();
            }
        } finally {
            loading = false;
            if (pendingReset) { pendingReset = false; load(true); }
        }
    }
    function selectRange(button, buttons) {
        buttons.forEach(function (other) {
            var active = other === button;
            if (other.classList) other.classList.toggle('active', active);
            if (other.setAttribute) other.setAttribute('aria-pressed', active ? 'true' : 'false');
        });
    }
    document.addEventListener('DOMContentLoaded', function () {
        var historyDetails = byId('decision-history');
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
        var rawEventsDetails = byId('journal-raw-events-disclosure');
        if (rawEventsDetails) {
            rawEventsDetails.addEventListener('toggle', function () {
                if (rawEventsDetails.open && !rawJournalEventsRendered) renderRawJournalEvents(byId('journal-raw-events'));
            });
        }
        var latestRaw = byId('latest-raw-disclosure');
        if (latestRaw) {
            latestRaw.addEventListener('toggle', function () {
                var pre = byId('latest-raw');
                if (latestRaw.open && pre && !pre.dataset.generated) renderLatestRaw();
            });
        }
        var moreReasons = byId('decision-more-reasons');
        if (moreReasons) {
            moreReasons.addEventListener('click', function () {
                var technical = byId('technical-details');
                var group = byId('diag-reasons-group');
                if (technical) technical.open = true;
                if (group && group.scrollIntoView) group.scrollIntoView({block: 'start', behavior: 'smooth'});
                if (group && group.focus) group.focus({preventScroll: true});
            });
        }
        var retry = byId('decision-retry');
        if (retry) retry.addEventListener('click', function () { load(true); });
        var rangeButtons = Array.prototype.slice.call(document.querySelectorAll('[data-decision-days]'));
        rangeButtons.forEach(function (button) {
            button.addEventListener('click', function () {
                days = Number(button.dataset.decisionDays); selectRange(button, rangeButtons); load(true);
                if (historyExpanded) loadBatteryHistory(true);
                else clearBatteryHistory();
            });
        });
        byId('decision-load-more').addEventListener('click', function () {
            this.disabled = true; load(false);
        });
        byId('battery-load-more').addEventListener('click', function () {
            this.disabled = true; loadBatteryHistory(false);
        });
        load(true);
        if (historyExpanded) loadBatteryHistory(true);
    });
}());
