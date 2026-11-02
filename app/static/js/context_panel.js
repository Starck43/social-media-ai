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

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initContextPanel);
    } else {
        initContextPanel();
    }
})();
