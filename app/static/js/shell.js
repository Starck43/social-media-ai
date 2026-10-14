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

/* One bounded unread list drives both counter and rows; reads remain workspace-shared. */
function notificationsBell(csrf) {
    return {
        openModal: false, loading: false, items: [], error: '', pending: false,
        init() {
            this.$watch('items', () => this.updateDockSpace());
            this.$watch('openModal', () => this.updateDockSpace());
            this.load();
        },
        updateDockSpace() {
            document.documentElement.style.setProperty('--shell-dock-space',
                this.items.length || this.openModal ? 'calc(80px + env(safe-area-inset-bottom))' : '0px');
        },
        open() {
            if (this.openModal) return;
            this.returnFocus = document.activeElement;
            this.openModal = true;
            this.savedOverflow = document.body.style.overflow;
            document.body.style.overflow = 'hidden';
            this.$nextTick(() => { this.$refs.dialog.showModal(); this.$refs.dialog.focus(); });
            this.load();
        },
        close() {
            if (!this.openModal) return;
            this.$refs.dialog?.close();
            this.openModal = false;
            document.body.style.overflow = this.savedOverflow || '';
            this.$nextTick(() => {
                if (this.items.length) this.$refs.bell?.focus();
                else document.getElementById('page-content')?.focus();
            });
        },
        trapFocus(event) {
            const controls = Array.from(this.$refs.dialog.querySelectorAll('button, a[href], [tabindex="0"]')).filter(el => el.getClientRects().length && !el.disabled);
            const first = controls[0], last = controls[controls.length - 1];
            if (!first) { event.preventDefault(); this.$refs.dialog.focus(); return; }
            if (event.shiftKey && (document.activeElement === first || document.activeElement === this.$refs.dialog)) { event.preventDefault(); last.focus(); }
            else if (!event.shiftKey && (document.activeElement === last || document.activeElement === this.$refs.dialog)) { event.preventDefault(); first.focus(); }
        },
        async readOne(item) {
            if (this.pending || this.loading) return;
            this.pending = true; this.error = '';
            let read = false;
            try {
                const response = await fetch('/app/notifications/read/' + item.id, {
                    method: 'POST', headers: { Accept: 'application/json', 'Content-Type': 'application/x-www-form-urlencoded' },
                    body: new URLSearchParams({ _csrf: csrf })
                });
                if (!response.ok || response.redirected) throw new Error('read');
                const data = await response.json();
                if (!data.ok) throw new Error('read');
                this.items = this.items.filter(n => n.id !== item.id);
                read = true;
                // Reading can remove the focused row. The dialog retains a valid focus target.
                this.$nextTick(() => this.$refs.dialog?.focus());
            } catch (_) { this.error = 'Не удалось отметить уведомление прочитанным.'; }
            finally { this.pending = false; }
            if (read) await this.load();
            if (this.openModal) this.$nextTick(() => this.$refs.dialog?.focus());
        },
        async load() {
            if (this.loading || this.pending) return;
            this.loading = true;
            try {
                const response = await fetch('/app/notifications', { headers: { Accept: 'application/json' }, cache: 'no-store' });
                if (!response.ok || response.redirected) throw new Error('load');
                const data = await response.json();
                if (!Array.isArray(data.notifications)) throw new Error('shape');
                this.items = data.notifications; this.error = '';
            } catch (_) { this.error = 'Не удалось обновить уведомления.'; }
            finally { this.loading = false; }
        }
    };
}
