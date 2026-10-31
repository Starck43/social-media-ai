/* Admin console menu: one open sidebar category at a time.
 *
 * sqladmin's own script remembers expanded groups in
 * `sqladmin:menu:<base_url>` and restores every remembered name on load; the
 * categories also carry `data-bs-auto-close="false"`, so Bootstrap leaves the
 * previous dropdown alone. Together they stack groups under each other.
 *
 * The click listener is attached to the root element in the capture phase, so
 * it runs before Bootstrap's document-level handler: the other groups are
 * folded first, and Bootstrap then flips the clicked category as usual.
 * Clicking a model inside an open submenu keeps its category open, so the
 * submenu is still expanded on the model page it navigates to.
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

    function foldOthers(menu, keep) {
        Array.prototype.forEach.call(menu.querySelectorAll(CATEGORY), function (item) {
            if (item !== keep) close(item);
        });
    }

    function openItems(menu) {
        var open = [];
        Array.prototype.forEach.call(menu.querySelectorAll(CATEGORY), function (item) {
            var panel = item.querySelector(PANEL);
            if (panel && panel.classList.contains(SHOW)) open.push(item);
        });
        return open;
    }

    // The category that holds the current page, whether the console marked the
    // header, the model link, or both. The last match wins, as in the markup
    // one category holds at most one active model.
    function activeCategory(menu) {
        var found = null;
        Array.prototype.forEach.call(menu.querySelectorAll(CATEGORY), function (item) {
            var toggle = item.querySelector(TOGGLE);
            var panel = item.querySelector(PANEL);
            if ((toggle && toggle.classList.contains(ACTIVE)) ||
                (panel && panel.querySelector && panel.querySelector('.' + ACTIVE))) {
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

    // The native restore script may already have trimmed the stored stack;
    // the render state, then the newest remembered name, then the current page
    // decide which group stays open.
    function restore(menu) {
        var remembered = readNames(storageKey(menu));
        var openNow = openItems(menu);
        var shown = openNow.length ? openNow[openNow.length - 1] : null;
        var keep = shown || activeCategory(menu) || null;
        if (!keep && remembered.length) {
            var newest = remembered[remembered.length - 1];
            Array.prototype.forEach.call(menu.querySelectorAll(CATEGORY), function (item) {
                if (!keep && nameOf(item) === newest) keep = item;
            });
            // A remembered group that no longer exists is dropped silently.
        }
        if (!keep) { writeNames(storageKey(menu), []); return; }
        writeNames(storageKey(menu), [nameOf(keep)]);
        foldOthers(menu, keep);
        open(keep);
    }

    // Bootstrap's flip for the document is bound at load, not on ready, so a
    // capture listener on the root must not run before it exists; attaching on
    // DOMContentLoaded keeps the fold in front of every later click.
    function onReady() {
        var menu = document.querySelector(MENU);
        if (!menu) return;
        restore(menu);
        document.documentElement.addEventListener('click', function (event) {
            var target = event.target;
            if (!target || Number(event.button) > 0) return;
            // Modified clicks open a link in another tab; the page stays put.
            if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
            var clicked = target.closest ? target.closest(CATEGORY) : null;
            // Clicks outside the menu leave it alone.
            if (!clicked && !menu.contains(target)) return;
            var insidePanel = clicked && clicked.querySelector(PANEL);
            if (insidePanel && insidePanel.contains(target)) {
                // A model inside the open submenu: the page is about to change,
                // and the category must stay expanded there.
                foldOthers(menu, clicked);
                open(clicked);
                syncStorage(menu);
                return;
            }
            // Fold everything first. Only the clicked category may come back:
            // Bootstrap opens it next because it saw a closed toggle.
            foldOthers(menu, null);
            syncStorage(menu);
        }, true);
        // Bootstrap reports its own flip a tick later; mirror it into storage.
        document.addEventListener('shown.bs.dropdown', function () { syncStorage(menu); });
        document.addEventListener('hidden.bs.dropdown', function () { syncStorage(menu); });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', onReady);
    else onReady();
})();
