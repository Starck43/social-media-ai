/* Create a draft only. Approval and sending remain separate outbox operations. */
(() => {
  document.addEventListener('submit', async (event) => {
    const form = event.target.closest('[data-triage-reply-draft-form]');
    if (!form) return;
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    const message = form.querySelector('[data-reply-draft-status]');
    if (!button || !message || button.disabled) return;
    button.disabled = true;
    message.replaceChildren();
    message.textContent = 'Создаём черновик…';
    try {
      // Reuse the same key on retry, so a lost response cannot duplicate a draft.
      if (!form.dataset.draftRequestKey) form.dataset.draftRequestKey = crypto.randomUUID();
      const data = new FormData(form);
      data.set('idempotency_key', form.dataset.draftRequestKey);
      const response = await fetch(form.action, {method:'POST', body:data, credentials:'same-origin', headers:{'Accept':'application/json'}});
      if (response.status === 201) {
        message.textContent = 'Черновик создан, отправка не выполнялась. ';
        const link = document.createElement('a');
        link.href = '/app/communications?tab=outbox';
        link.textContent = 'Открыть исходящие';
        link.className = 'underline';
        message.append(link);
        // Do not allow accidental duplicate submissions of an already-created draft.
        return;
      }
      message.textContent = response.status === 404
        ? 'Черновик не создан: исходное сообщение или проверенный контакт недоступны. Проверьте подключение и контакт.'
        : response.status === 403
        ? 'Черновик не создан: нет доступа или сеанс устарел. Обновите страницу.'
        : response.status >= 500
        ? 'Не удалось подтвердить создание черновика. Проверьте исходящие перед повторной попыткой.'
        : 'Черновик не создан. Проверьте исходное сообщение и повторите попытку.';
    } catch (_) {
      message.textContent = 'Не удалось подтвердить создание черновика. Проверьте исходящие перед повторной попыткой.';
    }
    button.disabled = false;
  });
})();
