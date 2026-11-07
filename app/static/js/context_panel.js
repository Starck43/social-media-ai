/**
 * Shared Evidence Drawer (context-panel) & Chat Context Handoff (P12-T06)
 *
 * Implements the cross-domain evidence drawer contract:
 * - Listens for clicks on elements with [data-evidence-trigger] or [data-evidence-ref]
 * - Extracts data attributes: title, ref, hash, anchor, boundary, context-type, context-id
 * - Updates the #context-panel dialog DOM elements
 * - Builds and updates the [data-chat-handoff] link pointing to /app/chat?context_type=...
 * - Opens the #context-panel dialog
 */
(function() {
    function initContextPanel() {
        const panel = document.getElementById('context-panel');
        if (!panel) return;

        document.addEventListener('click', function(event) {
            const trigger = event.target.closest('[data-evidence-trigger], [data-evidence-ref], [data-evidence-anchor]');
            if (!trigger || panel.contains(trigger)) return;

            // Do not override explicit close buttons
            if (event.target.closest('[data-dialog-close]')) return;

            const title = trigger.getAttribute('data-evidence-title') || trigger.innerText.trim() || 'Доказательство';
            const ref = trigger.getAttribute('data-evidence-ref') || trigger.getAttribute('data-evidence-anchor') || '';
            const hash = trigger.getAttribute('data-evidence-hash') || '';
            const anchor = trigger.getAttribute('data-evidence-anchor') || ref;
            const boundary = trigger.getAttribute('data-evidence-boundary') || 'cloud';
            const contextType = trigger.getAttribute('data-evidence-context-type') || trigger.getAttribute('data-context-type') || 'object';
            const contextId = trigger.getAttribute('data-evidence-context-id') || trigger.getAttribute('data-context-id') || ref || '';

            // Update panel elements if dynamic fields exist
            const titleEl = panel.querySelector('[data-evidence-title]');
            if (titleEl) titleEl.textContent = title;

            const refEl = panel.querySelector('[data-evidence-ref]');
            if (refEl) refEl.textContent = ref;

            const hashEl = panel.querySelector('[data-evidence-hash]');
            if (hashEl) hashEl.textContent = hash;

            const anchorEl = panel.querySelector('[data-evidence-anchor]');
            if (anchorEl) anchorEl.textContent = anchor;

            const boundaryEl = panel.querySelector('[data-evidence-boundary]');
            if (boundaryEl) {
                boundaryEl.textContent = boundary;
                if (boundary === 'local_only') {
                    boundaryEl.classList.add('text-amber-600', 'font-bold');
                } else {
                    boundaryEl.classList.remove('text-amber-600', 'font-bold');
                }
            }

            // Update chat handoff link
            const chatLink = panel.querySelector('[data-chat-handoff]');
            if (chatLink) {
                const url = new URL('/app/chat', window.location.origin);
                if (contextType) url.searchParams.set('context_type', contextType);
                if (contextId) url.searchParams.set('context_id', contextId);
                if (boundary) url.searchParams.set('boundary', boundary);
                chatLink.href = url.pathname + url.search;
                chatLink.hidden = false;
            }

            // Open panel dialog
            if (typeof panel.showModal === 'function' && !panel.open) {
                panel.showModal();
            } else if (!panel.open) {
                panel.setAttribute('open', '');
            }
        });
    }

    function initChatDock() {
        const dock = document.getElementById('chat-dock-drawer');
        const toggleBtn = document.getElementById('chat-dock-toggle');
        if (!dock || !toggleBtn) return;

        let activeLauncher = toggleBtn;
        const chipEl = dock.querySelector('[data-dock-context-chip]');
        const chipTitleEl = dock.querySelector('[data-dock-chip-title]');
        const removeChipBtn = dock.querySelector('[data-dock-remove-chip]');
        const boundaryBadge = dock.querySelector('[data-dock-boundary-badge]');
        const refusalAlert = dock.querySelector('[data-dock-local-only-refusal]');
        const sendBtn = dock.querySelector('[data-dock-send]');
        const openFullLink = dock.querySelector('[data-dock-open-full]');
        const inputEl = dock.querySelector('[data-dock-input]');
        const formEl = dock.querySelector('[data-dock-form]');
        const typeInput = dock.querySelector('[data-dock-input-type]');
        const idInput = dock.querySelector('[data-dock-input-id]');
        const boundaryInput = dock.querySelector('[data-dock-input-boundary]');

        function applyDockContext(ctx) {
            const cType = (ctx && ctx.contextType) || '';
            const cId = (ctx && ctx.contextId) || '';
            const boundary = (ctx && ctx.boundary) || 'cloud';
            const title = (ctx && ctx.title) || document.getElementById('page-heading')?.textContent?.trim() || 'Текущая страница';

            if (typeInput) typeInput.value = cType;
            if (idInput) idInput.value = cId;
            if (boundaryInput) boundaryInput.value = boundary;

            if (chipEl) {
                chipEl.hidden = Boolean(ctx && ctx.cleared);
                if (chipTitleEl) chipTitleEl.textContent = title;
            }
            if (boundaryBadge) {
                boundaryBadge.textContent = boundary;
                boundaryBadge.setAttribute('data-boundary', boundary);
            }
            const blocked = Boolean(!ctx?.cleared && boundary !== 'cloud');
            if (refusalAlert) refusalAlert.hidden = !blocked;
            if (sendBtn) sendBtn.disabled = blocked;

            if (openFullLink) {
                const url = new URL('/app/chat', window.location.origin);
                if (!ctx?.cleared && cType && cId) {
                    url.searchParams.set('context_type', cType);
                    url.searchParams.set('context_id', cId);
                    url.searchParams.set('boundary', boundary);
                }
                openFullLink.href = url.pathname + url.search;
            }
        }

        function detectPageContext(trigger) {
            const source = trigger?.hasAttribute('data-context-id')
                ? trigger
                : document.querySelector('[data-object-context], [data-evidence-trigger][data-evidence-context-id]');
            if (source) {
                return {
                    contextType: source.getAttribute('data-context-type') || source.getAttribute('data-evidence-context-type') || '',
                    contextId: source.getAttribute('data-context-id') || source.getAttribute('data-evidence-context-id') || '',
                    boundary: source.getAttribute('data-boundary') || source.getAttribute('data-evidence-boundary') || 'cloud',
                    title: source.getAttribute('data-context-title') || source.getAttribute('data-evidence-title') || document.getElementById('page-heading')?.textContent?.trim() || 'Объект',
                };
            }
            return {
                contextType: '',
                contextId: '',
                boundary: 'cloud',
                title: document.getElementById('page-heading')?.textContent?.trim() || 'Текущая страница',
            };
        }

        function openDock(launcher) {
            activeLauncher = launcher || toggleBtn;
            applyDockContext(detectPageContext(launcher));
            toggleBtn.setAttribute('aria-expanded', 'true');
            if (typeof dock.showModal === 'function' && !dock.open) {
                dock.showModal();
            } else if (!dock.open) {
                dock.setAttribute('open', '');
            }
            if (inputEl && !sendBtn?.disabled) {
                inputEl.focus();
            } else {
                dock.querySelector('[data-chat-dock-close]')?.focus();
            }
        }

        function closeDock() {
            if (typeof dock.close === 'function' && dock.open) {
                dock.close();
            } else {
                dock.removeAttribute('open');
            }
            toggleBtn.setAttribute('aria-expanded', 'false');
            (activeLauncher || toggleBtn)?.focus();
        }

        toggleBtn.addEventListener('click', function(event) {
            event.preventDefault();
            openDock(toggleBtn);
        });

        document.addEventListener('click', function(event) {
            const trigger = event.target.closest('[data-ask-agent-trigger]');
            if (trigger) {
                event.preventDefault();
                openDock(trigger);
            }
        });

        dock.querySelector('[data-chat-dock-close]')?.addEventListener('click', function(event) {
            event.preventDefault();
            closeDock();
        });

        dock.addEventListener('keydown', function(event) {
            if (event.key === 'Escape') {
                event.preventDefault();
                closeDock();
            }
        });

        dock.addEventListener('close', function() {
            toggleBtn.setAttribute('aria-expanded', 'false');
            (activeLauncher || toggleBtn)?.focus();
        });

        removeChipBtn?.addEventListener('click', function() {
            applyDockContext({ cleared: true, boundary: 'cloud' });
        });

        formEl?.addEventListener('submit', function(event) {
            const boundary = boundaryInput?.value || 'cloud';
            if (boundary !== 'cloud') {
                event.preventDefault();
                if (refusalAlert) refusalAlert.hidden = false;
                if (sendBtn) sendBtn.disabled = true;
            }
        });
    }

    function boot() {
        initContextPanel();
        initChatDock();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();

