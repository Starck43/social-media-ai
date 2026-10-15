/* Progressive enhancement only: POST, mode buttons and confirmation work without JS. */
(() => {
    'use strict';
    const form = document.getElementById('llm-test-form');
    if (!form) return;
    const status = document.getElementById('llm-submit-status');
    form.addEventListener('submit', event => {
        if (form.dataset.busy === 'true') { event.preventDefault(); return; }
        const mode = event.submitter && event.submitter.value;
        if (!['short', 'mock', 'real'].includes(mode)) { event.preventDefault(); return; }
        if (mode === 'mock' && !document.getElementById('llm-prompt').value.trim()) {
            event.preventDefault(); status.textContent = 'Введите вопрос для mock-теста.';
            document.getElementById('llm-prompt').focus(); return;
        }
        if (mode === 'real' && !document.getElementById('llm-confirm-real').checked) {
            event.preventDefault(); status.textContent = 'Сначала разрешите один платный вызов.';
            document.getElementById('llm-confirm-real').focus(); return;
        }
        form.dataset.busy = 'true';
        status.textContent = mode === 'real' ? 'Ожидаем ответ провайдера…' : 'Выполняем локальную проверку…';
        // Do not disable the submitter: its name/value must reach the server.
        form.querySelectorAll('button[type="submit"]').forEach(button => button.setAttribute('aria-disabled', 'true'));
    });
    window.addEventListener('pageshow', () => {
        delete form.dataset.busy; status.textContent = '';
        form.querySelectorAll('button[type="submit"]').forEach(button => button.removeAttribute('aria-disabled'));
    });
})();
