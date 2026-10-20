/* Presentation guard only: pending/running admission stays on the server. */
(function () {
    'use strict';
    let submitting = false;
    function sync() {
        const panel = document.getElementById('task-run-progress');
        if (!panel) return;
        const busy = submitting || panel.dataset.taskBusy === 'true';
        document.querySelectorAll('[data-task-mutation]').forEach(control => {
            control.setAttribute('aria-disabled', String(busy));
            control.querySelectorAll('button').forEach(button => { button.disabled = busy; });
        });
        const label = document.querySelector('[data-task-run-label]');
        if (label) {
            const running = busy && panel.dataset.taskStatus === 'running';
            label.classList.toggle('task-run-status', running);
            label.dataset.status = running ? 'running' : panel.dataset.taskStatus;
            label.textContent = submitting ? 'Отправляем…' : busy ? (running ? 'Выполняется' : panel.dataset.taskStatus === 'unconfirmed' ? 'Проверяем статус' : 'В очереди') : 'Выполнить сейчас';
        }
    }
    document.addEventListener('click', event => {
        if (event.target.closest('[data-task-mutation][aria-disabled="true"]')) {
            event.preventDefault();
            event.stopImmediatePropagation();
        }
    }, true);
    document.addEventListener('submit', event => {
        const form = event.target.closest('[data-task-mutation]');
        if (!form) return;
        if (submitting || form.getAttribute('aria-disabled') === 'true') {
            event.preventDefault();
            event.stopImmediatePropagation();
            return;
        }
        // Keep ordinary browser submission (including CSRF and redirect). Lock
        // before another click without disabling successful form controls early.
        submitting = true;
        queueMicrotask(sync);
    }, true);
    document.addEventListener('DOMContentLoaded', () => {
        sync();
        // Alpine teleports header controls after DOMContentLoaded.
        const actions = document.getElementById('page-actions');
        if (actions) new MutationObserver(sync).observe(actions, {childList: true});
    });
    document.addEventListener('htmx:afterSwap', sync);
    function unconfirmed(event) {
        const target = event.detail && (event.detail.elt || event.detail.target);
        const panel = document.getElementById('task-run-progress');
        if (!panel || !target || (target !== panel && !panel.contains(target))) return;
        panel.dataset.taskStatus = 'unconfirmed';
        // A failed poll is never evidence that the worker stopped.
        if (!panel.querySelector('[data-progress-connection-note]')) {
            const note = document.createElement('p');
            note.dataset.progressConnectionNote = '';
            note.className = 'mt-4 text-sm text-amber-800 dark:text-amber-300';
            note.textContent = 'Нет свежих данных. Последний прогресс сохранён; повторный запуск недоступен до подтверждения статуса.';
            panel.append(note);
        }
        sync();
    }
    document.addEventListener('htmx:sendError', unconfirmed);
    document.addEventListener('htmx:responseError', unconfirmed);
    window.addEventListener('pageshow', () => { submitting = false; sync(); });
})();
