/* dashboard.js — dessmonitor read-only architect dashboard
 *
 * PR 0038: Architect Dashboard and Analytics UI.
 * Implements short polling against the authenticated GET /control/state
 * endpoint with architect-inspired dark theme rendering.
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
    var MAX_CONSECUTIVE_BACKOFF = 3;

    /* -----------------------------------------------------------------------
     * State
     * -----------------------------------------------------------------------
     */

    var connectionState = "connecting";
    var lastSuccessfulResponseTime = null;
    var lastSnapshot = null;
    var consecutiveFailures = 0;
    var currentAbortController = null;
    var pollTimeoutId = null;
    var staleTimerId = null;
    var offlineTimerId = null;

    /* -----------------------------------------------------------------------
     * DOM references
     * -----------------------------------------------------------------------
     */

    var dom = {};

    function cacheDom() {
        dom.navStatusBadge = document.getElementById("nav-status-badge");
        dom.dashboardAlert = document.getElementById("dashboard-alert");
        dom.alertText = document.getElementById("alert-text");
        dom.alertTime = document.getElementById("alert-time");
        dom.dashboardUnavailable = document.getElementById("dashboard-unavailable");
        dom.dashboardContent = document.getElementById("dashboard-content");
        dom.sourceIndicator = document.getElementById("source-indicator");
        dom.invFreshness = document.getElementById("inv-freshness");
        dom.dsPvPower = document.getElementById("ds-pv-power");
        dom.dsPvStatus = document.getElementById("ds-pv-status");
        dom.dsBatteryVoltage = document.getElementById("ds-battery-voltage");
        dom.dsBatterySub = document.getElementById("ds-battery-sub");
        dom.dsOutputVoltage = document.getElementById("ds-output-voltage");
        dom.dsOutputPower = document.getElementById("ds-output-power");
        dom.dsLoadSub = document.getElementById("ds-load-sub");
        dom.dsAcInput = document.getElementById("ds-ac-input");
        dom.invDetailAcVoltage = document.getElementById("inv-detail-ac-voltage");
        dom.invDetailLoad = document.getElementById("inv-detail-load");
        dom.invDetailChg = document.getElementById("inv-detail-chg");
        dom.invDetailDis = document.getElementById("inv-detail-dis");
        dom.invTimestamp = document.getElementById("inv-timestamp");
        dom.socPct = document.getElementById("socPct");
        dom.socGaugeFill = document.getElementById("socGaugeFill");
        dom.miniPv = document.getElementById("mini-pv");
        dom.miniSoc = document.getElementById("mini-soc");
        dom.miniLoads = document.getElementById("mini-loads");
        dom.miniWatertemp = document.getElementById("mini-watertemp");
        dom.loadsTableBody = document.getElementById("loads-table-body");
        dom.loadsOnBadge = document.getElementById("loads-on-badge");
        dom.loadsOffBadge = document.getElementById("loads-off-badge");
        dom.loadsUnknownBadge = document.getElementById("loads-unknown-badge");
        dom.totalLoadWatts = document.getElementById("total-load-watts");
        dom.sensorsPanel = document.getElementById("sensors-panel");
        dom.footerInvTime = document.getElementById("footer-inv-time");
        dom.footerAcIn = document.getElementById("footer-ac-in");
        dom.footerLoad = document.getElementById("footer-load");
        dom.footerDis = document.getElementById("footer-dis");
        dom.footerChg = document.getElementById("footer-chg");
        dom.footerUpdated = document.getElementById("footer-updated");
        /* Hidden compatibility elements */
        dom.connectionBadge = document.getElementById("connection-state-badge");
        dom.lastRefresh = document.getElementById("last-refresh");
        dom.summaryStatus = document.getElementById("summary-status");
        dom.summaryApiStatus = document.getElementById("summary-api-status");
        dom.summaryTotalLoads = document.getElementById("summary-total-loads");
        dom.summaryOnCount = document.getElementById("summary-on-count");
        dom.summaryOffCount = document.getElementById("summary-off-count");
        dom.summaryUnknownCount = document.getElementById("summary-unknown-count");
        dom.snapshotTimestamp = document.getElementById("snapshot-timestamp");
        dom.dsWorkingMode = document.getElementById("ds-working-mode");
        dom.dashboardWarnings = document.getElementById("dashboard-warnings");
        dom.warningsBody = document.getElementById("warnings-body");
        dom.startupResetBadge = document.getElementById("startup-reset-badge");
        dom.startupResetInfoText = document.getElementById("startup-reset-info-text");
        /* Detect if we are on the analytics page (no dashboard-content) */
        dom.isAnalyticsPage = !dom.dashboardContent;
    }

    /* -----------------------------------------------------------------------
     * Connection state machine
     * -----------------------------------------------------------------------
     */

    var STATE_LABELS = {
        connecting: "Connecting\u2026",
        online: "OK",
        stale: "Stale",
        degraded: "Degraded",
        offline: "Offline"
    };

    var STATE_CLASSES = {
        connecting: "degraded",
        online: "ok",
        stale: "degraded",
        degraded: "degraded",
        offline: "error"
    };

    function setConnectionState(newState) {
        if (connectionState === newState) { return; }
        connectionState = newState;
        if (dom.navStatusBadge) {
            dom.navStatusBadge.textContent = STATE_LABELS[newState] || newState;
            dom.navStatusBadge.className = "dm-badge " + (STATE_CLASSES[newState] || "degraded");
        }
        if (dom.connectionBadge) {
            dom.connectionBadge.textContent = STATE_LABELS[newState] || newState;
        }
        if (dom.dashboardContent) {
            if (newState === "stale" || newState === "offline") {
                dom.dashboardContent.classList.add("is-stale-data");
            } else {
                dom.dashboardContent.classList.remove("is-stale-data");
            }
        }
    }

    /* -----------------------------------------------------------------------
     * Stale / offline timer management
     * -----------------------------------------------------------------------
     */

    function clearStaleTimers() {
        if (staleTimerId) { clearTimeout(staleTimerId); staleTimerId = null; }
        if (offlineTimerId) { clearTimeout(offlineTimerId); offlineTimerId = null; }
    }

    function startStaleTimers() {
        clearStaleTimers();
        staleTimerId = setTimeout(function () {
            if (connectionState === "online") { setConnectionState("stale"); }
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
        if (document.hidden) { return POLL_HIDDEN_MS; }
        if (connectionState === "offline") { return POLL_OFFLINE_MS; }
        if (consecutiveFailures >= MAX_CONSECUTIVE_BACKOFF) { return POLL_OFFLINE_MS; }
        return POLL_NORMAL_MS;
    }

    /* -----------------------------------------------------------------------
     * Formatting helpers
     * -----------------------------------------------------------------------
     */

    function safeText(value) {
        if (value === null || value === undefined || value === "") { return "-"; }
        return String(value);
    }

    function formatCompact(val, unit) {
        if (val === null || val === undefined) { return "N/A"; }
        if (unit) { return String(val) + " " + unit; }
        return String(val);
    }

    function formatTimestamp(isoString) {
        if (!isoString || isoString === "" || isoString === "-") { return "-"; }
        try {
            var d = new Date(isoString);
            if (isNaN(d.getTime())) { return "-"; }
            return d.toLocaleString();
        } catch (e) { return "-"; }
    }

    function formatLondonTimestamp(isoString) {
        if (!isoString || isoString === "" || isoString === "-") { return "N/A"; }
        try {
            var d = new Date(isoString);
            if (isNaN(d.getTime())) { return "N/A"; }
            return d.toLocaleString("en-GB", { timeZone: "Europe/London" });
        } catch (e) { return "N/A"; }
    }

    /* -----------------------------------------------------------------------
     * Source indicator derivation
     * -----------------------------------------------------------------------
     */

    var INVERTER_MODES = {
        "Battery Mode": true, "PV Mode": true, "Invert Mode": true,
        "Power Saving Mode": true, "Standby Mode": true, "Bypass Mode": true
    };

    function getSourceState(inverter) {
        if (!inverter || !Array.isArray(inverter) || inverter.length === 0) { return "unknown"; }
        var inv = inverter[0];
        if (!inv || typeof inv !== "object") { return "unknown"; }
        var wm = inv.working_mode;
        if (!wm || wm === "") { return "unknown"; }
        if (wm === "Line Mode") { return "mains"; }
        if (wm === "Fault Mode") { return "fault"; }
        if (INVERTER_MODES[wm]) { return "inverter"; }
        return "unknown";
    }

    var SOURCE_LABELS = {
        inverter: "\u2600 Inverter",
        mains: "\u26A1 Mains",
        fault: "\u26A0 Fault",
        unknown: "\u2014 N/A"
    };

    var SOURCE_CLASSES = {
        inverter: "dm-badge ok",
        mains: "dm-badge degraded",
        fault: "dm-badge error",
        unknown: "dm-badge"
    };

    function renderSourceIndicator(snapshot) {
        if (!snapshot || !snapshot.inverter) { return; }
        var state = getSourceState(snapshot.inverter);
        if (dom.sourceIndicator) {
            dom.sourceIndicator.textContent = SOURCE_LABELS[state] || "\u2014 N/A";
            dom.sourceIndicator.className = SOURCE_CLASSES[state] || "dm-badge";
        }
    }

    /* -----------------------------------------------------------------------
     * Inverter freshness
     * -----------------------------------------------------------------------
     */

    function computeInverterFreshness(observedAtStr) {
        if (!observedAtStr || observedAtStr === "") { return "unavailable"; }
        try {
            var obsTime = new Date(observedAtStr).getTime();
            if (isNaN(obsTime)) { return "unavailable"; }
            var ageSeconds = (Date.now() - obsTime) / 1000;
            if (ageSeconds <= 150) { return "fresh"; }
            if (ageSeconds <= 600) { return "stale"; }
            return "unavailable";
        } catch (e) { return "unavailable"; }
    }

    function renderInverterFreshness(snapshot) {
        if (!snapshot) { return; }
        var inverter = snapshot.inverter;
        if (!Array.isArray(inverter) || inverter.length === 0) {
            if (dom.invFreshness) { dom.invFreshness.textContent = "unavailable"; dom.invFreshness.className = "dm-badge"; }
            return;
        }
        var inv = inverter[0];
        if (!inv || typeof inv !== "object") {
            if (dom.invFreshness) { dom.invFreshness.textContent = "unavailable"; dom.invFreshness.className = "dm-badge"; }
            return;
        }
        var state = computeInverterFreshness(inv.observed_at);
        var labels = { fresh: "fresh", stale: "stale", unavailable: "unavailable" };
        var classes = { fresh: "dm-badge ok", stale: "dm-badge degraded", unavailable: "dm-badge" };
        if (dom.invFreshness) {
            dom.invFreshness.textContent = labels[state] || "unavailable";
            dom.invFreshness.className = classes[state] || "dm-badge";
        }
    }

    /* -----------------------------------------------------------------------
     * SOC semicircle gauge (SVG)
     * -----------------------------------------------------------------------
     */

    function renderSocGauge(soc) {
        if (!dom.socGaugeFill || !dom.socPct) { return; }
        if (soc === null || soc === undefined) {
            dom.socPct.textContent = "\u2014";
            dom.socPct.style.color = "var(--text-dim)";
            dom.socGaugeFill.setAttribute("d", "M 11 70 A 54 54 0 0 1 11 70");
            dom.socGaugeFill.setAttribute("stroke", "#30363d");
            return;
        }
        var pct = Math.max(0, Math.min(100, soc));
        dom.socPct.textContent = Math.round(pct) + "%";
        /* Color gradient: red -> amber -> green */
        if (pct <= 30) {
            dom.socPct.style.color = "var(--red)";
            dom.socGaugeFill.setAttribute("stroke", "#ff3860");
        } else if (pct <= 60) {
            dom.socPct.style.color = "var(--amber)";
            dom.socGaugeFill.setAttribute("stroke", "#f5a623");
        } else {
            dom.socPct.style.color = "var(--green)";
            dom.socGaugeFill.setAttribute("stroke", "#23d160");
        }
        /* Arc: startAngle=PI, endAngle=PI + (soc/100)*PI */
        var angle = (pct / 100) * Math.PI;
        var cx = 65, cy = 70, r = 54;
        var startAngle = Math.PI;
        var endAngle = Math.PI + angle;
        var x1 = cx + r * Math.cos(startAngle);
        var y1 = cy + r * Math.sin(startAngle);
        var x2 = cx + r * Math.cos(endAngle);
        var y2 = cy + r * Math.sin(endAngle);
        var largeArc = angle > Math.PI ? 1 : 0;
        dom.socGaugeFill.setAttribute("d",
            "M " + x1.toFixed(1) + " " + y1.toFixed(1) +
            " A " + r + " " + r + " 0 " + largeArc + " 1 " + x2.toFixed(1) + " " + y2.toFixed(1)
        );
    }

    /* -----------------------------------------------------------------------
     * Operator summary (inverter hero metrics)
     * -----------------------------------------------------------------------
     */

    function renderOperatorSummary(snapshot) {
        if (!snapshot) { return; }
        var inverter = snapshot.inverter;
        if (!Array.isArray(inverter) || inverter.length === 0) {
            setInverterNA();
            return;
        }
        var inv = inverter[0];
        if (!inv || typeof inv !== "object") { setInverterNA(); return; }

        /* PV Power */
        if (dom.dsPvPower) {
            dom.dsPvPower.textContent = formatCompact(inv.pv_total_power, "");
        }
        if (dom.dsPvStatus && inv.pv_total_power !== null && inv.pv_total_power !== undefined) {
            dom.dsPvStatus.textContent = inv.pv_total_power > 0 ? "\u25B2 Generating" : "\u2014 Idle";
        } else if (dom.dsPvStatus) {
            dom.dsPvStatus.textContent = "\u2014";
        }

        /* Battery Voltage */
        if (dom.dsBatteryVoltage) {
            dom.dsBatteryVoltage.textContent = formatCompact(inv.battery_voltage, "");
        }
        if (dom.dsBatterySub) {
            var dis = inv.battery_current_dis;
            var chg = inv.battery_current_chg;
            if (dis !== null && dis !== undefined && dis > 0) {
                dom.dsBatterySub.textContent = "Discharging " + dis + "A";
            } else if (chg !== null && chg !== undefined && chg > 0) {
                dom.dsBatterySub.textContent = "Charging " + chg + "A";
            } else {
                dom.dsBatterySub.textContent = "\u2014";
            }
        }

        /* Output Voltage */
        if (dom.dsOutputVoltage) {
            dom.dsOutputVoltage.textContent = formatCompact(inv.output_voltage, "");
        }

        /* Output Power */
        if (dom.dsOutputPower) {
            dom.dsOutputPower.textContent = formatCompact(inv.output_power, "");
        }

        /* AC Input */
        if (dom.dsAcInput) {
            dom.dsAcInput.textContent = formatCompact(inv.ac_input_voltage, "");
        }

        /* Load percentage */
        if (dom.invDetailLoad) {
            dom.invDetailLoad.textContent = formatCompact(inv.ac_output_load, "%");
        }
        if (dom.dsLoadSub) {
            var loadPct = inv.ac_output_load;
            if (loadPct !== null && loadPct !== undefined) {
                dom.dsLoadSub.textContent = "Load: " + loadPct + "%";
            } else {
                dom.dsLoadSub.textContent = "\u2014";
            }
        }

        /* Detail row */
        if (dom.invDetailAcVoltage) {
            dom.invDetailAcVoltage.textContent = formatCompact(inv.ac_input_voltage, "V");
        }
        if (dom.invDetailChg) {
            dom.invDetailChg.textContent = formatCompact(inv.battery_current_chg, "A");
        }
        if (dom.invDetailDis) {
            dom.invDetailDis.textContent = formatCompact(inv.battery_current_dis, "A");
        }

        /* Inverter timestamp */
        if (dom.invTimestamp) {
            dom.invTimestamp.textContent = formatLondonTimestamp(inv.observed_at);
        }

        /* SOC gauge */
        renderSocGauge(inv.battery_soc);

        /* Mini stat cards */
        if (dom.miniPv) {
            dom.miniPv.textContent = formatCompact(inv.pv_total_power, "W");
        }
        if (dom.miniSoc) {
            dom.miniSoc.textContent = inv.battery_soc !== null && inv.battery_soc !== undefined
                ? Math.round(inv.battery_soc) + "%" : "\u2014";
        }

        /* Footer bar */
        if (dom.footerInvTime) {
            dom.footerInvTime.textContent = formatLondonTimestamp(inv.observed_at);
        }
        if (dom.footerAcIn) {
            dom.footerAcIn.textContent = formatCompact(inv.ac_input_voltage, "V");
        }
        if (dom.footerLoad) {
            dom.footerLoad.textContent = formatCompact(inv.ac_output_load, "%");
        }
        if (dom.footerDis) {
            dom.footerDis.textContent = formatCompact(inv.battery_current_dis, "A");
        }
        if (dom.footerChg) {
            dom.footerChg.textContent = formatCompact(inv.battery_current_chg, "A");
        }
    }

    function setInverterNA() {
        if (dom.dsPvPower) dom.dsPvPower.textContent = "N/A";
        if (dom.dsBatteryVoltage) dom.dsBatteryVoltage.textContent = "N/A";
        if (dom.dsOutputVoltage) dom.dsOutputVoltage.textContent = "N/A";
        if (dom.dsOutputPower) dom.dsOutputPower.textContent = "N/A";
        if (dom.dsAcInput) dom.dsAcInput.textContent = "N/A";
        if (dom.invDetailAcVoltage) dom.invDetailAcVoltage.textContent = "N/A";
        if (dom.invDetailLoad) dom.invDetailLoad.textContent = "N/A";
        if (dom.invDetailChg) dom.invDetailChg.textContent = "N/A";
        if (dom.invDetailDis) dom.invDetailDis.textContent = "N/A";
        if (dom.invTimestamp) dom.invTimestamp.textContent = "N/A";
        if (dom.invFreshness) { dom.invFreshness.textContent = "unavailable"; dom.invFreshness.className = "dm-badge"; }
        renderSocGauge(null);
        if (dom.miniPv) dom.miniPv.textContent = "\u2014";
        if (dom.miniSoc) dom.miniSoc.textContent = "\u2014";
    }

    /* -----------------------------------------------------------------------
     * Alert strip
     * -----------------------------------------------------------------------
     */

    function renderAlertStrip(data) {
        if (!dom.dashboardAlert) { return; }
        var apiStatus = (data && data.status) ? String(data.status) : "";
        if (apiStatus === "degraded" || apiStatus === "blocked") {
            dom.dashboardAlert.classList.remove("hidden");
            if (dom.alertText) {
                dom.alertText.textContent = "Control state snapshot degraded — API reporting " + apiStatus + " state";
            }
            if (dom.alertTime && data.snapshot && data.snapshot.created_at) {
                dom.alertTime.textContent = "Snapshot: " + formatTimestamp(data.snapshot.created_at);
            }
        } else {
            dom.dashboardAlert.classList.add("hidden");
        }
    }

    /* -----------------------------------------------------------------------
     * Loads table
     * -----------------------------------------------------------------------
     */

    function renderLoadsTable(snapshot) {
        if (!dom.loadsTableBody) { return; }
        var loads = snapshot.loads;
        if (!Array.isArray(loads) || loads.length === 0) {
            dom.loadsTableBody.textContent = "";
            var emptyRow = document.createElement("tr");
            var emptyCell = document.createElement("td");
            emptyCell.colSpan = 4;
            emptyCell.style.cssText = "text-align:center; color:var(--text-dim);";
            emptyCell.textContent = "No loads available";
            emptyRow.appendChild(emptyCell);
            dom.loadsTableBody.appendChild(emptyRow);
            if (dom.loadsOnBadge) dom.loadsOnBadge.textContent = "0 ON";
            if (dom.loadsOffBadge) dom.loadsOffBadge.textContent = "0 OFF";
            if (dom.loadsUnknownBadge) dom.loadsUnknownBadge.textContent = "0 UNK";
            if (dom.totalLoadWatts) dom.totalLoadWatts.textContent = "\u2014";
            if (dom.miniLoads) dom.miniLoads.textContent = "0 / 0";
            return;
        }

        dom.loadsTableBody.textContent = "";
        var onCount = 0, offCount = 0, unknownCount = 0;
        var totalActiveW = 0;
        var deviceEnergyMap = window._deviceEnergyToday || {};

        for (var i = 0; i < loads.length; i++) {
            var load = loads[i];
            if (!load || typeof load !== "object") { continue; }
            var displayName = safeText(load.display_name);
            var currentlyOn = load.currently_on;
            var configuredWatts = load.configured_load_watts || 0;
            var observedPowerW = (load.observed_power_w != null) ? load.observed_power_w : null;
            var isLifeSupport = load.is_life_support === true;
            var freshness = load.freshness || "";
            var isStale = (freshness === "stale");

            if (currentlyOn === true) { onCount++; totalActiveW += (observedPowerW !== null ? observedPowerW : configuredWatts); }
            else if (currentlyOn === false) { offCount++; }
            else { unknownCount++; }

            var tr = document.createElement("tr");
            if (isStale) { tr.className = "is-stale-row"; }

            /* Device Name */
            var tdName = document.createElement("td");
            var nameSpan = document.createElement("span");
            nameSpan.className = "dm-device-name";
            nameSpan.textContent = displayName;
            tdName.appendChild(nameSpan);

            /* Freshness dot + age */
            var observedAt = load.observed_at || null;
            var ageSeconds = null;
            if (observedAt) {
                try {
                    ageSeconds = Math.floor((Date.now() - new Date(observedAt).getTime()) / 1000);
                } catch(e) {}
            }
            if (ageSeconds !== null) {
                var ageDot = document.createElement("span");
                ageDot.className = "dm-obs-dot";
                var ageText = ageSeconds < 60
                    ? ageSeconds + "s ago"
                    : ageSeconds < 3600
                        ? Math.floor(ageSeconds/60) + "m ago"
                        : Math.floor(ageSeconds/3600) + "h ago";
                if (ageSeconds < 30) {
                    ageDot.classList.add("fresh");
                } else if (ageSeconds < 120) {
                    ageDot.classList.add("stale");
                } else {
                    ageDot.classList.add("old");
                }
                ageDot.title = "Last observed: " + ageText + " (" + observedAt + ")";
                tdName.appendChild(document.createTextNode(" "));
                tdName.appendChild(ageDot);
            }
            if (isLifeSupport) {
                tdName.appendChild(document.createTextNode(" "));
                var lsTag = document.createElement("span");
                lsTag.className = "tag is-life-support";
                lsTag.textContent = "Life Support";
                tdName.appendChild(lsTag);
            }
            if (isStale) {
                tdName.appendChild(document.createTextNode(" "));
                var staleSpan = document.createElement("span");
                staleSpan.className = "dm-badge degraded";
                staleSpan.style.cssText = "font-size:0.6rem; padding:0.1rem 0.3rem;";
                staleSpan.textContent = "stale";
                staleSpan.title = "Observation is stale";
                tdName.appendChild(staleSpan);
            }
            /* Description */
            var descSpan = document.createElement("div");
            descSpan.className = "dm-device-desc";
            descSpan.textContent = (load.description || "\u2014");
            tdName.appendChild(descSpan);
            tr.appendChild(tdName);

            /* State */
            var tdState = document.createElement("td");
            if (currentlyOn === true) {
                var onSpan = document.createElement("span");
                onSpan.className = "dm-state-on";
                var dotOn = document.createElement("span");
                dotOn.className = "dm-state-dot on";
                onSpan.appendChild(dotOn);
                onSpan.appendChild(document.createTextNode("ON"));
                tdState.appendChild(onSpan);
            } else if (currentlyOn === false) {
                var offSpan = document.createElement("span");
                offSpan.className = "dm-state-off";
                var dotOff = document.createElement("span");
                dotOff.className = "dm-state-dot off";
                offSpan.appendChild(dotOff);
                offSpan.appendChild(document.createTextNode("OFF"));
                tdState.appendChild(offSpan);
            } else {
                tdState.textContent = "\u2014";
                tdState.style.color = "var(--text-dim)";
            }
            tr.appendChild(tdState);

            /* Power */
            var tdPower = document.createElement("td");
            var wattSpan = document.createElement("div");
            wattSpan.className = "dm-watt";
            if (observedPowerW !== null) {
                wattSpan.textContent = observedPowerW.toFixed(1) + " W";
                if (currentlyOn === true && observedPowerW < configuredWatts * 0.1) {
                    wattSpan.classList.add("dm-watt-idle");
                }
            } else {
                wattSpan.textContent = configuredWatts + " W";
            }
            if (currentlyOn !== true) { wattSpan.classList.add("dm-watt-off"); }
            tdPower.appendChild(wattSpan);
            var barW = (observedPowerW !== null) ? observedPowerW : configuredWatts;
            if (currentlyOn === true && barW > 0) {
                var wattBar = document.createElement("div");
                wattBar.className = "dm-watt-bar";
                var wattFill = document.createElement("div");
                wattFill.className = "dm-watt-bar-fill";
                var maxW = 2000;
                var pctW = Math.min(100, (barW / maxW) * 100);
                wattFill.style.width = pctW + "%";
                if (observedPowerW !== null && observedPowerW < configuredWatts * 0.1) {
                    wattFill.classList.add("dm-watt-bar-idle");
                }
                wattBar.appendChild(wattFill);
                tdPower.appendChild(wattBar);
            }
            if (configuredWatts > 0 && observedPowerW !== null) {
                var cfgNote = document.createElement("div");
                cfgNote.className = "dm-watt-cfg";
                cfgNote.textContent = "cfg: " + configuredWatts + " W";
                tdPower.appendChild(cfgNote);
            }
            tr.appendChild(tdPower);

            /* Today energy — Calc only (Meter unreliable due to pod restarts) */
            var devKey = (load.display_name || "").toLowerCase();
            var devEnergy = deviceEnergyMap[devKey];
            var configW = load.configured_load_watts || 0;
            var tdToday = document.createElement("td");
            tdToday.className = "dm-mono";
            if (devEnergy != null) {
                var onHours = devEnergy.on_hours || 0;
                var calcWh = devEnergy.avg_power_w
                    ? devEnergy.avg_power_w * onHours
                    : onHours * configW;
                if (calcWh > 0) {
                    tdToday.textContent = (calcWh / 1000).toFixed(2) + " kWh";
                    if (devEnergy.avg_power_w) {
                        tdToday.title = "avg " + devEnergy.avg_power_w.toFixed(0) + "W x " + onHours.toFixed(1) + "h (real power)";
                        tdToday.style.color = "var(--green)";
                    } else {
                        tdToday.title = configW + "W (config) x " + onHours.toFixed(1) + "h (estimated)";
                        tdToday.style.color = "var(--text-dim)";
                    }
                } else {
                    tdToday.textContent = "—";
                }
            } else {
                tdToday.textContent = "—";
            }
            tr.appendChild(tdToday);

            /* Roles */
            var tdRoles = document.createElement("td");
            tdRoles.style.cssText = "font-size:0.7rem; color:var(--text-dim); font-family:var(--mono);";
            var roles = load.roles;
            if (Array.isArray(roles) && roles.length > 0) {
                tdRoles.textContent = roles.join(", ");
            } else {
                tdRoles.textContent = "\u2014";
            }
            tr.appendChild(tdRoles);

            dom.loadsTableBody.appendChild(tr);
        }

        /* Update badges */
        if (dom.loadsOnBadge) dom.loadsOnBadge.textContent = onCount + " ON";
        if (dom.loadsOffBadge) dom.loadsOffBadge.textContent = offCount + " OFF";
        if (dom.loadsUnknownBadge) dom.loadsUnknownBadge.textContent = unknownCount + " UNK";
        if (dom.totalLoadWatts) dom.totalLoadWatts.textContent = totalActiveW + " W";
        if (dom.miniLoads) dom.miniLoads.textContent = onCount + " / " + loads.length;

        /* Hidden compatibility fields */
        if (dom.summaryTotalLoads) dom.summaryTotalLoads.textContent = String(loads.length);
        if (dom.summaryOnCount) dom.summaryOnCount.textContent = String(onCount);
        if (dom.summaryOffCount) dom.summaryOffCount.textContent = String(offCount);
        if (dom.summaryUnknownCount) dom.summaryUnknownCount.textContent = String(unknownCount);
    }

    /* -----------------------------------------------------------------------
     * Sensors panel
     * -----------------------------------------------------------------------
     */

    function renderSensorsPanel(snapshot) {
        if (!dom.sensorsPanel) { return; }
        var sensors = snapshot.sensors;
        dom.sensorsPanel.textContent = "";

        if (!Array.isArray(sensors) || sensors.length === 0) {
            var emptyDiv = document.createElement("div");
            emptyDiv.className = "dm-sensor";
            var emptyBody = document.createElement("div");
            emptyBody.className = "dm-sensor-body";
            var emptyDesc = document.createElement("div");
            emptyDesc.className = "dm-sensor-desc";
            emptyDesc.style.cssText = "text-align:center; padding:1rem;";
            emptyDesc.textContent = "No sensors available";
            emptyBody.appendChild(emptyDesc);
            emptyDiv.appendChild(emptyBody);
            dom.sensorsPanel.appendChild(emptyDiv);
            /* Also update watertemp mini stat */
            if (dom.miniWatertemp) { dom.miniWatertemp.textContent = "\u2014"; }
            return;
        }

        for (var i = 0; i < sensors.length; i++) {
            var sensor = sensors[i];
            if (!sensor || typeof sensor !== "object") { continue; }

            var displayName = safeText(sensor.display_name);
            var rawDescription = sensor.description || "";
            var rawValue = sensor.value;
            var unit = sensor.unit || "celsius";
            var freshness = sensor.freshness || "";
            var status = sensor.status || "";

            var sensorDiv = document.createElement("div");
            sensorDiv.className = "dm-sensor";

            /* Icon */
            var iconDiv = document.createElement("div");
            iconDiv.className = "dm-sensor-icon";
            iconDiv.textContent = "\uD83C\uDF21"; /* thermometer emoji */
            sensorDiv.appendChild(iconDiv);

            /* Body */
            var bodyDiv = document.createElement("div");
            bodyDiv.className = "dm-sensor-body";

            var nameDiv = document.createElement("div");
            nameDiv.className = "dm-sensor-name";
            nameDiv.textContent = rawDescription || displayName;
            bodyDiv.appendChild(nameDiv);

            var descDiv = document.createElement("div");
            descDiv.className = "dm-sensor-desc";
            descDiv.textContent = displayName;
            bodyDiv.appendChild(descDiv);

            /* Freshness/status row */
            var metaDiv = document.createElement("div");
            metaDiv.style.cssText = "margin-top:0.3rem; display:flex; gap:0.4rem; align-items:center;";
            if (freshness) {
                var freshSpan = document.createElement("span");
                freshSpan.className = "dm-freshness " + (freshness === "fresh" ? "fresh" : "stale");
                freshSpan.textContent = freshness;
                metaDiv.appendChild(freshSpan);
            }
            if (status) {
                var statusSpan = document.createElement("span");
                statusSpan.className = "dm-freshness " + (status === "valid" ? "fresh" : "stale");
                statusSpan.textContent = status;
                metaDiv.appendChild(statusSpan);
            }
            bodyDiv.appendChild(metaDiv);
            sensorDiv.appendChild(bodyDiv);

            /* Value */
            var valDiv = document.createElement("div");
            valDiv.className = "dm-sensor-val";
            if (rawValue === null || rawValue === undefined) {
                valDiv.textContent = "\u2014";
                valDiv.style.color = "var(--text-dim)";
            } else {
                valDiv.textContent = String(rawValue);
                var unitSpan = document.createElement("span");
                unitSpan.className = "dm-sensor-unit";
                if (unit === "celsius") {
                    unitSpan.textContent = "\u00B0C";
                } else {
                    unitSpan.textContent = " " + unit;
                }
                valDiv.appendChild(unitSpan);
            }
            sensorDiv.appendChild(valDiv);

            dom.sensorsPanel.appendChild(sensorDiv);

            /* Update watertemp mini stat if this is watertemp */
            if (displayName.toLowerCase().indexOf("watertemp") !== -1 || displayName.toLowerCase().indexOf("water") !== -1) {
                if (dom.miniWatertemp && rawValue !== null && rawValue !== undefined) {
                    dom.miniWatertemp.textContent = rawValue + "\u00B0C";
                } else if (dom.miniWatertemp) {
                    dom.miniWatertemp.textContent = "\u2014";
                }
            }
        }
    }

    /* -----------------------------------------------------------------------
     * Startup reset badge
     * -----------------------------------------------------------------------
     */

    function renderStartupReset(snapshot) {
        if (!snapshot) { return; }
        var startupResetStatus = snapshot.startup_reset_status;
        if (!startupResetStatus || !dom.startupResetBadge) { return; }
        dom.startupResetBadge.classList.remove("is-hidden");
        var badgeClass = "dm-badge";
        var label = "Startup Reset: " + startupResetStatus;
        if (startupResetStatus === "in_progress") {
            badgeClass += " degraded";
        } else if (startupResetStatus === "confirmed") {
            badgeClass += " ok";
        } else if (startupResetStatus === "blocked") {
            badgeClass += " error";
        }
        dom.startupResetBadge.className = badgeClass;
        dom.startupResetBadge.textContent = label;
    }

    /* -----------------------------------------------------------------------
     * Rendering
     * -----------------------------------------------------------------------
     */

    function renderUnavailable() {
        if (dom.dashboardUnavailable) dom.dashboardUnavailable.classList.remove("is-hidden");
        if (dom.dashboardContent) dom.dashboardContent.classList.add("is-hidden");
    }

    function renderSnapshot(data) {
        if (!data || typeof data !== "object") { renderUnavailable(); return; }
        var snapshot = data.snapshot;
        if (snapshot === null || snapshot === undefined) { renderUnavailable(); return; }

        if (dom.dashboardUnavailable) dom.dashboardUnavailable.classList.add("is-hidden");
        if (dom.dashboardContent) dom.dashboardContent.classList.remove("is-hidden");

        /* Alert strip */
        renderAlertStrip(data);

        /* Source indicator and operator summary */
        renderSourceIndicator(snapshot);
        renderInverterFreshness(snapshot);
        renderOperatorSummary(snapshot);

        /* Startup reset */
        renderStartupReset(snapshot);

        /* Warnings */
        if (data.warnings && Array.isArray(data.warnings) && data.warnings.length > 0) {
            if (dom.dashboardWarnings) dom.dashboardWarnings.classList.remove("is-hidden");
            if (dom.warningsBody) dom.warningsBody.textContent = data.warnings.join("; ");
        } else {
            if (dom.dashboardWarnings) dom.dashboardWarnings.classList.add("is-hidden");
        }

        /* Loads table */
        renderLoadsTable(snapshot);

        /* Sensors panel */
        renderSensorsPanel(snapshot);

        /* Hidden compatibility */
        if (dom.summaryStatus && data.status) dom.summaryStatus.textContent = safeText(data.status);
        if (dom.summaryApiStatus) dom.summaryApiStatus.textContent = "API: " + safeText(data.status);
        if (dom.snapshotTimestamp) dom.snapshotTimestamp.textContent = "Snapshot: " + formatTimestamp(snapshot.created_at);

        /* Footer updated time */
        if (dom.footerUpdated) {
            dom.footerUpdated.textContent = "just now";
        }
    }

    /* -----------------------------------------------------------------------
     * Polling engine
     * -----------------------------------------------------------------------
     */

    function scheduleNextPoll(delayMs) {
        if (pollTimeoutId) { clearTimeout(pollTimeoutId); }
        pollTimeoutId = setTimeout(function () {
            pollTimeoutId = null;
            executePoll();
        }, delayMs);
    }

    function executePoll() {
        if (currentAbortController) { currentAbortController.abort(); }
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
                if (response.status === 401) { handleUnauthenticated(); return; }
                if (!response.ok) { handleHttpError(response.status); scheduleNextPoll(computeDelay()); return; }
                return response.json().then(function (data) {
                    handleSuccessfulResponse(data);
                    scheduleNextPoll(computeDelay());
                });
            })
            .catch(function (error) {
                clearTimeout(timeoutId);
                currentAbortController = null;
                if (error && error.name === "AbortError") { return; }
                handleNetworkError(error);
                scheduleNextPoll(computeDelay());
            });
    }

    function handleSuccessfulResponse(data) {
        consecutiveFailures = 0;
        lastSuccessfulResponseTime = Date.now();
        lastSnapshot = data;
        clearStaleTimers();
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
            if (lastSnapshot && connectionState === "online") { setConnectionState("degraded"); }
        } else if (status >= 500) {
            if (consecutiveFailures >= MAX_CONSECUTIVE_BACKOFF) { setConnectionState("offline"); }
        }
    }

    function handleNetworkError(/* error */) {
        consecutiveFailures++;
        if (consecutiveFailures >= MAX_CONSECUTIVE_BACKOFF) { setConnectionState("offline"); }
    }

    function handleUnauthenticated() {
        if (pollTimeoutId) { clearTimeout(pollTimeoutId); pollTimeoutId = null; }
        if (currentAbortController) { currentAbortController.abort(); currentAbortController = null; }
        clearStaleTimers();
        consecutiveFailures = 0;
        window.location.href = "/login";
    }

    function updateLastRefresh() {
        if (lastSuccessfulResponseTime && dom.lastRefresh) {
            var seconds = Math.floor((Date.now() - lastSuccessfulResponseTime) / 1000);
            dom.lastRefresh.textContent = "Last updated: " + seconds + "s ago";
        }
    }

    /* -----------------------------------------------------------------------
     * Visibility handling
     * -----------------------------------------------------------------------
     */

    function onVisibilityChange() {
        if (document.hidden) {
            if (pollTimeoutId) { clearTimeout(pollTimeoutId); pollTimeoutId = null; }
            if (!currentAbortController) { scheduleNextPoll(POLL_HIDDEN_MS); }
        } else {
            if (pollTimeoutId) { clearTimeout(pollTimeoutId); pollTimeoutId = null; }
            executePoll();
        }
    }

    /* -----------------------------------------------------------------------
     * Unload handling
     * -----------------------------------------------------------------------
     */

    function onBeforeUnload() {
        if (pollTimeoutId) { clearTimeout(pollTimeoutId); pollTimeoutId = null; }
        if (currentAbortController) { currentAbortController.abort(); currentAbortController = null; }
        if (refreshTickId) { clearTimeout(refreshTickId); refreshTickId = null; }
        clearStaleTimers();
    }

    /* -----------------------------------------------------------------------
     * Footer ticker
     * -----------------------------------------------------------------------
     */

    var refreshTickId = null;

    function scheduleRefreshTick() {
        if (refreshTickId) { clearTimeout(refreshTickId); }
        refreshTickId = setTimeout(function () {
            if (lastSuccessfulResponseTime && dom.footerUpdated) {
                var seconds = Math.floor((Date.now() - lastSuccessfulResponseTime) / 1000);
                dom.footerUpdated.textContent = seconds + "s ago";
            }
            scheduleRefreshTick();
        }, 1000);
    }

    /* -----------------------------------------------------------------------
     * Startup
     * -----------------------------------------------------------------------
     */

    function start() {
        cacheDom();
        /* Skip polling on analytics page */
        if (dom.isAnalyticsPage) { return; }
        document.addEventListener("visibilitychange", onVisibilityChange);
        window.addEventListener("beforeunload", onBeforeUnload);
        window.addEventListener("pagehide", onBeforeUnload);
        executePoll();
        scheduleRefreshTick();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})();
