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
        document.querySelectorAll('[data-shell-popover]').forEach(popover => {
            const trigger = popover.querySelector('[data-popover-trigger]');
            const panel = popover.querySelector('.shell-popover-panel');
            let timer, openedByHover = false;
            const open = (fromHover = false) => {
                if (panel.hidden) openedByHover = fromHover;
                clearTimeout(timer);
                document.querySelectorAll('[data-shell-popover]').forEach(other => { if (other !== popover) closePopover(other); });
                panel.hidden = false;
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
            document.addEventListener('click', event => { if (!popover.contains(event.target)) closePopover(popover); });
            if (popover.hasAttribute('data-hover-popover')) {
                popover.addEventListener('pointerenter', event => {
                    if (popover.closest('.shell-sidebar') && event.pointerType === 'mouse' && matchMedia('(hover: hover)').matches) open(true);
                });
                popover.addEventListener('pointerleave', () => {
                    timer = setTimeout(() => { if (!popover.contains(document.activeElement)) closePopover(popover); }, 180);
                });
            }
            panel.querySelectorAll('[data-theme-choice]').forEach(choice => choice.addEventListener('click', () => closePopover(popover, true)));
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
// Browser-local display preferences, never authorization or durable acknowledgement.
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
    read(userId) {
        try { const key = this.key(userId); return key ? this.normalize(JSON.parse(localStorage.getItem(key))) : this.defaults(); }
        catch (_) { return this.defaults(); }
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

function notificationPreferences(userId) {
    return {
        value: AttentionPreferences.read(userId), message: '',
        save() {
            if (!AttentionPreferences.key(userId)) { this.message = 'Не удалось подтвердить пользователя.'; return; }
            if (this.value.quiet && this.value.start === this.value.end) { this.message = 'Начало и конец тихих часов должны различаться.'; return; }
            const value = AttentionPreferences.normalize(this.value);
            if (JSON.stringify(value) !== JSON.stringify(this.value)) { this.message = 'Проверьте значения времени.'; return; }
            let persisted = false;
            try { localStorage.setItem(AttentionPreferences.key(userId), JSON.stringify(value)); persisted = true; } catch (_) { /* memory-only fallback */ }
            window.dispatchEvent(new CustomEvent('app:attention-preferences', { detail: { userId, value } }));
            this.message = persisted ? 'Сохранено только для вас в этом браузере.' : 'Применено на этой странице. Браузер не разрешил сохранение.';
        },
        restore() { this.value = AttentionPreferences.defaults(); this.save(); }
    };
}

function notificationsBell(csrf, workspaceId, userId) {
    const destinations = new Set(['', '/app/settings?tab=connections', '/app/tasks', '/app/sources', '/app/analytics', '/app/digests', '/app/scenarios']);
    return {
        openModal: false, loading: false, items: [], error: '', pending: false,
        hasMore: false, complete: false, contextChanged: false, generation: 0,
        preferences: AttentionPreferences.read(userId), preferencesOpen: false, popupVisible: false, popupKeys: new Set(), primed: false,
        init() {
            this.$watch('items', () => this.updateDockSpace());
            const preferencesChanged = event => {
                if (event.type === 'storage' && event.key !== AttentionPreferences.key(userId) && event.key !== null) return;
                if (event.type !== 'storage' && event.detail?.userId !== userId) return;
                this.preferences = event.type === 'storage' ? AttentionPreferences.read(userId) : AttentionPreferences.normalize(event.detail.value);
                this.popupVisible = false; this.updateDockSpace();
            };
            window.addEventListener('storage', preferencesChanged);
            window.addEventListener('app:attention-preferences', preferencesChanged);
            this.$watch('openModal', () => this.updateDockSpace());
            this.$watch('error', () => this.updateDockSpace());
            this.load();
            const poll = window.setInterval(() => { if (!document.hidden) this.load(); }, 30000);
            const changed = () => this.reset('Контекст изменился. Перезагрузите страницу.');
            window.addEventListener('app:context-changed', changed);
            window.addEventListener('pagehide', () => {
                window.clearInterval(poll);
                window.removeEventListener('app:context-changed', changed);
                window.removeEventListener('storage', preferencesChanged);
                window.removeEventListener('app:attention-preferences', preferencesChanged);
                this.generation++;
                this.items = [];
            }, { once: true });
        },
        headers() { return { Accept: 'application/json', 'X-App-Workspace': String(workspaceId), 'X-App-User': String(userId) }; },
        matches(data) { return data?.context?.workspace_id === workspaceId && data?.context?.user_id === userId; },
        reset(message) {
            this.generation++; this.items = []; this.hasMore = false; this.complete = false;
            this.loading = false; this.pending = false; this.contextChanged = true; this.error = message;
            this.popupVisible = false; this.popupKeys.clear();
            if (this.openModal) this.$nextTick(() => this.$refs.dialog?.focus());
            this.updateDockSpace();
        },
        validItem(item) {
            return item && typeof item.key === 'string' && item.key.length > 0 &&
                ['notification', 'connection', 'task'].includes(item.kind) &&
                item.workspace_id === workspaceId && item.scope === (item.kind === 'connection' ? 'personal' : 'workspace') &&
                ['none', 'read', 'open'].includes(item.action) && destinations.has(item.destination) &&
                (item.kind !== 'connection' || (item.read_id === null && item.destination === '/app/settings?tab=connections')) &&
                typeof item.title === 'string' && typeof item.message === 'string' && typeof item.created_at === 'string' &&
                (item.read_id === null || (Number.isSafeInteger(item.read_id) && item.read_id > 0));
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
                    !data.notifications.every(n => this.validItem(n)) || new Set(data.notifications.map(n => n.key)).size !== data.notifications.length) throw new Error('shape');
                this.items = data.notifications; this.hasMore = data.has_more; this.complete = data.complete;
                this.error = data.complete ? '' : 'Часть уведомлений недоступна. Состояние не полностью известно.';
                this.considerPopup();
            } catch (_) { if (generation === this.generation) { this.complete = false; this.error = 'Не удалось обновить уведомления. Показаны ранее известные сообщения.'; } }
            finally { if (generation === this.generation) this.loading = false; }
        }
    };
}
