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
        dom.dashboardWarnings = document.getElementById("dashboard-warnings");
        dom.warningsBody = document.getElementById("warnings-body");
        dom.snapshotTimestamp = document.getElementById("snapshot-timestamp");
        dom.loadsTableBody = document.getElementById("loads-table-body");
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

        /* Clear existing rows */
        dom.loadsTableBody.textContent = "";

        for (var i = 0; i < loads.length; i++) {
            var load = loads[i];
            if (!load || typeof load !== "object") { continue; }

            var displayName = safeText(load.display_name);
            var currentlyOn = load.currently_on === true;
            var configuredWatts = formatWatts(load.configured_load_watts);
            var controllable = formatYesNo(load.controllable);
            var status = safeText(load.status);
            var roles = formatRoles(load.roles);
            var isLifeSupport = load.is_life_support === true;

            if (currentlyOn) { onCount++; } else { offCount++; }

            var tr = document.createElement("tr");

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
            tr.appendChild(tdName);

            /* State cell (ON/OFF badge) */
            var tdState = document.createElement("td");
            var stateTag = document.createElement("span");
            stateTag.className = currentlyOn ? "tag is-success" : "tag is-light";
            stateTag.textContent = currentlyOn ? "ON" : "OFF";
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

            /* Roles cell */
            var tdRoles = document.createElement("td");
            tdRoles.textContent = roles;
            tr.appendChild(tdRoles);

            dom.loadsTableBody.appendChild(tr);
        }

        dom.summaryTotalLoads.textContent = String(totalLoads);
        dom.summaryOnCount.textContent = String(onCount);
        dom.summaryOffCount.textContent = String(offCount);
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
