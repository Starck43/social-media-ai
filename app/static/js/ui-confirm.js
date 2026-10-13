/* Shared text-only confirmation for ordinary forms and submitter overrides. */
(() => {
    const dialog = document.getElementById('ui-confirm');
    const approved = new WeakSet();
    let pending = null;

    function dismiss() {
        if (!pending) return;
        const { focus, overflow } = pending;
        pending = null;
        if (dialog.open) dialog.close();
        document.body.style.overflow = overflow;
        if (focus?.isConnected) focus.focus();
    }

    // Capture before Alpine/HTMX submit handlers set busy state or issue requests.
    document.addEventListener('submit', (event) => {
        const form = event.target;
        if (!(form instanceof HTMLFormElement)) return;
        if (approved.delete(form)) return;
        const submitter = event.submitter;
        const source = submitter?.hasAttribute('data-confirm') ? submitter : form;
        if (!source.hasAttribute('data-confirm')) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        // Never replace a pending action or fall back to a destructive submit.
        if (pending || !dialog || typeof dialog.showModal !== 'function') return;
        pending = {
            form, submitter,
            focus: submitter || document.activeElement,
            overflow: document.body.style.overflow,
        };
        document.getElementById('ui-confirm-title').textContent = source.dataset.confirmTitle || 'Подтвердить действие?';
        document.getElementById('ui-confirm-message').textContent = source.dataset.confirm;
        dialog.querySelector('[data-confirm-accept]').textContent = source.dataset.confirmLabel || 'Подтвердить';
        try {
            dialog.showModal();
            document.body.style.overflow = 'hidden';
            dialog.querySelector('[data-confirm-cancel]').focus();
        } catch (error) {
            dismiss();
        }
    }, true);

    if (!dialog) return;
    dialog.querySelector('[data-confirm-cancel]').addEventListener('click', dismiss);
    dialog.addEventListener('cancel', (event) => {
        // Keep Escape inside this dialog, not an Alpine editor behind it.
        event.preventDefault();
        event.stopPropagation();
        dismiss();
    });
    // Native dialog focus may move to browser chrome at a Tab boundary. Capture
    // Escape even then, before Alpine window handlers close the editor behind it.
    document.addEventListener('keydown', (event) => {
        if (!dialog.open || event.key !== 'Escape') return;
        event.preventDefault();
        event.stopImmediatePropagation();
        dismiss();
    }, true);
    dialog.addEventListener('close', () => {
        // A queued close event from the previous use must not dismiss a new one.
        if (!dialog.open) dismiss();
    });
    dialog.addEventListener('click', (event) => {
        if (event.target !== dialog) return;
        const rect = dialog.getBoundingClientRect();
        if (event.clientX < rect.left || event.clientX > rect.right ||
            event.clientY < rect.top || event.clientY > rect.bottom) dismiss();
    });
    dialog.querySelector('[data-confirm-accept]').addEventListener('click', () => {
        if (!pending) return;
        const { form, submitter } = pending;
        dismiss();
        if (!form.isConnected || (submitter && (!submitter.isConnected || submitter.disabled || submitter.form !== form))) return;
        // requestSubmit keeps validation, formaction, formnovalidate and button values.
        // This one-shot bypass is consumed synchronously by the replayed submit event.
        approved.add(form);
        try {
            if (submitter) form.requestSubmit(submitter);
            else form.requestSubmit();
        } finally {
            approved.delete(form);
        }
    });
})();
