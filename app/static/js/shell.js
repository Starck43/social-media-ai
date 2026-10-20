/* Shell UI state is local presentation only; the server owns all permissions. */
(function () {
    'use strict';
    const key = 'app.sidebar.collapsed';
    try { document.documentElement.dataset.sidebarCollapsed = String(localStorage.getItem(key) === 'true'); }
    catch (_) { document.documentElement.dataset.sidebarCollapsed = 'false'; }
    document.addEventListener('DOMContentLoaded', () => {
        const triggers = document.querySelectorAll('[data-sidebar-collapse]');
        const sync = () => triggers.forEach(button => {
            const collapsed = document.documentElement.dataset.sidebarCollapsed === 'true';
            button.setAttribute('aria-expanded', String(!collapsed));
            button.setAttribute('aria-label', collapsed ? 'Развернуть навигацию' : 'Свернуть навигацию');
        });
        triggers.forEach(button => button.addEventListener('click', () => {
            const value = String(document.documentElement.dataset.sidebarCollapsed !== 'true');
            document.documentElement.dataset.sidebarCollapsed = value;
            try { localStorage.setItem(key, value); } catch (_) { /* storage may be disabled */ }
            sync();
        }));
        sync();
        window.addEventListener('storage', event => {
            if (event.key === key) { document.documentElement.dataset.sidebarCollapsed = String(event.newValue === 'true'); sync(); }
        });
        const closePopover = (popover, focus = false) => {
            popover.querySelector('[data-popover-trigger]').setAttribute('aria-expanded', 'false');
            popover.querySelector('.shell-popover-panel').hidden = true;
            if (focus) popover.querySelector('[data-popover-trigger]').focus();
        };
        const initializedPopovers = new WeakSet();
        const initPopovers = (root = document) => {
            root.querySelectorAll('[data-shell-popover]').forEach(popover => {
                if (initializedPopovers.has(popover)) return;
                initializedPopovers.add(popover);
                const trigger = popover.querySelector('[data-popover-trigger]');
                const panel = popover.querySelector('.shell-popover-panel');
                let timer, openedByHover = false;
                const open = (fromHover = false) => {
                    if (panel.hidden) openedByHover = fromHover;
                    clearTimeout(timer);
                    document.querySelectorAll('[data-shell-popover]').forEach(other => { if (other !== popover) closePopover(other); });
                    panel.hidden = false;
                    if (popover.hasAttribute('data-popover-select')) {
                        // Keep option lists inside both the viewport and a scrolling dialog.
                        const rect = trigger.getBoundingClientRect();
                        const dialog = popover.closest('dialog')?.getBoundingClientRect();
                        const top = Math.max(8, dialog ? dialog.top + 8 : 8);
                        const bottom = Math.min(innerHeight - 8, dialog ? dialog.bottom - 8 : innerHeight - 8);
                        const above = Math.max(0, rect.top - top - 4);
                        const below = Math.max(0, bottom - rect.bottom - 4);
                        const upwards = below < Math.min(panel.scrollHeight, 320) && above > below;
                        panel.style.top = upwards ? 'auto' : 'calc(100% + 4px)';
                        panel.style.bottom = upwards ? 'calc(100% + 4px)' : 'auto';
                        panel.style.maxHeight = Math.max(44, Math.min(320, innerHeight / 2, upwards ? above : below)) + 'px';
                    }
                    trigger.setAttribute('aria-expanded', 'true');
                };
                trigger.addEventListener('click', () => {
                    if (panel.hidden) open();
                    else if (openedByHover) openedByHover = false;
                    else closePopover(popover);
                });
                trigger.addEventListener('keydown', event => {
                    if (event.key === 'ArrowDown') { event.preventDefault(); open(); panel.querySelector('a, button')?.focus(); }
                });
                popover.addEventListener('keydown', event => {
                    if (event.key === 'Escape' && !panel.hidden) { event.preventDefault(); event.stopPropagation(); closePopover(popover, true); }
                });
                popover.addEventListener('focusout', () => setTimeout(() => {
                    if (!popover.contains(document.activeElement)) closePopover(popover);
                }, 0));
                if (popover.hasAttribute('data-hover-popover')) {
                    popover.addEventListener('pointerenter', event => {
                        if (popover.closest('.shell-sidebar') && event.pointerType === 'mouse' && matchMedia('(hover: hover)').matches) open(true);
                    });
                    popover.addEventListener('pointerleave', () => {
                        timer = setTimeout(() => { if (!popover.contains(document.activeElement)) closePopover(popover); }, 180);
                    });
                }
                panel.querySelectorAll('[data-theme-choice]').forEach(choice => choice.addEventListener('click', () => closePopover(popover, true)));
                // Popover-select options (model pickers): write the value into the
                // hidden input, refresh the trigger label, mark aria-selected.
                panel.querySelectorAll('[data-popover-option]').forEach(option => option.addEventListener('click', () => {
                    const value = option.dataset.popoverOption || '';
                    const hidden = popover.querySelector('[data-popover-value]');
                    if (hidden) {
                        hidden.value = value;
                        hidden.dispatchEvent(new Event('input', { bubbles: true }));
                    }
                    const label = popover.querySelector('[data-popover-label]');
                    if (label) label.textContent = option.querySelector('span')?.textContent || value;
                    panel.querySelectorAll('[data-popover-option]').forEach(other => other.setAttribute('aria-selected', String(other === option)));
                    closePopover(popover, true);
                }));
            });
        };
        initPopovers();
        document.addEventListener('click', event => {
            document.querySelectorAll('[data-shell-popover]').forEach(popover => {
                if (!popover.contains(event.target)) closePopover(popover);
            });
        });
        let scrollDepth = 0, oldOverflow = '';
        document.querySelectorAll('[data-dialog-open]').forEach(trigger => {
            trigger.setAttribute('aria-controls', trigger.dataset.dialogOpen);
            trigger.setAttribute('aria-expanded', 'false');
        });
        document.querySelectorAll('dialog.shell-dialog, dialog.shell-drawer, dialog.shell-right-panel').forEach(dialog => {
            dialog.addEventListener('close', () => {
                scrollDepth = Math.max(0, scrollDepth - 1);
                if (!scrollDepth) document.body.style.overflow = oldOverflow;
                document.querySelectorAll('[data-dialog-open]').forEach(trigger => {
                    if (trigger.dataset.dialogOpen === dialog.id) trigger.setAttribute('aria-expanded', 'false');
                });
                // Native dialog normally restores focus; handle disappeared/hidden launchers.
                const launcher = dialog.shellLauncher;
                if (launcher?.isConnected && launcher.getClientRects().length) launcher.focus();
                else document.getElementById('page-content')?.focus();
            });
            dialog.addEventListener('keydown', event => {
                if (event.key !== 'Tab') return;
                const controls = Array.from(dialog.querySelectorAll('a[href], button, input, select, textarea, [tabindex="0"]'))
                    .filter(el => el.getClientRects().length && !el.disabled);
                const first = controls[0], last = controls[controls.length - 1];
                if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
                else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
            });
            dialog.addEventListener('click', event => {
                const rect = dialog.getBoundingClientRect();
                if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) dialog.close();
            });
        });
        function openDialog(dialog, launcher = document.activeElement) {
            if (!dialog || dialog.open) return;
            dialog.shellLauncher = launcher;
            if (!scrollDepth++) { oldOverflow = document.body.style.overflow; document.body.style.overflow = 'hidden'; }
            dialog.showModal();
            document.querySelectorAll('[data-dialog-open]').forEach(trigger => {
                if (trigger.dataset.dialogOpen === dialog.id) trigger.setAttribute('aria-expanded', 'true');
            });
        }
        async function openInvites(launcher) {
            const dialog = document.getElementById('invite-dialog');
            if (!dialog) return;
            document.querySelectorAll('[data-shell-popover]').forEach(p => closePopover(p));
            // Leave the mobile drawer before giving the invitation dialog focus.
            const drawer = document.getElementById('navigation-drawer');
            const focusTarget = drawer?.open ? drawer.shellLauncher :
                (launcher?.closest('[data-shell-popover]')?.querySelector('[data-popover-trigger]') || launcher);
            if (drawer?.open) drawer.close();
            openDialog(dialog, focusTarget);
            const body = document.getElementById('invite-body');
            // Recheck current server capability on each opening; do not retain a
            // previously authorized invite list after role changes in another tab.
            if (body.dataset.loading === 'true') return;
            body.dataset.loading = 'true';
            body.textContent = 'Загрузка…';
            try {
                const response = await fetch('/app/settings/invites', { headers: { Accept: 'text/html' }, cache: 'no-store' });
                if (!response.ok || response.redirected) throw new Error('load');
                body.innerHTML = await response.text();
                initPopovers(body);
            } catch (_) {
                body.textContent = 'Не удалось загрузить приглашения. Права могли измениться. ';
                const retry = document.createElement('button');
                retry.type = 'button'; retry.textContent = 'Повторить';
                retry.addEventListener('click', () => openInvites(launcher)); body.append(retry);
            } finally { body.dataset.loading = 'false'; }
        }
        document.addEventListener('click', event => {
            const close = event.target.closest('[data-dialog-close]');
            if (close) close.closest('dialog')?.close();
            const toggle = event.target.closest('[data-dialog-open]');
            if (toggle) openDialog(document.getElementById(toggle.dataset.dialogOpen), toggle);
            const invite = event.target.closest('[data-invite-open]');
            if (invite) openInvites(invite);
        });
        if (document.querySelector('[data-invite-dialog][data-auto-open]')) openInvites(document.querySelector('[data-invite-open]'));
    });
})();

/* One authorized, context-bound snapshot drives both the bell and its rows. */
// User-owned display preferences, never authorization or durable acknowledgement.
const AttentionPreferences = {
    defaults() { return { version: 1, connections: true, tasks: true, popup: false, quiet: false, start: '22:00', end: '08:00' }; },
    key(userId) { return Number.isSafeInteger(userId) && userId > 0 ? 'app:attention:display:v1:user:' + userId : null; },
    normalize(value) {
        const defaults = this.defaults();
        if (!value || value.version !== 1) return defaults;
        for (const key of ['connections', 'tasks', 'popup', 'quiet']) if (typeof value[key] !== 'boolean') return defaults;
        for (const key of ['start', 'end']) if (typeof value[key] !== 'string' || !/^([01]\d|2[0-3]):[0-5]\d$/.test(value[key])) return defaults;
        if (value.quiet && value.start === value.end) return defaults;
        return Object.fromEntries(Object.keys(defaults).map(key => [key, value[key]]));
    },
    readLocal(userId) {
        return this.readStoredLocal(userId) || this.defaults();
    },
    readStoredLocal(userId) {
        try {
            const key = this.key(userId), raw = key ? localStorage.getItem(key) : null;
            if (!raw) return null;
            const value = JSON.parse(raw), normalized = this.normalize(value);
            return JSON.stringify(normalized) === JSON.stringify(value) ? normalized : null;
        }
        catch (_) { return null; }
    },
    visible(item, preferences) {
        // Persisted messages cannot be suppressed until mandatory notice kinds have a contract.
        return item.kind === 'notification' || (item.kind === 'connection' && preferences.connections) || (item.kind === 'task' && preferences.tasks);
    },
    quietNow(preferences, now = new Date()) {
        if (!preferences.quiet) return false;
        const minutes = time => Number(time.slice(0, 2)) * 60 + Number(time.slice(3));
        const current = now.getHours() * 60 + now.getMinutes(), start = minutes(preferences.start), end = minutes(preferences.end);
        return start < end ? current >= start && current < end : current >= start || current < end;
    }
};

function notificationPreferences(userId, csrf) {
    return {
        value: AttentionPreferences.defaults(), serverValue: null, message: '', loading: true, saving: false, requestId: 0,
        init() {
            this.load();
            const refresh = () => { if (!document.hidden) this.load(false); };
            const poll = window.setInterval(refresh, 30000);
            document.addEventListener('visibilitychange', refresh);
            window.addEventListener('pagehide', () => {
                window.clearInterval(poll);
                document.removeEventListener('visibilitychange', refresh);
                this.requestId++;
            }, { once: true });
        },
        validValue(value) {
            return !!AttentionPreferences.key(userId) && JSON.stringify(AttentionPreferences.normalize(value)) === JSON.stringify(value);
        },
        async post(path, value) {
            const body = { _csrf: csrf };
            if (value) body.preferences = value;
            const response = await fetch('/app/notifications/preferences' + path, {
                method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-App-User': String(userId) },
                body: JSON.stringify(body), cache: 'no-store'
            });
            if (!response.ok || response.redirected) throw new Error('save');
            const data = await response.json();
            if (data.user_id !== userId || !this.validValue(data.preferences)) throw new Error('identity');
            return data.preferences;
        },
        async load(force = true) {
            if (!force && (this.loading || this.saving)) return;
            if (!AttentionPreferences.key(userId)) { this.loading = false; this.message = 'Не удалось подтвердить пользователя.'; return; }
            const requestId = ++this.requestId;
            this.loading = true;
            try {
                const response = await fetch('/app/notifications/preferences', {
                    headers: { Accept: 'application/json', 'X-App-User': String(userId) }, cache: 'no-store'
                });
                if (!response.ok || response.redirected) throw new Error('load');
                const data = await response.json();
                if (requestId !== this.requestId) return;
                if (data.user_id !== userId || !this.validValue(data.preferences)) throw new Error('identity');
                if (!force && this.serverValue !== null && JSON.stringify(this.serverValue) !== JSON.stringify(this.value)) return;
                this.value = { ...data.preferences };
                this.serverValue = { ...data.preferences };
                this.message = '';
                window.dispatchEvent(new CustomEvent('app:attention-preferences', { detail: { userId, value: this.value } }));
            } catch (_) {
                if (requestId === this.requestId) {
                    if (this.serverValue === null) this.serverValue = { ...this.value };
                    this.message = 'Не удалось загрузить настройки. Повторите попытку.';
                }
            }
            finally { if (requestId === this.requestId) this.loading = false; }
        },
        async save() {
            if (this.loading || this.saving) return;
            if (!AttentionPreferences.key(userId)) { this.message = 'Не удалось подтвердить пользователя.'; return; }
            if (!this.validValue(this.value)) { this.message = 'Проверьте значения настроек и времени.'; return; }
            const requestId = ++this.requestId;
            this.saving = true; this.message = '';
            try {
                const value = await this.post('', this.value);
                if (requestId !== this.requestId) return;
                this.value = { ...value };
                this.serverValue = { ...value };
                window.dispatchEvent(new CustomEvent('app:attention-preferences', { detail: { userId, value } }));
                this.message = 'Настройки сохранены для вашего аккаунта.';
            } catch (_) { if (requestId === this.requestId) this.message = 'Не удалось сохранить настройки.'; }
            finally { if (requestId === this.requestId) this.saving = false; }
        },
        async restore() {
            if (this.loading || this.saving) return;
            const requestId = ++this.requestId;
            this.saving = true; this.message = '';
            try {
                const value = await this.post('/reset', null);
                if (requestId !== this.requestId) return;
                this.value = { ...value };
                this.serverValue = { ...value };
                window.dispatchEvent(new CustomEvent('app:attention-preferences', { detail: { userId, value } }));
                this.message = 'Настройки восстановлены.';
            } catch (_) { if (requestId === this.requestId) this.message = 'Не удалось восстановить настройки.'; }
            finally { if (requestId === this.requestId) this.saving = false; }
        },
        async importLocal() {
            if (this.loading || this.saving) return;
            const local = AttentionPreferences.readStoredLocal(userId);
            if (!local) { this.message = 'В этом браузере нет сохранённых настроек для импорта.'; return; }
            this.value = local;
            await this.save();
        }
    };
}

function notificationsBell(csrf, workspaceId, userId) {
    const destinationRoots = ['', '/app/settings?tab=connections', '/app/tasks', '/app/sources', '/app/analytics', '/app/digests', '/app/scenarios'];
    const destinations = new Set(destinationRoots);
    const validDestination = (value) => {
        if (destinations.has(value)) return true;
        if (typeof value !== 'string' || !value.startsWith('/app/tasks/')) return false;
        const rest = value.slice('/app/tasks/'.length);
        return /^[1-9][0-9]*$/.test(rest);
    };
    return {
        openModal: false, loading: false, items: [], error: '', pending: false,
        hasMore: false, complete: false, contextChanged: false, generation: 0,
        backingTaskEvents: new Map(),
        preferences: AttentionPreferences.defaults(), preferencesOpen: false, popupVisible: false, popupKeys: new Set(), primed: false,
        preferenceRequestId: 0,
        init() {
            this.$watch('items', () => this.updateDockSpace());
            const preferencesChanged = event => {
                if (event.type !== 'storage' && event.detail?.userId !== userId) return;
                this.preferenceRequestId++;
                this.preferences = AttentionPreferences.normalize(event.detail.value);
                this.popupVisible = false; this.updateDockSpace();
            };
            window.addEventListener('app:attention-preferences', preferencesChanged);
            this.$watch('openModal', () => this.updateDockSpace());
            this.$watch('error', () => this.updateDockSpace());
            this.load();
            this.loadPreferences();
            const poll = window.setInterval(() => { if (!document.hidden) { this.load(); this.loadPreferences(); } }, 30000);
            const foreground = () => { if (!document.hidden) this.loadPreferences(); };
            document.addEventListener('visibilitychange', foreground);
            const changed = () => this.reset('Контекст изменился. Перезагрузите страницу.');
            window.addEventListener('app:context-changed', changed);
            window.addEventListener('pagehide', () => {
                window.clearInterval(poll);
                window.removeEventListener('app:context-changed', changed);
                window.removeEventListener('app:attention-preferences', preferencesChanged);
                document.removeEventListener('visibilitychange', foreground);
                this.generation++;
                this.preferenceRequestId++;
                this.contextChanged = true;
                this.items = [];
                this.backingTaskEvents.clear();
            }, { once: true });
        },
        headers() { return { Accept: 'application/json', 'X-App-Workspace': String(workspaceId), 'X-App-User': String(userId) }; },
        async loadPreferences() {
            if (this.contextChanged) return;
            const requestId = ++this.preferenceRequestId;
            try {
                const response = await fetch('/app/notifications/preferences', {
                    headers: { Accept: 'application/json', 'X-App-User': String(userId) }, cache: 'no-store'
                });
                if (requestId !== this.preferenceRequestId) return;
                if (!response.ok || response.redirected) throw new Error('preferences');
                const data = await response.json();
                if (requestId !== this.preferenceRequestId || this.contextChanged || data.user_id !== userId) return;
                if (JSON.stringify(AttentionPreferences.normalize(data.preferences)) !== JSON.stringify(data.preferences)) throw new Error('shape');
                const changed = JSON.stringify(this.preferences) !== JSON.stringify(data.preferences);
                this.preferences = data.preferences;
                if (changed) this.popupVisible = false;
                this.updateDockSpace();
            } catch (_) {
                if (requestId === this.preferenceRequestId) this.popupVisible = false;
            }
        },
        matches(data) { return data?.context?.workspace_id === workspaceId && data?.context?.user_id === userId; },
        reset(message) {
            this.generation++; this.preferenceRequestId++; this.items = []; this.hasMore = false; this.complete = false;
            this.backingTaskEvents.clear();
            this.loading = false; this.pending = false; this.contextChanged = true; this.error = message;
            this.popupVisible = false; this.popupKeys.clear();
            if (this.openModal) this.$nextTick(() => this.$refs.dialog?.focus());
            this.updateDockSpace();
        },
        validItem(item) {
            return item && typeof item.key === 'string' && item.key.length > 0 &&
                ['notification', 'connection', 'task'].includes(item.kind) &&
                item.workspace_id === workspaceId && item.scope === (item.kind === 'connection' ? 'personal' : 'workspace') &&
                ['none', 'read', 'open'].includes(item.action) && validDestination(item.destination) &&
                (item.kind !== 'connection' || (item.read_id === null && item.destination === '/app/settings?tab=connections')) &&
                typeof item.title === 'string' && typeof item.message === 'string' && typeof item.created_at === 'string' &&
                (item.task_id === undefined || (['task', 'notification'].includes(item.kind) && Number.isSafeInteger(item.task_id) && item.task_id > 0)) &&
                (item.kind !== 'notification' || item.task_id === undefined || item.current_task_key !== undefined) &&
                (item.current_task_key === undefined || (item.kind === 'notification' && Number.isSafeInteger(item.task_id) &&
                    (item.current_task_key === null || (typeof item.current_task_key === 'string' && item.current_task_key.startsWith(`task:${workspaceId}:${item.task_id}:failed:`))))) &&
                (item.failure_key === undefined || item.failure_key === null || (typeof item.failure_key === 'string' && item.failure_key === item.current_task_key)) &&
                (item.backing_event === undefined || (item.kind === 'task' && item.backing_event &&
                    typeof item.backing_event.key === 'string' && new RegExp(`^notification:${workspaceId}:[1-9][0-9]*$`).test(item.backing_event.key) &&
                    item.backing_event.failure_key === item.key &&
                    typeof item.backing_event.created_at === 'string')) &&
                (item.read_id === null || (Number.isSafeInteger(item.read_id) && item.read_id > 0));
        },
        reconcile(data) {
            const unavailable = data.unavailable ?? [];
            if (!Array.isArray(unavailable) || unavailable.some(source => !['connections', 'tasks', 'messages'].includes(source)) ||
                data.complete !== (unavailable.length === 0)) throw new Error('sources');
            const kinds = { connections: 'connection', tasks: 'task', messages: 'notification' };
            const failedKinds = new Set(unavailable.map(source => kinds[source]));
            if (typeof data.task_events_authorized !== 'boolean' || !data.task_events_authorized || !failedKinds.has('notification')) this.backingTaskEvents.clear();
            if (data.task_events_authorized && !failedKinds.has('notification') && !failedKinds.has('task')) {
                for (const item of data.notifications) {
                    const event = item.kind === 'task' ? item.backing_event : null;
                    if (event && typeof event.key === 'string' && new RegExp(`^notification:${workspaceId}:[1-9][0-9]*$`).test(event.key) &&
                        event.failure_key === item.key && typeof event.created_at === 'string') {
                        this.backingTaskEvents.set(event.key, { created_at: event.created_at, failure_key: event.failure_key });
                    }
                }
                while (this.backingTaskEvents.size > 100) this.backingTaskEvents.delete(this.backingTaskEvents.keys().next().value);
            }
            const taskStates = new Map();
            for (const item of data.notifications) {
                if (Number.isSafeInteger(item.task_id) && item.task_id > 0) {
                    taskStates.set(item.task_id, item.kind === 'task' ? item.key : item.current_task_key);
                }
            }
            const retained = this.items.filter(item => failedKinds.has(item.kind) && (item.state !== 'unknown' || data.task_events_authorized) &&
                !(item.kind === 'task' && taskStates.has(item.task_id) && taskStates.get(item.task_id) !== item.key))
                .map(item => ({ ...item, read_id: null, action: item.kind === 'notification' ? 'none' : item.action }));
            const merged = new Map([...retained, ...data.notifications].map(item => [item.key, item]));
            const signals = new Map([...merged.values()].filter(item => item.kind === 'task').map(item => [item.key, item]));
            const freshKeys = new Set(data.notifications.map(item => item.key)), rebound = new Set();
            for (const item of merged.values()) {
                const signal = item.kind === 'notification' && signals.get(item.failure_key);
                if (!signal) continue;
                if (freshKeys.has(item.key) && !rebound.has(signal.key)) {
                    signal.read_id = item.read_id; rebound.add(signal.key);
                }
                merged.delete(item.key);
            }
            if (failedKinds.has('notification') && data.task_events_authorized) {
                for (const [key, event] of this.backingTaskEvents) {
                    if ([...merged.values()].some(item => item.key === key || (item.kind === 'task' && item.key === event.failure_key))) continue;
                    merged.set(key, {
                        key, kind: 'notification', scope: 'workspace', workspace_id: workspaceId,
                        title: 'Событие требует проверки', message: 'Состояние сообщения неизвестно, источник временно недоступен.',
                        state: 'unknown', created_at: event.created_at, destination: '', action: 'none', read_id: null
                    });
                }
            }
            const rank = { connection: 0, task: 1, notification: 2 };
            const items = [...merged.values()].sort((a, b) => rank[a.kind] - rank[b.kind] ||
                (a.kind === 'connection' ? 0 : b.created_at.localeCompare(a.created_at) || b.key.localeCompare(a.key)));
            this.hasMore = data.has_more || items.length > 100 || (retained.length > 0 && this.hasMore);
            this.items = items.slice(0, 100); this.complete = data.complete;
        },
        visibleItems() { return this.items.filter(item => AttentionPreferences.visible(item, this.preferences)); },
        filtered() { return !this.preferences.connections || !this.preferences.tasks; },
        showDock() { return this.visibleItems().length > 0 || this.error || this.openModal; },
        showPopup() { return this.popupVisible && !this.openModal && !this.error && this.preferences.popup && !AttentionPreferences.quietNow(this.preferences) && this.visibleItems().length > 0; },
        badge() { const count = this.visibleItems().length; return this.error ? (count ? count + ' · ?' : '?') : String(count) + (this.hasMore && !this.filtered() ? '+' : ''); },
        considerPopup() {
            const keys = this.visibleItems().map(item => item.key);
            const fresh = keys.some(key => !this.popupKeys.has(key));
            this.popupVisible = this.primed && this.complete && fresh && this.preferences.popup && !AttentionPreferences.quietNow(this.preferences);
            this.popupKeys = new Set(keys); this.primed = true;
        },
        updateDockSpace() {
            document.documentElement.style.setProperty('--shell-dock-space',
                this.showDock() ? 'calc(80px + env(safe-area-inset-bottom))' : '0px');
        },
        open() {
            if (this.openModal) return;
            this.returnFocus = document.activeElement;
            this.popupVisible = false; this.openModal = true; this.savedOverflow = document.body.style.overflow;
            document.body.style.overflow = 'hidden';
            this.$nextTick(() => { this.$refs.dialog.showModal(); this.$refs.dialog.focus(); });
            this.load();
        },
        close() {
            if (!this.openModal) return;
            this.$refs.dialog?.close(); this.openModal = false;
            document.body.style.overflow = this.savedOverflow || '';
            this.$nextTick(() => {
                if (this.visibleItems().length || this.error) this.$refs.bell?.focus();
                else document.getElementById('page-content')?.focus();
            });
        },
        trapFocus(event) {
            const controls = Array.from(this.$refs.dialog.querySelectorAll('button, a[href], input, select, textarea, [tabindex="0"]')).filter(el => el.getClientRects().length && !el.disabled);
            const first = controls[0], last = controls[controls.length - 1];
            if (!first) { event.preventDefault(); this.$refs.dialog.focus(); return; }
            if (event.shiftKey && (document.activeElement === first || document.activeElement === this.$refs.dialog)) { event.preventDefault(); last.focus(); }
            else if (!event.shiftKey && (document.activeElement === last || document.activeElement === this.$refs.dialog)) { event.preventDefault(); first.focus(); }
        },
        async readOne(item) {
            if (this.pending || this.loading || this.contextChanged || !this.validItem(item) || !item.read_id || !this.items.some(n => n.key === item.key && n.read_id === item.read_id)) return;
            const generation = this.generation;
            this.pending = true; this.error = '';
            let read = false;
            try {
                const response = await fetch('/app/notifications/read/' + item.read_id, {
                    method: 'POST', headers: { ...this.headers(), 'Content-Type': 'application/x-www-form-urlencoded' },
                    body: new URLSearchParams({ _csrf: csrf, workspace_id: String(workspaceId) })
                });
                if (generation !== this.generation) return;
                if ([401, 403, 409].includes(response.status) || response.redirected) {
                    this.reset('Доступ или контекст изменился. Перезагрузите страницу.'); return;
                }
                if (!response.ok) throw new Error('read');
                const data = await response.json();
                if (generation !== this.generation) return;
                if (!this.matches(data) || !data.ok) {
                    this.reset('Не удалось подтвердить доступ к сообщению. Перезагрузите страницу.'); return;
                }
                // Read acknowledges the shared event, never the active problem.
                this.items = this.items.filter(n => n.kind !== 'notification' || n.key !== item.key);
                for (const n of this.items) if (n.key === item.key) n.read_id = null;
                read = true;
                this.$nextTick(() => this.$refs.dialog?.focus());
            } catch (_) { if (generation === this.generation) this.error = 'Не удалось отметить сообщение прочитанным.'; }
            finally { if (generation === this.generation) this.pending = false; }
            if (read) await this.load();
            if (this.openModal) this.$nextTick(() => this.$refs.dialog?.focus());
        },
        linkLabel(item) {
            if (item.kind === 'connection') return 'Открыть мои подключения';
            if ((item.kind === 'task' || item.kind === 'notification') && Number.isSafeInteger(item.task_id) && item.task_id > 0) return 'Открыть задачу';
            return 'Открыть раздел';
        },
        hasUnread() { return this.visibleItems().some(n => n.read_id); },
        async readAll() {
            if (this.pending || this.loading || this.contextChanged || !this.hasUnread()) return;
            const generation = this.generation;
            this.pending = true; this.error = '';
            let read = false;
            try {
                const response = await fetch('/app/notifications/read-all', {
                    method: 'POST', headers: { ...this.headers(), 'Content-Type': 'application/x-www-form-urlencoded' },
                    body: new URLSearchParams({ _csrf: csrf, workspace_id: String(workspaceId) })
                });
                if (generation !== this.generation) return;
                if ([401, 403, 409].includes(response.status) || response.redirected) {
                    this.reset('Доступ или контекст изменился. Перезагрузите страницу.'); return;
                }
                if (!response.ok) throw new Error('read-all');
                const data = await response.json();
                if (generation !== this.generation) return;
                if (!data || !this.matches(data.context || data)) {
                    this.reset('Не удалось подтвердить доступ к сообщениям. Перезагрузите страницу.'); return;
                }
                read = true;
                this.$nextTick(() => this.$refs.dialog?.focus());
            } catch (_) { if (generation === this.generation) this.error = 'Не удалось отметить все сообщения прочитанными.'; }
            finally { if (generation === this.generation) this.pending = false; }
            if (read) await this.load();
            if (this.openModal) this.$nextTick(() => this.$refs.dialog?.focus());
        },
        async load() {
            if (this.loading || this.pending || this.contextChanged) return;
            const generation = this.generation;
            this.loading = true;
            try {
                const response = await fetch('/app/notifications', { headers: this.headers(), cache: 'no-store' });
                if (generation !== this.generation) return;
                if ([401, 403, 409].includes(response.status) || response.redirected) {
                    this.reset('Доступ или контекст изменился. Перезагрузите страницу.'); return;
                }
                if (!response.ok) throw new Error('load');
                const data = await response.json();
                if (generation !== this.generation) return;
                if (!this.matches(data)) { this.reset('Контекст изменился. Перезагрузите страницу.'); return; }
                if (!Array.isArray(data.notifications) || data.notifications.length > 100 ||
                    data.count !== data.notifications.length || typeof data.complete !== 'boolean' || typeof data.has_more !== 'boolean' ||
                    typeof data.task_events_authorized !== 'boolean' ||
                    !data.notifications.every(n => this.validItem(n)) || new Set(data.notifications.map(n => n.key)).size !== data.notifications.length) throw new Error('shape');
                this.reconcile(data);
                this.error = data.complete ? '' : 'Часть уведомлений недоступна. Состояние не полностью известно.';
                this.considerPopup();
            } catch (_) { if (generation === this.generation) { this.complete = false; this.error = 'Не удалось обновить уведомления. Показаны ранее известные сообщения.'; } }
            finally { if (generation === this.generation) this.loading = false; }
        }
    };
}
