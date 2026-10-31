function chatApp() {
        return {
            panelOpen: true,
            modalTitle: '',
            modalLoading: false,
            modalError: '',
            workspaceTasks: [],
            workspaceSearch: '',
            workspaceOffset: 0,
            workspaceNextOffset: null,
            workspaceListLoading: false,
            workspaceListError: '',
            workspaceTask: null,
            workspaceTaskId: null,
            workspaceTaskLoading: false,
            workspaceTaskError: '',
            workspaceCheckedAt: '',
            workspaceListRequest: 0,
            workspaceTaskRequest: 0,
            awaitingConfirmation: Boolean(window.__chatAwaitingConfirmation),
            attachmentPolicy: window.__chatAttachmentPolicy || null,
            attachments: [],
            attachmentUploading: false,
            attachmentSeq: 0,
            draftNotice: '',
            pending: false,
            sending: false,
            abortController: null,
            notice: '',
            confirmation: null,
            autoScroll: true,
            messages: [],
            thinkingSeconds: 0,
            thinkingTimer: null,
            starters: [
                { icon: '📊', title: 'Сводка за неделю', hint: 'Главные темы и активность', text: 'Сводка за неделю' },
                { icon: '🗂️', title: 'Мои источники', hint: 'Что сейчас собирается', text: 'Покажи мои источники' },
                { icon: '🔎', title: 'Разовый сбор и анализ', hint: 'Уточним охват и покажем план', text: 'Хочу разово собрать и проанализировать недавние публикации о ремонте в Казани. Исключи рекламу, покажи ссылки и причины. Сначала уточни источники, период и ограничения.' },
                { icon: '⏰', title: 'Новое расписание', hint: 'Регулярная задача с подтверждением', text: 'Хочу настроить регулярный сбор и анализ. Сначала уточни цель, источники, период, результат и ограничения, покажи расписание и дождись моего подтверждения.' },
                { icon: '📈', title: 'Статус и результаты', hint: 'Продолжить с задачей или запуском', text: 'Покажи статус и доступные результаты задачи, которую мы обсуждали, и предложи, что проверить дальше.' },
                { icon: '✨', title: 'Возможности', hint: 'Команды и примеры', text: '/help' },
            ],
            csrf: document.querySelector('input[name="_csrf"]')?.value || '',

            init() {
                try { this.panelOpen = sessionStorage.getItem('chat.workspace.panel') !== 'closed'; } catch (_) { /* Storage may be unavailable in private sessions. */ }
                this.loadWorkspaceTasks();
                // Event delegation for JSON copy buttons (added via x-html)
                document.addEventListener('click', (e) => {
                    const btn = e.target.closest('[data-copy-json]')
                    if (btn) {
                        const json = btn.getAttribute('data-copy-json')
                            
                        navigator.clipboard.writeText(json).then(() => {
                            const orig = btn.innerHTML
                            btn.innerHTML = '✓'
                            setTimeout(() => { btn.innerHTML = orig }, 1500)
                        }).catch(() => { this.notice = 'Не удалось скопировать. Выделите текст сообщения вручную.' })
                    }
                })
                // The page scrolls inside .shell-content, not window: listen there.
                this._scroller = this.scrollContainer();
                if (this._scroller && this._scroller.addEventListener) {
                    this._onScrollBound = () => this.onScroll();
                    const target = (this._scroller === document.scrollingElement || this._scroller === document.documentElement)
                        ? window
                        : this._scroller;
                    target.addEventListener('scroll', this._onScrollBound, { passive: true });
                    window.addEventListener('resize', this._onScrollBound);
                }
                if (typeof ResizeObserver !== 'undefined') {
                    this._chatResizeObserver = new ResizeObserver(() => this.onScroll());
                    if (this.$refs.messages) this._chatResizeObserver.observe(this.$refs.messages);
                    if (this.$refs.form) this._chatResizeObserver.observe(this.$refs.form);
                }
                // Initialize messages from server-rendered data
                this.messages = this.visibleMessages(window.__chatMessages || [])
                // Scroll to bottom on page load
                this.$nextTick(() => { this.scrollToBottom(); this.onScroll(); })
            },

            destroy() {
                this._chatResizeObserver?.disconnect();
                const target = this._scroller === document.scrollingElement || this._scroller === document.documentElement ? window : this._scroller;
                target?.removeEventListener('scroll', this._onScrollBound);
                window.removeEventListener('resize', this._onScrollBound);
                this.stopThinking();
            },
            toggleWorkspacePanel() {
                this.panelOpen = !this.panelOpen;
                try { sessionStorage.setItem('chat.workspace.panel', this.panelOpen ? 'open' : 'closed'); } catch (_) { /* Keep the panel usable without storage. */ }
                this.$nextTick(() => requestAnimationFrame(() => {
                    // Alpine's x-show schedules visibility in a frame. Focus only
                    // after the tab/panel is visible, including in Safari.
                    const target = this.panelOpen ? this.$refs.workspacePanel : this.$refs.panelToggle;
                    target?.focus();
                    this.onScroll();
                    if (this.panelOpen && !window.matchMedia('(min-width: 1200px)').matches) target?.scrollIntoView({block: 'start'});
                }));
            },
            openWorkspacePage(path, title, launcher = document.activeElement) {
                let url;
                try { url = new URL(path, window.location.origin); } catch (_) { return; }
                // Keep authentication and canonical page permission checks in the iframe.
                if (url.origin !== window.location.origin || !url.pathname.startsWith('/app/') || url.pathname.startsWith('/app/chat')) return;
                url.searchParams.set('embedded', '1');
                this.modalTitle = title;
                this.modalLoading = true;
                this.modalError = '';
                this._modalLauncher = launcher;
                this.$refs.workspaceFrame.src = url.href;
                if (!this.$refs.workspaceDialog.open) this.$refs.workspaceDialog.showModal();
            },
            workspaceFrameLoaded() {
                const frame = this.$refs.workspaceFrame;
                if (!frame.hasAttribute('src')) return;
                this.modalLoading = false;
                try {
                    const doc = frame.contentDocument;
                    if (!doc || !frame.contentWindow.location.pathname.startsWith('/app/')) {
                        this.modalError = 'Страница недоступна. Проверьте вход и доступ к данным.';
                        return;
                    }
                    // Existing pages keep their scripts, forms, CSRF and permission checks.
                    // Hide only duplicate shell navigation inside this embedded document.
                    const style = doc.createElement('style');
                    style.textContent = '.shell-sidebar,.shell-drawer,.shell-navbar,.shell-skip,.shell-guest-theme{display:none!important}.shell-layout{display:block!important}.shell-page{margin:0!important;min-width:0}.shell-content{padding:16px!important}';
                    doc.head.append(style);
                    doc.addEventListener('keydown', event => {
                        if (event.key === 'Escape' && !doc.querySelector('dialog[open]')) {
                            event.preventDefault();
                            this.$refs.workspaceDialog.close();
                        }
                    });
                } catch (_) { this.modalError = 'Не удалось открыть страницу в этом окне.'; }
            },
            workspaceModalClosed() {
                this.$refs.workspaceFrame.removeAttribute('src');
                this.modalLoading = false;
                this.modalError = '';
                const launcher = this._modalLauncher;
                this.$nextTick(() => (launcher?.isConnected && launcher.getClientRects().length ? launcher : this.$refs.input)?.focus());
                if (this.workspaceTaskId) this.refreshWorkspaceTask();
                this.loadWorkspaceTasks(this.workspaceOffset);
            },
            draftAction(text) {
                if (this.pending || this.awaitingConfirmation) return;
                const input = this.$refs.input;
                const existing = input.value.trim();
                input.value = existing ? input.value + '\n\n' + text : text;
                this.draftNotice = existing ? 'Запрос добавлен к вашему черновику. Проверьте перед отправкой.' : 'Запрос подготовлен. Уточните детали и отправьте.';
                this.resizeInput();
                input.focus();
                this.$nextTick(() => this.scrollToBottom());
            },
            async workspaceRead(url) {
                const response = await fetch(url, {headers: {'Accept': 'application/json'}, cache: 'no-store'});
                const data = await response.json();
                if (!response.ok) {
                    const error = new Error(data.error || 'Не удалось загрузить данные');
                    error.status = response.status;
                    throw error;
                }
                return data;
            },
            async loadWorkspaceTasks(offset = 0) {
                if (!document.querySelector('[data-chat-task-picker]')) return;
                const request = ++this.workspaceListRequest;
                this.workspaceListLoading = true;
                this.workspaceListError = '';
                this.workspaceTasks = [];
                this.workspaceNextOffset = null;
                try {
                    const data = await this.workspaceRead('/app/chat/workspace/tasks?' + new URLSearchParams({search: this.workspaceSearch, offset}));
                    if (request !== this.workspaceListRequest) return;
                    this.workspaceTasks = data.tasks || [];
                    this.workspaceOffset = offset;
                    this.workspaceNextOffset = data.next_offset;
                } catch (error) {
                    if (request === this.workspaceListRequest) {
                        this.workspaceListError = error.status === 403 ? 'Доступ к задачам изменился.' : 'Не удалось загрузить задачи. Повторите попытку.';
                        if (error.status === 403) this.clearWorkspaceTask();
                    }
                } finally {
                    if (request === this.workspaceListRequest) this.workspaceListLoading = false;
                }
            },
            async selectWorkspaceTask(id) {
                if (this.pending || this.awaitingConfirmation) return;
                this.clearWorkspaceTask();
                this.workspaceTaskId = Number(id);
                await this.refreshWorkspaceTask();
            },
            clearWorkspaceTask() {
                if (this.pending || this.awaitingConfirmation) return;
                ++this.workspaceTaskRequest;
                this.workspaceTaskId = null;
                this.workspaceTask = null;
                this.workspaceTaskError = '';
                this.workspaceCheckedAt = '';
                this.workspaceTaskLoading = false;
            },
            async refreshWorkspaceTask() {
                if (!this.workspaceTaskId) return;
                const id = this.workspaceTaskId;
                const request = ++this.workspaceTaskRequest;
                this.workspaceTaskLoading = true;
                this.workspaceTaskError = '';
                // Do not keep stale plan/results visible after access changes.
                this.workspaceTask = null;
                this.workspaceCheckedAt = '';
                try {
                    const data = await this.workspaceRead('/app/chat/workspace/tasks/' + id);
                    if (request !== this.workspaceTaskRequest || id !== this.workspaceTaskId) return;
                    this.workspaceTask = data;
                    this.workspaceCheckedAt = new Date().toLocaleTimeString('ru-RU');
                } catch (error) {
                    if (request === this.workspaceTaskRequest) {
                        this.workspaceTaskError = [403, 404].includes(error.status) ? 'Задача больше недоступна. Снимите выбор или выберите другую.' : 'Не удалось обновить задачу. Повторите попытку.';
                    }
                } finally {
                    if (request === this.workspaceTaskRequest) this.workspaceTaskLoading = false;
                }
            },
            workspaceContextBlocked() {
                return Boolean(!this.awaitingConfirmation && this.workspaceTaskId &&
                    (this.workspaceTaskLoading || this.workspaceTaskError));
            },
            workspacePlanNotice(status) {
                return ({legacy: 'Для этой задачи нет сохранённого плана.', source_access_required: 'Для просмотра плана нужен доступ к источникам.', scenario_access_required: 'Для просмотра плана нужен доступ к сценарию.', scope_changed: 'Источники изменились. Проверьте план задачи.', source_unavailable: 'Один из источников недоступен.'})[status] || 'План недоступен. Откройте подробности задачи.';
            },
            workspaceWindow(window) {
                if (!window) return 'Период не указан';
                return window.type === 'rolling' ? 'Последние ' + window.days + ' дн.' : (window.start || 'С начала') + ' — ' + (window.end || 'по текущую дату');
            },
            workspaceRunStatus(status) {
                return ({pending: 'В очереди', running: 'Выполняется', done: 'Обработка завершена — проверьте результат и охват', failed: 'Завершилось с ошибкой', cancelled: 'Отменено', unavailable: 'Сведения о запуске недоступны'})[status] || 'Состояние не подтверждено';
            },
            visibleMessages(items) {
                return items.filter(m => ['user', 'assistant'].includes(m.role) && typeof m.content === 'string' && m.content.trim());
            },
            attachmentEnabled() {
                return Boolean(this.attachmentPolicy && this.attachmentPolicy.enabled);
            },
            attachmentAcceptAttr() {
                if (!this.attachmentPolicy) return '';
                const docs = this.attachmentPolicy.allowed_document_extensions || [];
                const imgs = this.attachmentPolicy.allowed_image_extensions || [];
                return [...docs, ...imgs].join(',');
            },
            handleAttachClick() {
                if (!this.attachmentEnabled()) {
                    this.$refs.attachmentInfo?.showModal();
                    return;
                }
                this.$refs.attachmentInput?.click();
            },
            openAttachmentLimits() {
                this.$refs.attachmentInfo?.showModal();
            },
            chooseFileFromDialog() {
                this.$refs.attachmentInfo?.close();
                if (this.attachmentEnabled()) {
                    this.$refs.attachmentInput?.click();
                }
            },
            attachmentStateLabel(att) {
                if (!att) return '';
                if (att.state === 'selected') return 'Выбрано — проверка…';
                if (att.state === 'rejected') return 'Отклонено';
                if (att.state === 'processed') return 'Обработано';
                if (att.requires_confirmation) {
                    return att.confirmed ? 'Подтверждено' : 'Требуется подтверждение';
                }
                return 'Готово к отправке';
            },
            readyAttachments() {
                return this.attachments.filter(
                    (a) => a.id && (a.state === 'uploaded' || a.state === 'confirm_required')
                );
            },
            hasRejectedAttachments() {
                return this.attachments.some((a) => a.state === 'rejected');
            },
            hasUnconfirmedLargeAttachments() {
                const ready = this.readyAttachments();
                if (!ready.length) return false;
                if (ready.some((a) => a.requires_confirmation && !a.confirmed)) return true;
                const threshold = this.attachmentPolicy?.confirm_token_threshold || 1500;
                const totalTokens = ready.reduce((acc, a) => acc + (Number(a.estimated_tokens) || 0), 0);
                return totalTokens >= threshold && !ready.every((a) => a.confirmed);
            },
            toggleAttachmentConfirm(clientId, checked) {
                const item = this.attachments.find((a) => a.clientId === clientId);
                if (item) {
                    item.confirmed = Boolean(checked);
                }
            },
            async onFilesSelected(event) {
                const inputEl = event?.target;
                const files = Array.from(inputEl?.files || []);
                // Reset immediately so selecting the same file again triggers change in Safari/Chrome
                if (inputEl) inputEl.value = '';
                if (!files.length || !this.attachmentEnabled()) return;

                const maxFiles = this.attachmentPolicy?.max_files_per_turn || 4;
                const activeCount = this.attachments.filter((a) => a.state !== 'rejected').length;
                if (activeCount + files.length > maxFiles) {
                    this.notice = `Можно прикрепить не более ${maxFiles} файлов к одному сообщению.`;
                    return;
                }
                this.notice = '';
                for (const file of files) {
                    await this.uploadAttachment(file);
                }
            },
            async uploadAttachment(file) {
                const clientId = 'att-' + (++this.attachmentSeq) + '-' + Date.now();
                const entry = {
                    clientId,
                    id: null,
                    filename: file.name || 'file',
                    size_bytes: file.size || 0,
                    state: 'selected',
                    summary_label: 'Проверка формата и оценка токенов…',
                    estimated_tokens: 0,
                    estimated_cost_usd: 0,
                    requires_confirmation: false,
                    confirmation_reason: '',
                    omitted_note: '',
                    confirmed: false,
                    error: '',
                };
                this.attachments.push(entry);
                this.attachmentUploading = true;
                try {
                    const formData = new FormData();
                    formData.append('_csrf', this.csrf);
                    formData.append('file', file);
                    if (this.workspaceTaskId) {
                        formData.append('task_id', String(this.workspaceTaskId));
                    }
                    const response = await fetch('/app/chat/attachments', {
                        method: 'POST',
                        headers: {
                            'X-Requested-With': 'XMLHttpRequest',
                            'Accept': 'application/json',
                        },
                        body: formData,
                    });
                    let data = {};
                    try {
                        data = await response.json();
                    } catch (_) {
                        data = {};
                    }
                    if (!response.ok || data.error || !data.attachment) {
                        entry.state = 'rejected';
                        entry.summary_label = '';
                        entry.error = data.error || `Файл отклонён (${response.status}).`;
                        return;
                    }
                    const srv = data.attachment;
                    entry.id = srv.id;
                    entry.filename = srv.filename || entry.filename;
                    entry.summary_label = srv.summary_label || '';
                    entry.estimated_tokens = Number(srv.estimated_tokens) || 0;
                    entry.estimated_cost_usd = Number(srv.estimated_cost_usd) || 0;
                    entry.requires_confirmation = Boolean(srv.requires_confirmation);
                    entry.confirmation_reason = srv.confirmation_reason || '';
                    entry.omitted_note = srv.omitted_note || '';
                    entry.state = entry.requires_confirmation ? 'confirm_required' : 'uploaded';
                } catch (err) {
                    console.error('Attachment upload error:', err);
                    entry.state = 'rejected';
                    entry.summary_label = '';
                    entry.error = err.message || 'Не удалось загрузить файл.';
                } finally {
                    this.attachmentUploading = this.attachments.some((a) => a.state === 'selected');
                }
            },
            async removeAttachment(clientId) {
                const idx = this.attachments.findIndex((a) => a.clientId === clientId);
                if (idx === -1) return;
                const [removed] = this.attachments.splice(idx, 1);
                if (removed?.id) {
                    try {
                        await fetch(`/app/chat/attachments/${encodeURIComponent(removed.id)}/delete`, {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/x-www-form-urlencoded',
                                'X-Requested-With': 'XMLHttpRequest',
                                'Accept': 'application/json',
                            },
                            body: new URLSearchParams({ '_csrf': this.csrf }),
                        });
                    } catch (_) {
                        // Best-effort server cleanup
                    }
                }
            },
            async cleanupStagedAttachments() {
                const staged = this.attachments.filter((a) => a.id);
                this.attachments = [];
                if (!staged.length) return;
                try {
                    await fetch('/app/chat/attachments/cleanup', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/x-www-form-urlencoded',
                            'X-Requested-With': 'XMLHttpRequest',
                            'Accept': 'application/json',
                        },
                        body: new URLSearchParams({ '_csrf': this.csrf }),
                    });
                } catch (_) {
                    // Best-effort cleanup
                }
            },
            requestConfirmation(kind, id = null) {
                if (this.pending) return;
                this.confirmation = { kind, id, label: kind === 'clear' ? 'Очистить историю диалога?' : 'Удалить сообщение?' };
            },
            performConfirmation() {
                const action = this.confirmation; this.confirmation = null;
                if (action?.kind === 'clear') this.clearHistory();
                else if (action?.kind === 'send_large_attachments') {
                    for (const att of this.readyAttachments()) {
                        att.confirmed = true;
                    }
                    this.sendMessage(action.preset || '', true);
                }
                else if (action) this.deleteMessage(action.id);
            },
            isLast(message) { return this.messages.at(-1)?.id === message.id },
            thinkingLabel() {
                if (this.thinkingSeconds >= 20) return 'Почти готово…'
                if (this.thinkingSeconds >= 6) return 'Работаю с данными…'
                return 'Думаю…'
            },
            startThinking() {
                this.stopThinking()
                this.thinkingSeconds = 0
                this.thinkingTimer = setInterval(() => { this.thinkingSeconds++ }, 1000)
            },
            stopThinking() {
                if (this.thinkingTimer) clearInterval(this.thinkingTimer)
                this.thinkingTimer = null
            },
            resizeInput() { const el = this.$refs.input; el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 180) + 'px'; },
            stopGeneration() {
                if (this.abortController) this.abortController.abort();
                if (this.attachments.some((a) => a.id)) {
                    this.cleanupStagedAttachments();
                }
            },
            safeMarkdown(html) {
                const doc = new DOMParser().parseFromString(html, 'text/html');
                const allowed = new Set(['P','BR','STRONG','EM','DEL','UL','OL','LI','H1','H2','H3','H4','BLOCKQUOTE','PRE','CODE','A','TABLE','THEAD','TBODY','TR','TH','TD','HR','DIV','SPAN','BUTTON']);
                for (const el of Array.from(doc.body.querySelectorAll('*')).reverse()) {
                    if (!allowed.has(el.tagName)) { el.replaceWith(doc.createTextNode(el.textContent || '')); continue; }
                    for (const attr of Array.from(el.attributes)) {
                        const safe = (attr.name === 'href' && el.tagName === 'A' && /^(https?:|mailto:|\/(?!\/)|#)/i.test(attr.value.trim())) || (attr.name === 'data-copy-json' && el.tagName === 'BUTTON') || (attr.name === 'type' && el.tagName === 'BUTTON') || (attr.name === 'class' && /^(json-|whitespace-pre-wrap|break-all|text-xs|text-cyan-200)/.test(attr.value));
                        if (!safe) el.removeAttribute(attr.name);
                    }
                    if (el.tagName === 'BUTTON') el.setAttribute('type','button');
                }
                return doc.body.innerHTML;
            },

            renderMarkdown(text) {
                if (!text) return ""

                // Extract ```json ... ``` blocks and standalone JSON, replace with placeholders
                const jsonBlocks = []
                let id = 0

                // 1) ```json blocks
                text = text.replace(/```json\s*([\s\S]*?)```/g, (match, code) => {
                    const idx = id++
                    jsonBlocks.push({ type: 'fenced', code: code })
                    return `%%JSON_BLOCK_${idx}%%`
                })

                // 2) Standalone JSON (entire message is one object/array)
                const stripped = text.trim()
                if ((stripped.startsWith("{") || stripped.startsWith("[")) && !stripped.startsWith("<")) {
                    try {
                        JSON.parse(stripped)
                        const idx = id++
                        jsonBlocks.push({ type: 'standalone', code: stripped })
                        text = `%%JSON_BLOCK_${idx}%%`
                    } catch (e) {
                        // Not valid JSON, fall through to markdown
                    }
                }

                let html
                try {
                    html = marked.parse(text)
                } catch (e) {
                    html = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\n/g, "<br>")
                }

                // Restore JSON blocks — use data-* + event delegation to avoid escaping hell
                html = html.replace(/%%JSON_BLOCK_(\d+)%%/g, (match, num) => {
                    const block = jsonBlocks[parseInt(num)]
                    if (!block) return match
                    const display = block.code
                        .replace(/&/g, '&amp;')
                        .replace(/</g, '&lt;')
                        .replace(/>/g, '&gt;')
                    const attrSafe = block.code
                        .replace(/&/g, '&amp;')
                        .replace(/"/g, '&quot;')
                        .replace(/</g, '&lt;')
                        .replace(/>/g, '&gt;')
                    return (
                        '<div class="json-block">' +
                        '<div class="json-block-header">' +
                        '<span class="json-block-label">JSON</span>' +
                        '<button type="button" class="json-copy-btn" data-copy-json="' + attrSafe + '">Копировать</button>' +
                        '</div>' +
                        '<pre class="whitespace-pre-wrap break-all text-xs text-cyan-200">' +
                        display +
                        '</pre></div>'
                    )
                })

                return this.safeMarkdown(html)
            },

            async loadTranscript() {
                const response = await fetch('/app/chat', {
                    headers: {
                        'X-Requested-With': 'XMLHttpRequest',
                        'Accept': 'application/json, text/plain, */*',
                    },
                })
                const data = await response.json()
                if (!response.ok) throw new Error(data.error || 'Не удалось обновить диалог')
                this.messages = this.visibleMessages(data.messages || [])
                this.awaitingConfirmation = Boolean(data.awaiting_confirmation);
                this.$nextTick(() => this.scrollToBottom())
            },

            async sendMessage(preset = '', forceConfirmLarge = false) {
                const textarea = this.$refs.input
                const rawText = (typeof preset === 'string' && preset ? preset : textarea.value).trim()
                const readyAtts = this.readyAttachments()
                if ((!rawText && !readyAtts.length) || this.pending || this.workspaceContextBlocked()) return
                if (this.attachmentUploading) {
                    this.notice = 'Дождитесь завершения проверки и загрузки вложений.'
                    return
                }
                if (this.hasRejectedAttachments()) {
                    this.notice = 'Удалите отклонённые вложения перед отправкой сообщения.'
                    return
                }
                if (!forceConfirmLarge && this.hasUnconfirmedLargeAttachments()) {
                    const totalTokens = readyAtts.reduce((acc, a) => acc + (Number(a.estimated_tokens) || 0), 0)
                    const modelMax = this.attachmentPolicy?.model_max_tokens || 4096
                    this.confirmation = {
                        kind: 'send_large_attachments',
                        preset: typeof preset === 'string' ? preset : '',
                        label: `Крупное вложение (~${totalTokens} токенов при лимите модели ${modelMax}). Подтвердить отправку и расход токенов?`,
                    }
                    return
                }

                const text = rawText || 'Проанализируй прикреплённые файлы в контексте задачи.'
                const attachmentIds = readyAtts.map((a) => a.id).join(',')
                const confirmLarge = (forceConfirmLarge || readyAtts.some((a) => a.confirmed)) ? '1' : ''

                this.notice = ''
                this.draftNotice = '';
                const selectedId = this.workspaceTaskId;

                this.pending = true
                this.sending = true
                this.abortController = new AbortController()
                this.startThinking()

                // Optimistic: show user message immediately
                const optimisticText = readyAtts.length
                    ? `[Вложения: ${readyAtts.map((a) => a.filename).join(', ')}]

${text}`
                    : text
                const userMsg = {
                    id: 'pending-' + Date.now(),
                    role: 'user',
                    content: optimisticText,
                    created_at: new Date().toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' }),
                }
                this.messages.push(userMsg)
                textarea.value = ''
                textarea.style.height = 'auto'
                this.$nextTick(() => this.scrollToBottom())

                try {
                    const response = await fetch('/app/chat', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/x-www-form-urlencoded',
                            'X-Requested-With': 'XMLHttpRequest',
                            'Accept': 'application/json, text/plain, */*',
                        },
                        body: new URLSearchParams({
                            '_csrf': this.csrf,
                            'text': text,
                            ...(selectedId ? {task_id: selectedId} : {}),
                            ...(attachmentIds ? {attachment_ids: attachmentIds} : {}),
                            ...(confirmLarge ? {confirm_large: confirmLarge} : {}),
                        }),
                        signal: this.abortController.signal,
                    })

                    let data = {}
                    try {
                        data = await response.json()
                    } catch (parseError) {
                        // A gateway/HTML error page is still an error with a
                        // status code, not "the server said nothing".
                        if (response.ok) throw parseError
                    }

                    if (!response.ok || data.error) {
                        const error = new Error(data.error || `Ошибка отправки (${response.status})`)
                        error.code = data.code || null
                        throw error
                    }

                    for (const att of readyAtts) {
                        att.state = 'processed'
                    }
                    this.attachments = []

                    // Replace with full transcript from server (includes user msg + assistant replies)
                    this.messages = this.visibleMessages(data.messages || [])
                    this.awaitingConfirmation = Boolean(data.awaiting_confirmation);
                    if (data.warning) this.notice = data.warning
                    else if (data.awaiting_confirmation) {
                        // A staged confirmation is a question, not a missing
                        // answer: the server keeps it in the transcript now, so
                        // the "no text" notice must not contradict it.
                        this.notice = ''
                    }
                    else if (this.messages.at(-1)?.role !== 'assistant') this.notice = 'Ответ без текста. Проверьте результат действия или уточните запрос.'
                    this.$nextTick(() => this.scrollToBottom())
                } catch (err) {
                    if (err.name === 'AbortError') {
                        this.notice = 'Генерация остановлена.'
                        this.loadTranscript().catch(() => { })
                        return
                    }
                    console.error('Chat send error:', err)
                    // Keep the user's message visible and mark it as not sent:
                    // removing it made a refused turn look like the text was
                    // never typed, which is exactly how a spent daily limit
                    // looked like a broken form.
                    userMsg.failed = true
                    textarea.value = rawText
                    this.notice = (err.code === 'daily_limit_reached' ? 'Лимит: ' : '') +
                        (err.message || 'Не удалось отправить сообщение.')
                    this.$nextTick(() => this.scrollToBottom())
                } finally {
                    this.pending = false
                    this.sending = false
                    this.abortController = null
                    this.stopThinking()
                    if (this.workspaceTaskId) this.refreshWorkspaceTask();
                }
            },

            async clearHistory() {
                if (this.pending) return
                this.pending = true
                try {
                    const response = await fetch('/app/chat', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/x-www-form-urlencoded',
                            'X-Requested-With': 'XMLHttpRequest',
                            'Accept': 'application/json, text/plain, */*',
                        },
                        body: new URLSearchParams({
                            '_csrf': this.csrf,
                            'text': '/stop',
                        }),
                    })
                    const data = await response.json()
                    if (!response.ok || data.error) throw new Error(data.error)
                    this.messages = this.visibleMessages(data.messages || [])
                    this.awaitingConfirmation = Boolean(data.awaiting_confirmation);
                    this.attachments = [];
                    this.pending = false;
                    this.clearWorkspaceTask();
                    if (data.warning) this.notice = data.warning
                    this.$nextTick(() => this.scrollToBottom())
                } catch (err) {
                    console.error('Clear history error:', err)
                    this.notice = 'Не удалось очистить историю: ' + (err.message || '')
                } finally {
                    this.pending = false
                }
            },

            async deleteMessage(messageId, confirmMsg = 'Удалить сообщение?') {
                if (this.pending) return
                this.pending = true
                try {
                    const response = await fetch(`/app/chat/message/${messageId}/delete`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/x-www-form-urlencoded',
                            'X-Requested-With': 'XMLHttpRequest',
                            'Accept': 'application/json, text/plain, */*',
                        },
                        body: new URLSearchParams({
                            '_csrf': this.csrf,
                        }),
                    })
                    const data = await response.json()
                    if (!response.ok || data.error) throw new Error(data.error)
                    // Reload transcript
                    await this.loadTranscript()
                } catch (err) {
                    console.error('Delete message error:', err)
                    this.notice = 'Не удалось удалить сообщение: ' + (err.message || '')
                } finally { this.pending = false }
            },

            scrollContainer() {
                // The whole page scrolls now: .shell-content is the scroller,
                // window/document is the fallback.
                return document.querySelector('.shell-content') || document.scrollingElement || document.documentElement;
            },

            scrollToBottom() {
                const scroller = this.scrollContainer();
                const target = this.$refs.form || this.$refs.end;
                if (target && target.scrollIntoView) {
                    target.scrollIntoView({ behavior: "smooth", block: "end" });
                    return;
                }
                if (scroller) {
                    const top = scroller === document.scrollingElement || scroller === document.documentElement
                        ? document.documentElement.scrollHeight
                        : scroller.scrollHeight;
                    scroller.scrollTo({ top, behavior: "smooth" });
                }
            },

            onScroll() {
                const scroller = this._scroller || this.scrollContainer();
                const form = this.$refs.form;
                if (!scroller || !form) return;
                const isDoc = scroller === document.scrollingElement || scroller === document.documentElement;
                const bounds = isDoc ? {top: 0, bottom: window.innerHeight} : scroller.getBoundingClientRect();
                const rect = form.getBoundingClientRect();
                // Only offer the shortcut after the entire composer is below view.
                // A tall context panel must not make it appear beside a visible input.
                this.autoScroll = rect.top < bounds.bottom + 80;
                const chat = this.$refs.messages?.getBoundingClientRect();
                if (chat && this.$refs.scrollButton) {
                    this.$refs.scrollButton.style.left = (chat.left + chat.width / 2) + 'px';
                }
            },

            copyText(text, event) {
                navigator.clipboard.writeText(text).then(() => {
                    // Brief visual feedback
                    const btn = event.target.closest("button")
                    if (btn) {
                        const orig = btn.innerHTML
                        btn.innerHTML = "<svg xmlns=\"http://www.w3.org/2000/svg\" class=\"h-3.5 w-3.5 text-emerald-400\" fill=\"none\" viewBox=\"0 0 24 24\" stroke-width=\"2\" stroke=\"currentColor\"><path stroke-linecap=\"round\" stroke-linejoin=\"round\" d=\"m4.5 12.75 6 6 9-13.5\"/></svg>"
                        setTimeout(() => {
                            btn.innerHTML = orig
                        }, 1500)
                    }
                }).catch(() => { this.notice = 'Не удалось скопировать. Выделите текст сообщения вручную.' })
            },
        }
    }
