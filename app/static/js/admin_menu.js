/* Admin console menu: one open sidebar category at a time.
 *
 * sqladmin's own script remembers expanded groups in
 * `sqladmin:menu:<base_url>` and restores every remembered name on load; the
 * categories also carry `data-bs-auto-close="false"`, so Bootstrap leaves the
 * previous dropdown alone. Together they stack groups under each other.
 *
 * An open group therefore has two owners: this script closes every other
 * group, and the storage key keeps a single name so a reload restores one.
 *
 * The click listener is attached to the root element in the capture phase, so
 * it runs before Bootstrap's document-level handler: every group is folded
 * first, and the clicked category then opens through Bootstrap as usual.
 * Clicking a child model link inside an open submenu keeps that category open
 * and stored so the submenu stays expanded on the target model page.
 */
(function () {
    'use strict';
    var STORAGE_PREFIX = 'sqladmin:menu:';
    var MENU = '[data-sqladmin-menu]';
    var CATEGORY = '[data-sqladmin-menu-category]';
    var TOGGLE = '[data-bs-toggle="dropdown"]';
    var PANEL = '.dropdown-menu';
    var SHOW = 'show';
    var ACTIVE = 'active';

    function storageKey(menu) {
        var base = menu.getAttribute('data-sqladmin-menu');
        return STORAGE_PREFIX + base;
    }

    function readNames(key) {
        try {
            var stored = JSON.parse(localStorage.getItem(key));
            return Array.isArray(stored) ? stored : [];
        } catch (_) { return []; }
    }

    function writeNames(key, names) {
        try { localStorage.setItem(key, JSON.stringify(names)); } catch (_) { /* storage may be disabled */ }
    }

    function close(item) {
        var toggle = item.querySelector(TOGGLE);
        var panel = item.querySelector(PANEL);
        if (toggle) { toggle.classList.remove(SHOW); toggle.setAttribute('aria-expanded', 'false'); }
        if (panel) panel.classList.remove(SHOW);
    }

    function openItem(item) {
        var toggle = item.querySelector(TOGGLE);
        var panel = item.querySelector(PANEL);
        if (toggle) { toggle.classList.add(SHOW); toggle.setAttribute('aria-expanded', 'true'); }
        if (panel) panel.classList.add(SHOW);
    }

    function openItems(menu) {
        var open = [];
        Array.prototype.forEach.call(menu.querySelectorAll(CATEGORY), function (item) {
            var panel = item.querySelector(PANEL);
            if (panel && panel.classList.contains(SHOW)) open.push(item);
        });
        return open;
    }

    function nameOf(item) { return item.getAttribute('data-sqladmin-menu-category'); }

    function activeCategory(menu) {
        var found = null;
        Array.prototype.forEach.call(menu.querySelectorAll(CATEGORY), function (item) {
            var toggle = item.querySelector(TOGGLE);
            var panel = item.querySelector(PANEL);
            if ((toggle && toggle.classList && toggle.classList.contains(ACTIVE)) ||
                (panel && panel.querySelector && panel.querySelector('.' + ACTIVE))) {
                found = nameOf(item);
            }
        });
        return found;
    }

    // Keep exactly the group that is open now, so a reload restores just it.
    function syncStorage(menu) {
        var open = openItems(menu);
        writeNames(storageKey(menu), open.length ? [nameOf(open[0])] : []);
    }

    // Read the stack before the restore script trims it, so the final choice
    // is the newest name either way. Returns the single remembered group.
    function collapseStack(menu) {
        var names = readNames(storageKey(menu));
        return names.length ? names[names.length - 1] : null;
    }

    // The native restore script may already have trimmed the stored stack;
    // the render state, storage, or the active route category decides which group stays open.
    function restore(menu) {
        var remembered = collapseStack(menu);
        var open = openItems(menu);
        var keep = open.length ? nameOf(open[open.length - 1]) : (remembered !== null ? remembered : activeCategory(menu));
        if (keep === null) return;
        writeNames(storageKey(menu), [keep]);
        Array.prototype.forEach.call(menu.querySelectorAll(CATEGORY), function (item) {
            if (nameOf(item) !== keep) close(item);
            else openItem(item);
        });
    }

    function onReady() {
        var menu = document.querySelector(MENU);
        if (!menu) return;
        restore(menu);
        document.documentElement.addEventListener('click', function (event) {
            var target = event.target;
            if (!target || Number(event.button) > 0) return;
            // Modified clicks open a link in another tab; the page stays put.
            if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
            var inside = menu.contains(target);
            var clicked = target.closest ? target.closest(CATEGORY) : null;
            // Clicks outside the menu leave it alone.
            if (!inside && !clicked) return;
            // Clicking a model link inside an open submenu keeps that category open
            // and remembered so the submenu stays expanded on the target model page.
            var clickedPanel = clicked ? clicked.querySelector(PANEL) : null;
            if (clicked && clickedPanel && clickedPanel.contains(target)) {
                Array.prototype.forEach.call(menu.querySelectorAll(CATEGORY), function (item) {
                    if (item !== clicked) close(item);
                    else openItem(item);
                });
                syncStorage(menu);
                return;
            }
            // Fold everything first. Only the clicked category may come back:
            // Bootstrap opens it next because it saw a closed toggle.
            Array.prototype.forEach.call(menu.querySelectorAll(CATEGORY), close);
            syncStorage(menu);
        }, true);
        // Bootstrap reports its own flip a tick later; mirror it into storage.
        document.addEventListener('shown.bs.dropdown', function () { syncStorage(menu); });
        document.addEventListener('hidden.bs.dropdown', function () { syncStorage(menu); });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', onReady);
    else onReady();
})();
