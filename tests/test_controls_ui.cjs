'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'app/web/static/controls.js'), 'utf8');

function setup(fetchImpl, initialStore = {}) {
    const handlers = {};
    const timers = [];
    const values = { ...initialStore };
    const elements = {};
    for (const id of ['loadStatus', 'sourceChangesCount', 'solarRangeLabel', 'gridRangeLabel', 'kpi-mode']) {
        elements[id] = { textContent: '', style: {} };
    }
    const buttons = [1, 2, 7].map(days => {
        const classes = new Set();
        return {
            dataset: { days: String(days) }, classes,
            classList: {
                add: value => classes.add(value), remove: value => classes.delete(value),
                toggle: (value, enabled) => enabled ? classes.add(value) : classes.delete(value)
            },
            addEventListener: (_event, fn) => { buttons.find(b => b.dataset.days === String(days)).click = fn; }
        };
    });
    const document = {
        hidden: false,
        getElementById: id => elements[id] || null,
        querySelectorAll: () => buttons,
        addEventListener: (event, fn) => { handlers[event] = fn; }
    };
    const context = {
        document, fetch: fetchImpl, Date, Number, Array, String, Object, parseInt,
        AbortController, setTimeout, clearTimeout,
        sessionStorage: { getItem: k => values[k] ?? null, setItem: (k, v) => { values[k] = v; } },
        setInterval: (fn, ms) => { timers.push({ fn, ms }); },
    };
    vm.runInNewContext(source, context);
    handlers.DOMContentLoaded();
    return { handlers, timers, elements, buttons, values, document };
}
const tick = () => new Promise(resolve => setImmediate(resolve));
const payload = () => ({ observations: [{ time: new Date().toISOString(), working_mode: 'Line Mode' }], transitions: [], summary_24h: {} });
const response = data => ({ ok: true, json: async () => data });

test('refresh preserves selected range and restores it on page reload', async () => {
    let calls = 0;
    const fetcher = async () => { calls++; return response(payload()); };
    const ui = setup(fetcher);
    await tick();
    assert.equal(ui.timers[0].ms, 60000);
    ui.buttons[2].click();
    assert.equal(ui.values['controls.days'], '7');
    assert.equal(ui.elements.solarRangeLabel.textContent, '7 days');
    ui.timers[0].fn();
    await tick();
    assert.equal(calls, 2);
    assert.equal(ui.elements.solarRangeLabel.textContent, '7 days');
    assert.match(ui.elements.loadStatus.textContent, /Updated/);
    const reloaded = setup(fetcher, ui.values);
    await tick();
    assert.ok(reloaded.buttons[2].classes.has('active'));
    assert.equal(reloaded.elements.solarRangeLabel.textContent, '7 days');
});

test('failures and malformed responses retain data and show a retry message', async () => {
    let calls = 0;
    const ui = setup(async () => {
        calls++;
        if (calls === 2) throw new Error('private details');
        if (calls === 3) return response({ observations: 'bad', transitions: [], summary_24h: {} });
        return response(payload());
    });
    await tick();
    assert.equal(ui.elements['kpi-mode'].textContent, 'Line Mode');
    ui.timers[0].fn(); await tick();
    assert.match(ui.elements.loadStatus.textContent, /last successful results/);
    assert.doesNotMatch(ui.elements.loadStatus.textContent, /private details/);
    assert.equal(ui.elements['kpi-mode'].textContent, 'Line Mode');
    ui.timers[0].fn(); await tick();
    assert.match(ui.elements.loadStatus.textContent, /Refresh failed/);
    ui.timers[0].fn(); await tick();
    assert.match(ui.elements.loadStatus.textContent, /Updated/);
});

test('slow requests do not overlap; hidden pages refresh when visible', async () => {
    let finish, calls = 0;
    const ui = setup(() => {
        calls++;
        return calls === 1 ? new Promise(resolve => { finish = resolve; }) : Promise.resolve(response(payload()));
    });
    ui.timers[0].fn();
    assert.equal(calls, 1);
    finish(response(payload())); await tick();
    ui.document.hidden = true; ui.timers[0].fn();
    assert.equal(calls, 1);
    ui.document.hidden = false; ui.handlers.visibilitychange(); await tick();
    assert.equal(calls, 2);
});

test('wide transition table has a keyboard accessible horizontal scroll region', () => {
    const html = fs.readFileSync(path.join(root, 'app/web/templates/controls.html'), 'utf8');
    const css = fs.readFileSync(path.join(root, 'app/web/static/controls.css'), 'utf8');
    assert.match(html, /dm-events-scroll[^>]*tabindex="0"[^>]*role="region"/);
    assert.match(css, /\.dm-events-scroll\s*\{[^}]*overflow-x:\s*auto/s);
    assert.match(css, /\.dm-events-scroll \.dm-table\s*\{[^}]*width:\s*max-content/s);
});
