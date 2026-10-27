function chatApp() {
        return {
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
                // Initialize messages from server-rendered data
                this.messages = this.visibleMessages(window.__chatMessages || [])
                // Scroll to bottom on page load
                this.$nextTick(() => { this.scrollToBottom(); this.onScroll(); })
            },

            visibleMessages(items) {
                return items.filter(m => ['user', 'assistant'].includes(m.role) && typeof m.content === 'string' && m.content.trim());
            },
            requestConfirmation(kind, id = null) {
                if (this.pending) return;
                this.confirmation = { kind, id, label: kind === 'clear' ? 'Очистить историю диалога?' : 'Удалить сообщение?' };
            },
            performConfirmation() {
                const action = this.confirmation; this.confirmation = null;
                if (action?.kind === 'clear') this.clearHistory();
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
            attachFile() { this.notice = 'Прикрепление файлов пока не поддерживается.'; },
            stopGeneration() { if (this.abortController) this.abortController.abort(); },
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
                this.$nextTick(() => this.scrollToBottom())
            },

            async sendMessage(preset = '') {
                const textarea = this.$refs.input
                const text = (typeof preset === 'string' && preset ? preset : textarea.value).trim()
                if (!text || this.pending) return
                this.notice = ''

                this.pending = true
                this.sending = true
                this.abortController = new AbortController()
                this.startThinking()

                // Optimistic: show user message immediately
                const userMsg = {
                    id: 'pending-' + Date.now(),
                    role: 'user',
                    content: text,
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

                    // Replace with full transcript from server (includes user msg + assistant replies)
                    this.messages = this.visibleMessages(data.messages || [])
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
                    textarea.value = text
                    this.notice = (err.code === 'daily_limit_reached' ? 'Лимит: ' : '') +
                        (err.message || 'Не удалось отправить сообщение.')
                    this.$nextTick(() => this.scrollToBottom())
                } finally {
                    this.pending = false
                    this.sending = false
                    this.abortController = null
                    this.stopThinking()
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
                if (!scroller) return;
                const isDoc = scroller === document.scrollingElement || scroller === document.documentElement;
                // Primary: is there scrollable room below (>120px)? Works even with short chats.
                const distance = isDoc
                    ? scroller.scrollHeight - window.innerHeight - window.scrollY
                    : scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
                if (distance > 120) { this.autoScroll = false; return; }
                // Secondary: input form is below the visible viewport (long page, form out of view).
                const form = this.$refs.form;
                if (form) {
                    const rect = form.getBoundingClientRect();
                    const viewport = window.innerHeight || document.documentElement.clientHeight;
                    if (rect.top - viewport > 120) { this.autoScroll = false; return; }
                }
                this.autoScroll = true;
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
