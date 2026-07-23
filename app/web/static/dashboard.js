/* dashboard.js — dessmonitor read-only dashboard
 *
 * Implements short polling against the authenticated GET /control/state
 * endpoint with a responsive state machine, bounded retry, and safe DOM
 * rendering.
 *
 * Rules:
 * - One request in flight maximum (AbortController)
 * - Recursive setTimeout scheduling (no setInterval)
 * - Timeout cleanup in all paths
 * - Cancellation on unload
 * - textContent only for all API values; no innerHTML for any API value
 * - No eval, localStorage, inline handlers
 * - No external resources
 */

(function () {
    "use strict";

    /* -----------------------------------------------------------------------
     * Constants
     * -----------------------------------------------------------------------
     */

    var POLL_NORMAL_MS = 5000;
    var POLL_OFFLINE_MS = 10000;
    var POLL_HIDDEN_MS = 30000;
    var REQUEST_TIMEOUT_MS = 10000;
    var STALE_THRESHOLD_MS = 15000;
    var OFFLINE_THRESHOLD_MS = 60000;

    var MAX_CONSECUTIVE_BACKOFF = 3; /* failures 0-2: normal, 3+: offline */

    /* -----------------------------------------------------------------------
     * State
     * -----------------------------------------------------------------------
     */

    var connectionState = "connecting"; /* connecting|online|stale|degraded|offline */
    var lastSuccessfulResponseTime = null;
    var lastSnapshot = null;
    var consecutiveFailures = 0;

    var currentAbortController = null;
    var pollTimeoutId = null;
    var staleTimerId = null;
    var offlineTimerId = null;

    /* -----------------------------------------------------------------------
     * DOM references (cached once after DOM ready) — deferred script ensures
     * the DOM is parsed, but we guard with DOMContentLoaded anyway.
     * -----------------------------------------------------------------------
     */

    var dom = {};

    function cacheDom() {
        dom.connectionBadge = document.getElementById("connection-state-badge");
        dom.connectionTags = document.getElementById("connection-state-tags");
        dom.lastRefresh = document.getElementById("last-refresh");
        dom.dashboardUnavailable = document.getElementById("dashboard-unavailable");
        dom.dashboardContent = document.getElementById("dashboard-content");
        dom.summaryStatus = document.getElementById("summary-status");
        dom.summaryApiStatus = document.getElementById("summary-api-status");
        dom.summaryTotalLoads = document.getElementById("summary-total-loads");
        dom.summaryOnCount = document.getElementById("summary-on-count");
        dom.summaryOffCount = document.getElementById("summary-off-count");
        dom.summaryUnknownCount = document.getElementById("summary-unknown-count");
        dom.dashboardWarnings = document.getElementById("dashboard-warnings");
        dom.warningsBody = document.getElementById("warnings-body");
        dom.snapshotTimestamp = document.getElementById("snapshot-timestamp");
        dom.loadsTableBody = document.getElementById("loads-table-body");
    dom.startupResetBadge = document.getElementById("startup-reset-badge");
    dom.startupResetInfo = document.getElementById("startup-reset-info");
    dom.startupResetInfoText = document.getElementById("startup-reset-info-text");
    dom.sensorsTableBody = document.getElementById("sensors-table-body");
    }

    /* -----------------------------------------------------------------------
     * Connection state machine
     * -----------------------------------------------------------------------
     */

    var STATE_CLASSES = {
        connecting: "is-info is-connecting",
        online: "is-success is-online",
        stale: "is-warning is-stale",
        degraded: "is-warning is-degraded",
        offline: "is-danger is-offline"
    };

    var STATE_LABELS = {
        connecting: "Connecting\u2026",
        online: "Online",
        stale: "Stale data",
        degraded: "System degraded",
        offline: "Offline"
    };

    function setConnectionState(newState) {
        if (connectionState === newState) {
            return;
        }
        connectionState = newState;
        dom.connectionBadge.textContent = STATE_LABELS[newState] || newState;
        dom.connectionBadge.className = "tag " + (STATE_CLASSES[newState] || "");

        /* Stale visual treatment on main area */
        if (newState === "stale" || newState === "offline") {
            dom.dashboardContent.classList.add("is-stale-data");
        } else {
            dom.dashboardContent.classList.remove("is-stale-data");
        }
    }

    /* -----------------------------------------------------------------------
     * Stale / offline timer management
     * -----------------------------------------------------------------------
     */

    function clearStaleTimers() {
        if (staleTimerId) {
            clearTimeout(staleTimerId);
            staleTimerId = null;
        }
        if (offlineTimerId) {
            clearTimeout(offlineTimerId);
            offlineTimerId = null;
        }
    }

    function startStaleTimers() {
        clearStaleTimers();
        staleTimerId = setTimeout(function () {
            if (connectionState === "online") {
                setConnectionState("stale");
            }
        }, STALE_THRESHOLD_MS);
        offlineTimerId = setTimeout(function () {
            if (connectionState === "online" || connectionState === "stale") {
                setConnectionState("offline");
            }
        }, OFFLINE_THRESHOLD_MS);
    }

    /* -----------------------------------------------------------------------
     * Delay computation
     * -----------------------------------------------------------------------
     */

    function computeDelay() {
        if (document.hidden) {
            return POLL_HIDDEN_MS;
        }
        if (connectionState === "offline") {
            return POLL_OFFLINE_MS;
        }
        if (consecutiveFailures >= MAX_CONSECUTIVE_BACKOFF) {
            return POLL_OFFLINE_MS;
        }
        return POLL_NORMAL_MS;
    }

    /* -----------------------------------------------------------------------
     * Formatting helpers
     * -----------------------------------------------------------------------
     */

    function safeText(value) {
        if (value === null || value === undefined || value === "") {
            return "-";
        }
        return String(value);
    }

    function formatYesNo(value) {
        if (value === true) { return "Yes"; }
        if (value === false) { return "No"; }
        return "-";
    }

    function formatOnOff(value) {
        if (value === true) { return "ON"; }
        if (value === false) { return "OFF"; }
        return "-";
    }

    function formatWatts(value) {
        if (value === null || value === undefined || value === "") {
            return "-";
        }
        return String(value) + " W";
    }

    function formatRoles(roles) {
        if (!Array.isArray(roles) || roles.length === 0) {
            return "-";
        }
        return roles.map(function (r) { return String(r); }).join(", ");
    }

    function formatTimestamp(isoString) {
        if (!isoString || isoString === "" || isoString === "-") {
            return "-";
        }
        try {
            var d = new Date(isoString);
            if (isNaN(d.getTime())) {
                return "-";
            }
            return d.toLocaleString();
        } catch (e) {
            return "-";
        }
    }

    /* Startup reset badge rendering */
    function renderStartupReset(snapshot) {
        if (!snapshot) {
            return;
        }
        var startupResetStatus = snapshot.startup_reset_status;
        var gateOpen = snapshot.startup_reset_gate_open;

        if (!startupResetStatus) {
            dom.startupResetBadge.classList.add("is-hidden");
            return;
        }

        dom.startupResetBadge.classList.remove("is-hidden");

        var badgeClass = "tag is-info";
        var label = "Startup Reset: " + startupResetStatus;

        if (startupResetStatus === "in_progress") {
            badgeClass = "tag is-warning";
            dom.startupResetInfoText.textContent = "Resetting switches to OFF...";
        } else if (startupResetStatus === "confirmed") {
            badgeClass = "tag is-success";
            dom.startupResetInfoText.textContent = "All switches confirmed OFF.";
        } else if (startupResetStatus === "blocked") {
            badgeClass = "tag is-danger";
            dom.startupResetInfoText.textContent = "Startup reset blocked — some switches not confirmed OFF.";
        } else if (startupResetStatus === "cancelled" || startupResetStatus === "not_started") {
            badgeClass = "tag is-light";
            dom.startupResetInfoText.textContent = "";
        }

        dom.startupResetBadge.className = badgeClass;
        dom.startupResetBadge.textContent = label;

        if (gateOpen === true) {
            dom.startupResetInfoText.textContent = "Automation gate is open.";
        } else if (gateOpen === false && startupResetStatus === "confirmed") {
            dom.startupResetInfoText.textContent = "";
        }
    }

    /* -----------------------------------------------------------------------
     * Rendering
     * -----------------------------------------------------------------------
     */

    function renderUnavailable() {
        dom.dashboardUnavailable.classList.remove("is-hidden");
        dom.dashboardContent.classList.add("is-hidden");
    }

    function renderSnapshot(data) {
        /* data is the full JSON response from GET /control/state */
        if (!data || typeof data !== "object") {
            renderUnavailable();
            return;
        }

        var snapshot = data.snapshot;

        /* No snapshot -> unavailable */
        if (snapshot === null || snapshot === undefined) {
            renderUnavailable();
            return;
        }

        dom.dashboardUnavailable.classList.add("is-hidden");
        dom.dashboardContent.classList.remove("is-hidden");

        /* Top-level status */
        dom.summaryStatus.textContent = safeText(
            snapshot.status ? snapshot.status : data.status
        );
        dom.summaryApiStatus.textContent = "API: " + safeText(data.status);

        /* Snapshot timestamp */
        dom.snapshotTimestamp.textContent = "Snapshot: " +
            formatTimestamp(snapshot.created_at);

        /* Warnings */
        if (data.warnings && Array.isArray(data.warnings) && data.warnings.length > 0) {
            dom.dashboardWarnings.classList.remove("is-hidden");
            dom.warningsBody.textContent = data.warnings.join("; ");
        } else {
            dom.dashboardWarnings.classList.add("is-hidden");
        }

        /* Loads table */
        var loads = snapshot.loads;
        if (!Array.isArray(loads) || loads.length === 0) {
            dom.loadsTableBody.textContent = "";
            var emptyRow = document.createElement("tr");
            var emptyCell = document.createElement("td");
            emptyCell.colSpan = 6;
            emptyCell.className = "has-text-centered has-text-grey";
            emptyCell.textContent = "No loads available";
            emptyRow.appendChild(emptyCell);
            dom.loadsTableBody.appendChild(emptyRow);
            dom.summaryTotalLoads.textContent = "0";
            dom.summaryOnCount.textContent = "0";
            dom.summaryOffCount.textContent = "0";
            return;
        }

        var totalLoads = loads.length;
        var onCount = 0;
        var offCount = 0;
        var unknownCount = 0;

        /* Clear existing rows */
        dom.loadsTableBody.textContent = "";

        for (var i = 0; i < loads.length; i++) {
            var load = loads[i];
            if (!load || typeof load !== "object") { continue; }

            var displayName = safeText(load.display_name);
            var currentlyOn = load.currently_on;
            var configuredWatts = formatWatts(load.configured_load_watts);
            var controllable = formatYesNo(load.controllable);
            var status = safeText(load.status);
            var roles = formatRoles(load.roles);
            var isLifeSupport = load.is_life_support === true;
            var freshness = load.freshness || "";
            var isStale = (freshness === "stale");
            var mappingStatus = load.mapping_status || null;
            var startupResetResult = load.startup_reset_result || null;

            if (currentlyOn === true) {
                onCount++;
            } else if (currentlyOn === false) {
                offCount++;
            } else {
                unknownCount++;
            }

            var tr = document.createElement("tr");
            if (isStale) {
                tr.className = "is-stale-row";
            }

            /* Device Name cell */
            var tdName = document.createElement("td");
            tdName.textContent = displayName;
            if (isLifeSupport) {
                tdName.appendChild(document.createTextNode(" "));
                var lsTag = document.createElement("span");
                lsTag.className = "tag is-life-support";
                lsTag.textContent = "Life Support";
                tdName.appendChild(lsTag);
            }
            if (isStale) {
                tdName.appendChild(document.createTextNode(" "));
                var staleIndicator = document.createElement("span");
                staleIndicator.className = "tag is-warning is-light is-stale-indicator";
                staleIndicator.textContent = "stale";
                staleIndicator.title = "Observation is stale — may not reflect current state";
                tdName.appendChild(staleIndicator);
            }
            tr.appendChild(tdName);

            /* State cell (ON/OFF/UNKNOWN badge) */
            var tdState = document.createElement("td");
            var stateTag = document.createElement("span");
            if (currentlyOn === true) {
                stateTag.className = "tag is-success";
                stateTag.textContent = "ON";
            } else if (currentlyOn === false) {
                stateTag.className = "tag is-light";
                stateTag.textContent = "OFF";
            } else {
                stateTag.className = "tag is-light";
                stateTag.textContent = "---";
                stateTag.title = "Unknown";
            }
            tdState.appendChild(stateTag);
            tr.appendChild(tdState);

            /* Load (W) cell */
            var tdWatts = document.createElement("td");
            tdWatts.textContent = configuredWatts;
            tr.appendChild(tdWatts);

            /* Controllable cell */
            var tdCtrl = document.createElement("td");
            tdCtrl.textContent = controllable;
            tr.appendChild(tdCtrl);

            /* Status cell */
            var tdStatus = document.createElement("td");
            tdStatus.textContent = status;
            tr.appendChild(tdStatus);

            /* Mapping status cell */
            var tdMapping = document.createElement("td");
            if (mappingStatus === "invalid") {
                var invTag = document.createElement("span");
                invTag.className = "tag is-danger is-light";
                invTag.textContent = "mapping invalid";
                tdMapping.appendChild(invTag);
            } else if (mappingStatus && mappingStatus !== "valid") {
                tdMapping.textContent = mappingStatus;
            }
            tr.appendChild(tdMapping);

            /* Reset result cell */
            var tdReset = document.createElement("td");
            if (startupResetResult === "confirmed_off") {
                var confTag = document.createElement("span");
                confTag.className = "tag is-success is-light";
                confTag.textContent = "reset confirmed";
                tdReset.appendChild(confTag);
            } else if (startupResetResult === "pending") {
                var pendTag = document.createElement("span");
                pendTag.className = "tag is-warning is-light";
                pendTag.textContent = "pending OFF";
                tdReset.appendChild(pendTag);
            } else if (startupResetResult === "contradictory") {
                var contTag = document.createElement("span");
                contTag.className = "tag is-danger is-light";
                contTag.textContent = "reset contradictory";
                tdReset.appendChild(contTag);
            } else if (startupResetResult && startupResetResult.indexOf("skipped") === 0) {
                /* Skip rendering for skipped devices */
            } else if (startupResetResult && startupResetResult !== "unknown") {
                tdReset.textContent = startupResetResult;
            }
            tr.appendChild(tdReset);

            /* Roles cell */
            var tdRoles = document.createElement("td");
            tdRoles.textContent = roles;
            tr.appendChild(tdRoles);

            dom.loadsTableBody.appendChild(tr);
        }

        dom.summaryTotalLoads.textContent = String(totalLoads);
        dom.summaryOnCount.textContent = String(onCount);
        dom.summaryOffCount.textContent = String(offCount);
        if (dom.summaryUnknownCount) {
            dom.summaryUnknownCount.textContent = String(unknownCount);
        }
    }

    /* Sensors rendering */
    function renderSensors(snapshot) {
        if (!snapshot) { return; }
        var sensors = snapshot.sensors;
        if (!Array.isArray(sensors) || sensors.length === 0) {
            dom.sensorsTableBody.textContent = "";
            var emptyRow = document.createElement("tr");
            var emptyCell = document.createElement("td");
            emptyCell.colSpan = 5;
            emptyCell.className = "has-text-centered has-text-grey";
            emptyCell.textContent = "No sensors available";
            emptyRow.appendChild(emptyCell);
            dom.sensorsTableBody.appendChild(emptyRow);
            return;
        }

        dom.sensorsTableBody.textContent = "";

        for (var i = 0; i < sensors.length; i++) {
            var sensor = sensors[i];
            if (!sensor || typeof sensor !== "object") { continue; }

            var displayName = safeText(sensor.display_name);
            var rawValue = sensor.value;
            var unit = sensor.unit || "celsius";
            var observedAt = formatTimestamp(sensor.observed_at);
            var freshness = sensor.freshness || "";
            var status = sensor.status || "";
            var isStale = (freshness === "stale");
            var isUnavailable = (freshness === "unavailable");

            var tr = document.createElement("tr");
            if (isStale) {
                tr.className = "is-stale-row";
            }

            /* Sensor name */
            var tdName = document.createElement("td");
            tdName.textContent = displayName;
            tr.appendChild(tdName);

            /* Value */
            var tdValue = document.createElement("td");
            if (rawValue === null || rawValue === undefined) {
                tdValue.textContent = "N/A";
                tdValue.className = "has-text-grey-light";
            } else if (unit === "celsius") {
                tdValue.textContent = String(rawValue) + " \u00b0C";
            } else {
                tdValue.textContent = String(rawValue) + " " + unit;
            }
            tr.appendChild(tdValue);

            /* Observed */
            var tdObs = document.createElement("td");
            tdObs.textContent = observedAt;
            tr.appendChild(tdObs);

            /* Freshness */
            var tdFresh = document.createElement("td");
            var freshTag = document.createElement("span");
            if (freshness === "fresh") {
                freshTag.className = "tag is-success is-light";
                freshTag.textContent = "fresh";
            } else if (freshness === "stale") {
                freshTag.className = "tag is-warning is-light";
                freshTag.textContent = "stale";
            } else {
                freshTag.className = "tag is-light";
                freshTag.textContent = "unavailable";
            }
            tdFresh.appendChild(freshTag);
            tr.appendChild(tdFresh);

            /* Status */
            var tdStatus = document.createElement("td");
            var statusTag = document.createElement("span");
            if (status === "valid") {
                statusTag.className = "tag is-success is-light";
                statusTag.textContent = "valid";
            } else if (status === "stale") {
                statusTag.className = "tag is-warning is-light";
                statusTag.textContent = "stale";
            } else if (status === "invalid") {
                statusTag.className = "tag is-danger is-light";
                statusTag.textContent = "invalid";
            } else {
                statusTag.className = "tag is-light";
                statusTag.textContent = "unavailable";
            }
            tdStatus.appendChild(statusTag);
            tr.appendChild(tdStatus);

            dom.sensorsTableBody.appendChild(tr);
        }
    }

    /* -----------------------------------------------------------------------
     * Polling engine
     * -----------------------------------------------------------------------
     */

    function scheduleNextPoll(delayMs) {
        if (pollTimeoutId) {
            clearTimeout(pollTimeoutId);
        }
        pollTimeoutId = setTimeout(function () {
            pollTimeoutId = null;
            executePoll();
        }, delayMs);
    }

    function executePoll() {
        /* Cancel any prior in-flight fetch */
        if (currentAbortController) {
            currentAbortController.abort();
        }
        currentAbortController = new AbortController();

        var timeoutId = setTimeout(function () {
            currentAbortController.abort();
        }, REQUEST_TIMEOUT_MS);

        fetch("/control/state", {
            method: "GET",
            credentials: "same-origin",
            signal: currentAbortController.signal,
            headers: { "Accept": "application/json" }
        })
            .then(function (response) {
                clearTimeout(timeoutId);
                currentAbortController = null;

                if (response.status === 401) {
                    handleUnauthenticated();
                    return;
                }

                if (!response.ok) {
                    handleHttpError(response.status);
                    scheduleNextPoll(computeDelay());
                    return;
                }

                return response.json().then(function (data) {
                    handleSuccessfulResponse(data);
                    scheduleNextPoll(computeDelay());
                });
            })
            .catch(function (error) {
                clearTimeout(timeoutId);
                currentAbortController = null;

                if (error && error.name === "AbortError") {
                    /* Intentional abort from timeout or new request — do not
                     * treat as a network failure. The polling cycle may be
                     * restarted elsewhere. */
                    return;
                }

                handleNetworkError(error);
                scheduleNextPoll(computeDelay());
            });
    }

    function handleSuccessfulResponse(data) {
        consecutiveFailures = 0;
        lastSuccessfulResponseTime = Date.now();
        lastSnapshot = data;

        clearStaleTimers();

        /* Determine state from response */
        var apiStatus = (data && data.status) ? String(data.status) : "";
        if (apiStatus === "degraded" || apiStatus === "blocked") {
            setConnectionState("degraded");
        } else {
            setConnectionState("online");
        }

        renderSnapshot(data);
        renderSensors(data.snapshot);
        updateLastRefresh();
        startStaleTimers();
    }

    function handleHttpError(status) {
        consecutiveFailures++;

        if (status >= 400 && status < 500) {
            /* Client error — do not retry aggressively, but keep current state */
            if (lastSnapshot && connectionState === "online") {
                setConnectionState("degraded");
            }
        } else if (status >= 500) {
            /* Server error — bounded retry */
            if (consecutiveFailures >= MAX_CONSECUTIVE_BACKOFF) {
                setConnectionState("offline");
            }
        }
    }

    function handleNetworkError(/* error */) {
        consecutiveFailures++;
        if (consecutiveFailures >= MAX_CONSECUTIVE_BACKOFF) {
            setConnectionState("offline");
        }
    }

    function handleUnauthenticated() {
        /* Clear all timers and state */
        if (pollTimeoutId) {
            clearTimeout(pollTimeoutId);
            pollTimeoutId = null;
        }
        if (currentAbortController) {
            currentAbortController.abort();
            currentAbortController = null;
        }
        clearStaleTimers();
        consecutiveFailures = 0;

        /* Navigate to login */
        window.location.href = "/login";
    }

    function updateLastRefresh() {
        if (lastSuccessfulResponseTime) {
            var seconds = Math.floor((Date.now() - lastSuccessfulResponseTime) / 1000);
            var text = "Last updated: " + seconds + "s ago";
            dom.lastRefresh.textContent = text;
        }
    }

    /* -----------------------------------------------------------------------
     * Visibility handling
     * -----------------------------------------------------------------------
     */

    var visibilityRefreshPending = false;

    function onVisibilityChange() {
        if (document.hidden) {
            /* When hidden, the next scheduled poll will use POLL_HIDDEN_MS.
             * Cancel any pending poll and reschedule at hidden rate. */
            if (pollTimeoutId) {
                clearTimeout(pollTimeoutId);
                pollTimeoutId = null;
            }
            /* Do NOT start a new poll — let the current cycle finish and
             * reschedule. But if there's no active cycle, start one. */
            if (!currentAbortController) {
                scheduleNextPoll(POLL_HIDDEN_MS);
            }
        } else {
            /* Becoming visible — immediate poll, then resume normal */
            if (!visibilityRefreshPending) {
                visibilityRefreshPending = true;
                /* Cancel pending timer */
                if (pollTimeoutId) {
                    clearTimeout(pollTimeoutId);
                    pollTimeoutId = null;
                }
                /* Immediate poll */
                executePoll();
                /* Allow next visibility change after poll settles */
                visibilityRefreshPending = false;
            }
        }
    }

    /* -----------------------------------------------------------------------
     * Unload handling
     * -----------------------------------------------------------------------
     */

    function onBeforeUnload() {
        if (pollTimeoutId) {
            clearTimeout(pollTimeoutId);
            pollTimeoutId = null;
        }
        if (currentAbortController) {
            currentAbortController.abort();
            currentAbortController = null;
        }
        if (refreshTickId) {
            clearTimeout(refreshTickId);
            refreshTickId = null;
        }
        clearStaleTimers();
    }

    /* -----------------------------------------------------------------------
     * Startup
     * -----------------------------------------------------------------------
     */

    var refreshTickId = null;

    function scheduleRefreshTick() {
        if (refreshTickId) {
            clearTimeout(refreshTickId);
        }
        refreshTickId = setTimeout(function () {
            updateLastRefresh();
            scheduleRefreshTick();
        }, 1000);
    }

    function start() {
        cacheDom();
        document.addEventListener("visibilitychange", onVisibilityChange);
        window.addEventListener("beforeunload", onBeforeUnload);
        window.addEventListener("pagehide", onBeforeUnload);

        /* Begin polling immediately */
        executePoll();

        /* Update the "last updated" text periodically */
        scheduleRefreshTick();
    }

    /* Run after DOM is ready */
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})();
