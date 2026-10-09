/* Nonblocking text-only feedback; never native browser dialogs. */
window.uiNotice = function (message, anchor) {
    const root = anchor || document.activeElement?.closest('form') || document.querySelector('main') || document.body;
    let notice = root.querySelector('[data-ui-notice]');
    if (!notice) { notice = document.createElement('p'); notice.dataset.uiNotice = ''; notice.setAttribute('role', 'status'); notice.style.cssText = 'padding:12px;border:1px solid var(--color-border,#cbd5e1);border-radius:8px;color:var(--color-text,#334155);background:var(--color-surface,#fff);overflow-wrap:anywhere'; root.prepend(notice); }
    notice.textContent = message;
};
