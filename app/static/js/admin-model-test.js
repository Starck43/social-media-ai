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
        const tenant = document.getElementById('llm-billing-tenant');
        if (mode === 'real' && (!tenant || !tenant.value)) {
            event.preventDefault(); status.textContent = 'Выберите рабочее пространство; нужен включённый личный учёт и администратор платформы.';
            status.scrollIntoView({block: 'center'}); status.focus(); return;
        }
        form.dataset.busy = 'true';
        status.textContent = mode === 'real' ? 'Ожидаем ответ модели…' : mode === 'short' ? 'Проверяем API без генерации…' : 'Выполняем локальную имитацию…';
        // Do not disable the submitter: its name/value must reach the server.
        form.querySelectorAll('button[type="submit"]').forEach(button => button.setAttribute('aria-disabled', 'true'));
    });
    document.querySelectorAll('[data-copy-target]').forEach(button => {
        button.addEventListener('click', async () => {
            const output = document.getElementById(button.dataset.copyTarget);
            const feedback = document.getElementById('llm-copy-status');
            try {
                await navigator.clipboard.writeText(output.textContent);
                feedback.textContent = 'JSON скопирован.';
            } catch (_) {
                const range = document.createRange(); range.selectNodeContents(output);
                const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
                feedback.textContent = 'Копирование недоступно. JSON выделен — скопируйте его вручную.';
                output.focus();
            }
        });
    });
    const result = document.getElementById('llm-result');
    if (result) { result.scrollIntoView({block: 'start'}); result.focus({preventScroll:true}); }
    window.addEventListener('pageshow', () => {
        delete form.dataset.busy; status.textContent = '';
        form.querySelectorAll('button[type="submit"]').forEach(button => button.removeAttribute('aria-disabled'));
    });
})();
