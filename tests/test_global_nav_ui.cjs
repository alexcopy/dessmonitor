'use strict';

const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../app/web/static/global_nav.js'), 'utf8');

test('mobile navigation opens, reports expanded state and closes on selection', () => {
    const listeners = {};
    const linkListeners = {};
    const classes = new Set();
    const button = {attributes: {}, setAttribute(name, value) {this.attributes[name] = value;},
        addEventListener(name, callback) {listeners[name] = callback;}};
    const link = {addEventListener(name, callback) {linkListeners[name] = callback;}};
    const links = {classList: {
        toggle(name) {if (classes.has(name)) classes.delete(name); else classes.add(name); return classes.has(name);},
        remove(name) {classes.delete(name);}
    }, querySelectorAll: () => [link]};
    let ready;
    const document = {getElementById: id => id === 'navHamburger' ? button : id === 'navLinks' ? links : null,
        addEventListener(name, callback) {if (name === 'DOMContentLoaded') ready = callback;}};
    vm.runInNewContext(source, {document});
    ready();
    assert.equal(button.attributes['aria-controls'], 'navLinks');
    assert.equal(button.attributes['aria-expanded'], 'false');
    listeners.click();
    assert.equal(classes.has('open'), true);
    assert.equal(button.attributes['aria-expanded'], 'true');
    linkListeners.click();
    assert.equal(classes.has('open'), false);
    assert.equal(button.attributes['aria-expanded'], 'false');
});
