'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'app/web/static/controls.js'), 'utf8');

function element(tag) {
    return {
        tag, children: [], style: {}, className: '', _text: '',
        set textContent(value) { this._text = String(value); this.children = []; },
        get textContent() { return this._text + this.children.map(child => child.textContent).join(''); },
        appendChild(child) {
            if (child.tag === '#fragment') this.children.push(...child.children);
            else this.children.push(child);
            return child;
        },
        replaceChildren(...children) { this._text = ''; this.children = children; }
    };
}

function setup(fetchImpl, initialStore = {}) {
    const handlers = {};
    const timers = [];
    const values = { ...initialStore };
    const elements = {};
    for (const id of ['loadStatus', 'sourceChangesCount', 'solarRangeLabel', 'gridRangeLabel', 'kpi-mode']) {
        elements[id] = { textContent: '', style: {} };
    }
    for (const id of ['eventsBody', 'significantEventsBody', 'events-count', 'significant-events-count']) {
        elements[id] = element(id);
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
        createElement: tag => element(tag),
        createDocumentFragment: () => element('#fragment'),
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

function row(minutesAgo, fields = {}) {
    return { time: new Date(Date.now() - minutesAgo * 60000).toISOString(),
        charger_priority: 'solar_only', working_mode: 'Line Mode', battery_soc: 50,
        battery_voltage: 25.4, pv_power_w: 100, output_power_w: 300, ...fields };
}
const history = observations => ({ observations, transitions: [], summary_24h: {} });
const cell = (ui, table, rowIndex, cellIndex) => ui.elements[table].children[rowIndex].children[cellIndex].textContent;

test('historical source, mode, SOC and voltage thresholds create independent significant events', async () => {
    const rows = [row(100), row(90, {charger_priority: 'solar_priority'}),
        row(80, {charger_priority: 'solar_priority', working_mode: 'Invert Mode'}),
        row(70, {charger_priority: 'solar_priority', working_mode: 'Invert Mode', battery_soc: 55}),
        row(60, {charger_priority: 'solar_priority', working_mode: 'Invert Mode', battery_soc: 55, battery_voltage: 25.7})];
    const ui = setup(async () => response(history(rows)));
    await tick();
    assert.equal(ui.elements['significant-events-count'].textContent, '5 events');
    assert.equal(ui.elements['events-count'].textContent, '1 transition');
    assert.equal(cell(ui, 'significantEventsBody', 0, 4), '25.7 V');
    assert.equal(cell(ui, 'significantEventsBody', 1, 3), '55%');
    assert.equal(cell(ui, 'significantEventsBody', 2, 2), 'Invert Mode');
    assert.equal(cell(ui, 'significantEventsBody', 3, 1), 'Solar priority');
    assert.equal(ui.elements.significantEventsBody.children[3].children[1].children[0].style.color, '#ffca28');
    assert.equal(ui.elements.significantEventsBody.children[1].children[3].style.color, 'var(--amber)');
    assert.equal(cell(ui, 'eventsBody', 0, 1), 'Line Mode → Invert Mode');
});

test('small and PV-only changes accumulate against the last event with exact decimal boundary', async () => {
    const rows = [row(80), row(70, {battery_soc: 52, battery_voltage: 25.5, pv_power_w: 900}),
        row(60, {battery_soc: 54, battery_voltage: 25.6, pv_power_w: 5}),
        row(50, {battery_soc: 54, battery_voltage: 25.7, pv_power_w: 12})];
    const ui = setup(async () => response(history(rows)));
    await tick();
    assert.equal(ui.elements['significant-events-count'].textContent, '2 events');
    assert.equal(cell(ui, 'significantEventsBody', 0, 4), '25.7 V');
    assert.equal(cell(ui, 'significantEventsBody', 0, 5), '12 W');
    assert.equal(cell(ui, 'significantEventsBody', 0, 6), '…');
    assert.equal(cell(ui, 'significantEventsBody', 1, 6), '30 min');
    assert.equal(cell(ui, 'eventsBody', 0, 0), 'No observed output-mode transitions in this period.');
});

test('missing or malformed SOC and voltage do not fabricate events', async () => {
    const rows = [row(30), row(20, {battery_soc: null, battery_voltage: null}),
        row(10, {battery_soc: 'bad', battery_voltage: 'bad'}), row(5)];
    const ui = setup(async () => response(history(rows)));
    await tick();
    assert.equal(ui.elements['significant-events-count'].textContent, '1 event');
    assert.equal(cell(ui, 'significantEventsBody', 0, 3), '50%');
});

test('rounded values determine significance and missing readings remain unknown after a mode change', async () => {
    const rows = [row(40, {battery_soc: 50.4, battery_voltage: 25.41}),
        row(30, {battery_soc: 54.4, battery_voltage: 25.64}),
        row(20, {battery_soc: 54.6, battery_voltage: 25.64}),
        row(10, {working_mode: 'Invert Mode', battery_soc: null, battery_voltage: null})];
    const ui = setup(async () => response(history(rows)));
    await tick();
    assert.equal(ui.elements['significant-events-count'].textContent, '3 events');
    assert.equal(cell(ui, 'significantEventsBody', 0, 3), '—');
    assert.equal(cell(ui, 'significantEventsBody', 0, 4), '—');
    assert.equal(cell(ui, 'significantEventsBody', 1, 3), '55%');
    assert.equal(cell(ui, 'significantEventsBody', 1, 4), '25.6 V');
});

test('both tables filter the selected range and refresh without duplicate rows', async () => {
    let rows = [row(1800), row(1790, {working_mode:'Invert Mode'}), row(60, {working_mode:'Invert Mode'}),
        row(50, {working_mode:'Line Mode', battery_soc:55})];
    const ui = setup(async () => response(history(rows)));
    await tick();
    assert.equal(ui.elements['significant-events-count'].textContent, '2 events');
    assert.equal(ui.elements['events-count'].textContent, '1 transition');
    ui.buttons[1].click();
    assert.equal(ui.elements['significant-events-count'].textContent, '3 events');
    assert.equal(ui.elements['events-count'].textContent, '2 transitions');
    rows = [...rows, row(10, {working_mode:'Invert Mode', battery_soc:55})];
    ui.timers[0].fn(); await tick();
    assert.equal(ui.elements['significant-events-count'].textContent, '4 events');
    assert.equal(ui.elements['events-count'].textContent, '3 transitions');
    ui.timers[0].fn(); await tick();
    assert.equal(ui.elements.significantEventsBody.children.length, 4);
    assert.equal(ui.elements.eventsBody.children.length, 3);
});

test('failed refresh retains both tables and empty history shows both empty states', async () => {
    let calls = 0;
    const ui = setup(async () => {
        calls++;
        if (calls === 2) throw Error('offline');
        return response(history(calls === 1 ? [row(20), row(10, {working_mode:'Invert Mode'})] : []));
    });
    await tick();
    const before = [ui.elements.eventsBody.textContent, ui.elements.significantEventsBody.textContent];
    ui.timers[0].fn(); await tick();
    assert.deepEqual([ui.elements.eventsBody.textContent, ui.elements.significantEventsBody.textContent], before);
    ui.timers[0].fn(); await tick();
    assert.equal(ui.elements['significant-events-count'].textContent, '0 events');
    assert.equal(ui.elements.significantEventsBody.textContent, 'No significant changes in this period.');
    assert.equal(ui.elements.eventsBody.textContent, 'No observed output-mode transitions in this period.');
});

test('template keeps both independent tables in order', () => {
    const html = fs.readFileSync(path.join(root, 'app/web/templates/controls.html'), 'utf8');
    assert.ok(html.indexOf('id="eventsTable"') < html.indexOf('id="significantEventsTable"'));
    for (const id of ['eventsTable', 'eventsBody', 'events-count', 'significantEventsTable', 'significantEventsBody', 'significant-events-count']) {
        assert.equal((html.match(new RegExp(`id="${id}"`, 'g')) || []).length, 1);
    }
});
