(function () {
    'use strict';
    var days = 1;
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
        var value = item && item.threshold && settings ? settings[item.threshold] : null;
        return value == null ? 'Unknown (current settings unavailable)' : String(value) + ' V';
    }
    function historicalReference(item) {
        var settings = item && item.historical_settings;
        var value = item && item.threshold && settings ? settings[item.threshold] : null;
        return value == null ? 'Unavailable' : String(value) + ' V (historical; not current)';
    }
    function renderHealth(data) {
        var health = data.worker_health || {};
        var latest = data.decisions.length ? data.decisions[0] : null;
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
        setText('worker-cycling', (cycling.detected ? 'DETECTED' : 'Not established') + ': ' +
            cycling.reported_mode_changes + ' reported mode changes, ' + cycling.short_runs + ' of ' +
            cycling.complete_runs + ' bounded runs shorter than ' +
            Math.round(((cycling.criteria || {}).short_run_max_seconds || 0) / 60) + ' min; detection confidence ' +
            cycling.detection_confidence + '; ' + joinCounts(cycling.transition_states) +
            ' (collection order only; physical switch times unknown)');
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
                'analysis cap': samples.rejected_analysis_cap}) || 'none'));
        var hypothesis = diagnostics.hypothesis_support || {};
        setText('worker-hypothesis', hypothesis.supported
            ? 'Supported: ' + hypothesis.candidate + ' (hypothetical; non-actionable)'
            : 'Not supported: ' + ((hypothesis.unsupported_because || []).join(', ') || 'no candidate') +
                '. Frequent switching alone is not threshold evidence.');
        setText('worker-hold-reasons', (diagnostics.hold_reasons || []).join(', ') || 'None');
    }
    async function load() {
        var status = document.getElementById('decision-status');
        var body = document.getElementById('decision-body');
        var requested = days;
        document.getElementById('decision-export').href = '/api/controls/decisions?days=' + days;
        try {
            var response = await fetch('/api/controls/decisions?days=' + days, {cache: 'no-store'});
            if (!response.ok) throw new Error('unavailable');
            var data = await response.json();
            if (requested !== days) return;
            if (!data || !Array.isArray(data.decisions)) throw new Error('invalid');
            renderHealth(data);
            body.replaceChildren();
            data.decisions.forEach(function (item) {
                var tr = document.createElement('tr');
                var current = currentThreshold(item);
                var baseline = historicalReference(item);
                var progress = item.confirmation_progress;
                var confirmation = progress ? progress.confirmed + ' / ' + progress.required + ' (' + (item.stability || 'unknown') + ')' : item.stability;
                var evidence = item.evidence_level || '—';
                if (item.verified_sag && item.verified_sag.evidence_level) {
                    evidence += ' / SagLearner ' + item.verified_sag.evidence_level;
                }
                var proposed = item.proposed_voltage != null ? item.proposed_voltage : item.candidate_voltage;
                var proposalLabel = item.proposed_voltage != null ? 'proposed' : 'candidate';
                [item.evaluated_at, item.decision, evidence, item.time_provenance, item.threshold,
                    current, baseline, proposed == null ? null : proposed + ' V (' + proposalLabel + '; hypothetical)',
                    confirmation, duration(item.cooldown_remaining_seconds), item.pv_w, item.load_w,
                    item.battery_v, item.confidence]
                    .forEach(function (value) {
                        var td = document.createElement('td');
                        td.textContent = value == null ? '—' : String(value);
                        tr.appendChild(td);
                    });
                var reason = document.createElement('td'), detail = document.createElement('details');
                var summary = document.createElement('summary');
                summary.textContent = item.explanation || (Array.isArray(item.reason_codes) ? item.reason_codes.join(', ') : 'Evidence unavailable');
                var pre = document.createElement('pre');
                pre.textContent = JSON.stringify(item, null, 2);
                detail.appendChild(summary); detail.appendChild(pre); reason.appendChild(detail); tr.appendChild(reason);
                body.appendChild(tr);
            });
            status.textContent = data.decisions.length ? data.decisions.length + ' decisions' : 'No decisions in this period.';
        } catch (_) {
            if (requested === days) {
                status.textContent = 'Decision journal unavailable; previous results retained.';
                setText('worker-status', 'DEGRADED');
                setText('worker-reason', 'Decision journal unavailable');
            }
        }
    }
    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('[data-decision-days]').forEach(function (button) {
            button.addEventListener('click', function () { days = Number(button.dataset.decisionDays); load(); });
        });
        load();
    });
}());
