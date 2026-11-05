/* Shared responsive behavior for the authenticated global navigation. */
(function () {
    'use strict';
    document.addEventListener('DOMContentLoaded', function () {
        var button = document.getElementById('navHamburger');
        var links = document.getElementById('navLinks');
        if (!button || !links) return;
        button.setAttribute('aria-controls', 'navLinks');
        button.setAttribute('aria-expanded', 'false');
        button.addEventListener('click', function () {
            var open = links.classList.toggle('open');
            button.setAttribute('aria-expanded', open ? 'true' : 'false');
        });
        links.querySelectorAll('a').forEach(function (link) {
            link.addEventListener('click', function () {
                links.classList.remove('open');
                button.setAttribute('aria-expanded', 'false');
            });
        });
    });
}());
