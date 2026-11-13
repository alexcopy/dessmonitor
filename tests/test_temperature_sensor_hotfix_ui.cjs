'use strict';

const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '..', 'app/web/static/dashboard.js'), 'utf8');

function element() {
    const classes = new Set();
    return {
        children: [], textContent: '', className: '', classList: {
            add(name) { classes.add(name); },
            remove(name) { classes.delete(name); },
            contains(name) { return classes.has(name); }
        }, style: {}, dataset: {}, appendChild(child) { this.children.push(child); return child; },
        setAttribute(name, value) { this[name] = value; }, addEventListener() {},
        classSet: classes
    };
}

function textOf(node) {
    return String(node.textContent || '') + ((node.children && node.children.length)
        ? node.children.map(textOf).join('')
        : '');
}

async function render(sensors, secondSensors) {
    const elements = {};
    const timers = [];
    let pollCount = 0;
    let ready;
    const get = id => elements[id] || (elements[id] = element());
    const document = {
        readyState: 'loading', hidden: false,
        getElementById: get,
        createElement: () => element(),
        createTextNode: value => ({children: [], textContent: String(value)}),
        addEventListener(name, callback) { if (name === 'DOMContentLoaded') ready = callback; }
    };
    const response = {status: 200, ok: true, json: async () => ({status: 'ok', snapshot: {
        created_at: '2026-11-13T12:00:00+00:00', loads: [], inverter: [],
        sensors: pollCount === 0 ? sensors : secondSensors
    }})};
    const context = {
        document,
        window: {addEventListener() {}, location: {}},
        fetch: async () => response,
        AbortController: class { abort() {} },
        setTimeout: callback => {
            const timer = {callback, cancelled: false};
            timers.push(timer);
            return timer;
        },
        clearTimeout: timer => { if (timer) timer.cancelled = true; },
        Date, Math, Number, String, JSON, isNaN, console
    };
    vm.runInNewContext(source, context);
    ready();
    await new Promise(resolve => setImmediate(resolve));
    await new Promise(resolve => setImmediate(resolve));
    if (secondSensors !== undefined) {
        pollCount = 1;
        vm.runInNewContext(source, context);
        ready();
        await new Promise(resolve => setImmediate(resolve));
        await new Promise(resolve => setImmediate(resolve));
    }
    return elements;
}

test('sensor rows and mini indicator share the selected water-temperature formatter', async () => {
    const elements = await render([
        {sensor_id: 'bad', display_name: 'Bad probe', metric: 'water_temperature', value: null,
            unit: 'celsius', freshness: 'unavailable', status: 'invalid'},
        {sensor_id: 'good', display_name: 'Good probe', metric: 'water_temperature', value: 23.7,
            unit: 'celsius', freshness: 'fresh', status: 'valid'},
        {sensor_id: 'unknown', display_name: 'Unknown probe', metric: 'water_temperature', value: null,
            unit: 'celsius', freshness: 'unavailable', status: 'unverified'}
    ]);
    const rows = elements['sensors-panel'].children;
    assert.equal(rows.length, 3);
    assert.equal(textOf(rows[1].children[2]), '23.7°C');
    assert.equal(elements['mini-watertemp'].textContent, textOf(rows[1].children[2]));
    assert.equal(elements['mini-watertemp'].className, 'dm-stat-mini-val ok');
    assert.match(rows[0].children[2].className, /error/);
    assert.match(rows[2].children[2].className, /degraded/);
    assert.equal(textOf(rows[0].children[1].children[2]), 'Check reading');
    assert.equal(textOf(rows[2].children[1].children[2]), 'Unverified');
});

test('cached and unavailable states do not receive the live green class', async () => {
    const elements = await render([
        {sensor_id: 'cached', display_name: 'Cached probe', metric: 'water_temperature', value: 19.9,
            unit: 'celsius', freshness: 'fresh', status: 'cached'},
        {sensor_id: 'none', display_name: 'No probe', metric: 'water_temperature', value: null,
            unit: 'celsius', freshness: 'unavailable', status: 'unavailable'}
    ]);
    const rows = elements['sensors-panel'].children;
    assert.match(rows[0].children[2].className, /degraded/);
    assert.doesNotMatch(rows[0].children[2].className, /\bok\b/);
    assert.match(rows[1].children[2].className, /neutral/);
    assert.equal(elements['mini-watertemp'].textContent, '19.9°C');
    assert.match(elements['mini-watertemp'].className, /degraded/);
});

test('live-to-empty transition resets the mini indicator to neutral', async () => {
    const elements = await render([
        {sensor_id: 'live', display_name: 'Live probe', metric: 'water_temperature', value: 23.7,
            unit: 'celsius', freshness: 'fresh', status: 'valid'}
    ], []);
    assert.equal(elements['mini-watertemp'].textContent, '—');
    assert.equal(elements['mini-watertemp'].className, 'dm-stat-mini-val neutral');
    assert.doesNotMatch(elements['mini-watertemp'].className, /\bok\b/);
});
