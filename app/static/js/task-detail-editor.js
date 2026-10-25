/* Keep the full task editor at its own URL and load it only when requested. */
(function () {
    'use strict';
    document.addEventListener('DOMContentLoaded', () => {
        const dialog = document.getElementById('task-editor-dialog');
        if (!dialog || typeof dialog.showModal !== 'function') return;
        const frame = dialog.querySelector('iframe');
        document.addEventListener('click', event => {
            const trigger = event.target.closest('[data-task-editor-open]');
            if (!trigger) return;
            if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) {
                event.stopPropagation();
                return;
            }
            event.preventDefault();
            if (!frame.hasAttribute('src')) {
                const url = new URL(trigger.href);
                url.searchParams.set('embedded', '1');
                frame.src = url.href;
            }
        }, true);
        dialog.addEventListener('close', () => frame.removeAttribute('src'));
    });
})();
