/* Admin console menu: one open sidebar category at a time.
 *
 * sqladmin's own script remembers expanded groups in
 * `sqladmin:menu:<base_url>` and restores every remembered name on load; the
 * categories also carry `data-bs-auto-close="false"`, so Bootstrap leaves the
 * previous dropdown alone. Together they stack groups under each other.
 *
 * The submenu is owned by this script rather than by Bootstrap's data-api:
 * nothing here hides the panel with Tabler's floating-popover CSS, and a
 * missed instance leaves a category that can be closed but never opened.
 * A header click therefore just toggles it after folding the others, and the
 * model pages of the open category are reached with the submenu still shown.
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

    function nameOf(item) { return item.getAttribute('data-sqladmin-menu-category'); }

    function close(item) {
        var toggle = item.querySelector(TOGGLE);
        var panel = item.querySelector(PANEL);
        if (toggle) { toggle.classList.remove(SHOW); toggle.setAttribute('aria-expanded', 'false'); }
        if (panel) panel.classList.remove(SHOW);
    }

    function open(item) {
        var toggle = item.querySelector(TOGGLE);
        var panel = item.querySelector(PANEL);
        if (toggle) { toggle.classList.add(SHOW); toggle.setAttribute('aria-expanded', 'true'); }
        if (panel) panel.classList.add(SHOW);
    }

    function isOpen(item) {
        var panel = item.querySelector(PANEL);
        return Boolean(panel && panel.classList.contains(SHOW));
    }

    function foldOthers(menu, keep) {
        Array.prototype.forEach.call(menu.querySelectorAll(CATEGORY), function (item) {
            if (item !== keep) close(item);
        });
    }

    function openItems(menu) {
        var open = [];
        Array.prototype.forEach.call(menu.querySelectorAll(CATEGORY), function (item) {
            if (isOpen(item)) open.push(item);
        });
        return open;
    }

    function isHidden(element) {
        var rects = element.getClientRects ? element.getClientRects() : [1];
        if (!rects.length) return true;
        var style = window.getComputedStyle(element);
        return style.visibility === 'hidden' || style.display === 'none';
    }

    // The category that holds the current page, whether the console marked the
    // header, the model link, or both. There is no single "active" class to
    // trust, so a model link counts when it points at this page and is not
    // hidden by the collapsed panel. The last match wins: one category holds at
    // most one active model in the markup.
    function activeCategory(menu) {
        var found = null;
        // Snapshot first: the walk below must not see the menu this restore is
        // changing.
        var items = Array.prototype.slice.call(menu.querySelectorAll(CATEGORY));
        items.forEach(function (item) {
            var toggle = item.querySelector(TOGGLE);
            var panel = item.querySelector(PANEL);
            var links = panel ? panel.querySelectorAll('a[href]') : [];
            var current = false;
            Array.prototype.forEach.call(links, function (link) {
                if (!current && !isHidden(link) &&
                    link.href.split('#')[0].split('?')[0] === window.location.href.split('#')[0].split('?')[0]) {
                    current = true;
                }
            });
            if ((toggle && toggle.classList.contains(ACTIVE)) ||
                (panel && panel.querySelector && panel.querySelector('.' + ACTIVE)) || current) {
                found = item;
            }
        });
        return found;
    }

    // Keep exactly the group that is open now, so a reload restores just it.
    function syncStorage(menu) {
        var open = openItems(menu);
        writeNames(storageKey(menu), open.length ? [nameOf(open[0])] : []);
    }

    // The rendered state, then the current page, then the newest remembered
    // name decide which group stays open.
    function restore(menu) {
        // One reconciliation on load, not one per category: folding inside
        // activeCategory's loop could close the very group it just matched.
        var remembered = readNames(storageKey(menu));
        var openNow = openItems(menu);
        var keep = openNow.length ? openNow[openNow.length - 1] : activeCategory(menu);
        if (!keep && remembered.length) {
            var newest = remembered[remembered.length - 1];
            Array.prototype.forEach.call(menu.querySelectorAll(CATEGORY), function (item) {
                if (!keep && nameOf(item) === newest) keep = item;
            });
            // A remembered group that no longer exists is dropped silently.
        }
        foldOthers(menu, keep || null);
        if (keep) open(keep);
        syncStorage(menu);
    }

    // This click listener runs before the console bundle's dropdown handler, and
    // that ordering is the whole point: it must not be left to chance.
    //
    // Tabler's bundle binds Bootstrap's dropdown to `document` in the capture
    // phase. A listener on `documentElement` capture runs *after* it (the
    // capture path is window, document, documentElement, ...), so the bundle
    // opened the panel first and this code then read the category as already
    // open, computed "toggle it closed" and hid the panel it had just opened:
    // a header could be collapsed but never expanded. `window` capture runs
    // first on every path, so the state below is decided by this script alone.
    function onReady() {
        var menu = document.querySelector(MENU);
        if (!menu) return;
        restore(menu);
        window.addEventListener('click', function (event) {
            var target = event.target;
            if (!target || Number(event.button) > 0) return;
            // Modified clicks open a link in another tab; the page stays put.
            if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
            var clicked = target.closest ? target.closest(CATEGORY) : null;
            // Clicks outside the menu leave it alone.
            if (!clicked && !menu.contains(target)) return;
            var panel = clicked && clicked.querySelector(PANEL);
            if (panel && panel.contains(target)) {
                // A model inside the submenu: the page is about to change and
                // the category must stay expanded there.
                foldOthers(menu, clicked);
                open(clicked);
                syncStorage(menu);
                return;
            }
            var toggle = clicked && clicked.querySelector(TOGGLE);
            if (!toggle || !toggle.contains(target)) {
                // Any other sidebar item leaves no group open.
                foldOthers(menu, null);
                syncStorage(menu);
                return;
            }
            // Header: toggle it ourselves and fold the rest, so the state never
            // depends on which Bootstrap instances Tabler happened to create.
            // The event is ended here too: the bundle's dropdown handler would
            // otherwise open or close this very panel again from its own stale
            // instance, and its `data-bs-auto-close="false"` categories would
            // stack open on top of each other.
            var willOpen = !isOpen(clicked);
            foldOthers(menu, willOpen ? clicked : null);
            if (willOpen) open(clicked);
            syncStorage(menu);
            event.stopPropagation();
            event.preventDefault();
        }, true);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', onReady);
    else onReady();
})();
