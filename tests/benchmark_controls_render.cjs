'use strict';

// Measures Controls JS processing with a synthetic DOM and a Chart stub. It is
// reproducible, but intentionally excludes real browser layout, canvas drawing,
// and network transfer. Arguments: source.js payload.json selected-days.
const fs = require('node:fs');
const vm = require('node:vm');
const [sourcePath, payloadPath, daysArg] = process.argv.slice(2);
if (!sourcePath || !payloadPath || !['1', '2', '7'].includes(daysArg)) {
    process.stderr.write('usage: node tests/benchmark_controls_render.cjs source.js payload.json 1|2|7\n');
    process.exit(2);
}
const source = fs.readFileSync(sourcePath, 'utf8');
const payloadText = fs.readFileSync(payloadPath, 'utf8');
const days = Number(daysArg);
const elements = new Map();
let loadStatusUpdates = 0;
function element(tag) {
    return {
        tag, children: [], style: {}, className: '', dataset: {},
        set textContent(value) {
            this._text = String(value); this.children = [];
            if (this.tag === 'loadStatus') loadStatusUpdates++;
        },
        get textContent() { return (this._text || '') + this.children.map(child => child.textContent).join(''); },
        appendChild(child) {
            if (child.tag === '#fragment') this.children.push(...child.children);
            else this.children.push(child);
            return child;
        },
        replaceChildren(...children) { this._text = ''; this.children = children; }
    };
}
for (const id of ['loadStatus', 'sourceChangesCount', 'solarRangeLabel', 'gridRangeLabel',
    'kpi-source', 'kpi-mode', 'kpi-mode-changes', 'kpi-line-invert', 'kpi-invert-line',
    'kpi-source-changes', 'kpi-shortest-dwell', 'kpi-median-dwell', 'kpi-soc', 'kpi-batv',
    'events-count', 'significant-events-count', 'eventsBody', 'significantEventsBody',
    'outputChart', 'pvChart', 'sourceChart']) elements.set(id, element(id));
let domReady;
let visibilityChange;
const fetchUrls = [];
const buttons = [1, 2, 7].map(value => {
    const classes = new Set();
    return {dataset: {days: String(value)}, classList: {
        add: name => classes.add(name), remove: name => classes.delete(name),
        toggle: (name, state) => state ? classes.add(name) : classes.delete(name)
    }, addEventListener() {}};
});
const charts = [];
function Chart(canvas, config) {
    this.canvas = canvas; this.data = config.data; this.options = config.options;
    this.updateCount = 0; this.destroyCount = 0; charts.push(this);
}
Chart.prototype.update = function () { this.updateCount++; };
Chart.prototype.destroy = function () { this.destroyCount++; };
const document = {
    hidden: false,
    getElementById: id => elements.get(id) || null,
    createElement: tag => element(tag),
    createDocumentFragment: () => element('#fragment'),
    querySelectorAll: () => buttons,
    addEventListener: (type, callback) => {
        if (type === 'DOMContentLoaded') domReady = callback;
        if (type === 'visibilitychange') visibilityChange = callback;
    }
};
const timerCallbacks = [];
let fetchCalls = 0;
const context = {
    document, Chart, Date, Number, Array, String, Object, parseInt, Math, JSON,
    AbortController, setTimeout, clearTimeout,
    setInterval: callback => { timerCallbacks.push(callback); },
    sessionStorage: {getItem: key => key === 'controls.days' ? String(days) : null,
        setItem() {}},
    fetch: async url => { fetchCalls++; fetchUrls.push(url); return {ok: true, json: async () => JSON.parse(payloadText)};}
};
const tick = () => new Promise(resolve => setImmediate(resolve));
async function waitFor(predicate) {
    for (let i = 0; i < 200; i++) {
        if (predicate()) return;
        await tick();
    }
    throw new Error('render did not finish');
}
async function main() {
    const started = process.hrtime.bigint();
    vm.runInNewContext(source, context);
    domReady();
    await waitFor(() => /Updated/.test(elements.get('loadStatus').textContent));
    const initialMs = Number(process.hrtime.bigint() - started) / 1e6;
    const beforeRefresh = fetchCalls;
    const statusUpdatesBeforeRefresh = loadStatusUpdates;
    const refreshStarted = process.hrtime.bigint();
    timerCallbacks[0]();
    await waitFor(() => fetchCalls > beforeRefresh && loadStatusUpdates > statusUpdatesBeforeRefresh &&
        /Updated/.test(elements.get('loadStatus').textContent));
    const refreshMs = Number(process.hrtime.bigint() - refreshStarted) / 1e6;
    process.stdout.write(JSON.stringify({days, initial_js_render_ms: Number(initialMs.toFixed(3)),
        refresh_js_render_ms: Number(refreshMs.toFixed(3)), observations: JSON.parse(payloadText).observations.length,
        requested_url: fetchUrls[0], refresh_url: fetchUrls[1],
        chart_instances_after_refresh: charts.length,
        chart_updates_after_refresh: charts.reduce((sum, chart) => sum + chart.updateCount, 0),
        chart_destroys_after_refresh: charts.reduce((sum, chart) => sum + chart.destroyCount, 0),
        note: 'Node DOM/Chart stub; excludes browser layout, canvas drawing, and network'}));
}
main().catch(error => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; });
