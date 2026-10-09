function chatApp() {
        return {
            pending: false,
            notice: '',
            confirmation: null,
            autoScroll: true,
            messages: [],
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
                // Initialize messages from server-rendered data
                this.messages = this.visibleMessages(window.__chatMessages || [])
                // Scroll to bottom on page load
                this.$nextTick(() => this.scrollToBottom())
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
            suggest(text) { this.$refs.input.value = text; this.$refs.input.focus(); this.resizeInput(); },
            resizeInput() { const el = this.$refs.input; el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 180) + 'px'; },
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

            async sendMessage() {
                const textarea = this.$refs.input
                const text = textarea.value.trim()
                if (!text || this.pending) return
                this.notice = ''

                this.pending = true

                // Optimistic: show user message immediately
                const userMsg = {
                    id: 'pending-' + Date.now(),
                    role: 'user',
                    content: text,
                    created_at: new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }),
                }
                this.messages.push(userMsg)
                textarea.value = ''
                textarea.style.height = 'auto'

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
                    })

                    const data = await response.json()

                    if (!response.ok || data.error) {
                        throw new Error(data.error || 'Ошибка отправки')
                    }

                    // Replace with full transcript from server (includes user msg + assistant replies)
                    this.messages = this.visibleMessages(data.messages || [])
                    if (data.warning) this.notice = data.warning
                    else if (this.messages.at(-1)?.role !== 'assistant') this.notice = 'Ответ без текста. Проверьте результат действия или уточните запрос.'
                    this.$nextTick(() => this.scrollToBottom())
                } catch (err) {
                    console.error('Chat send error:', err)
                    // Remove the optimistic user message and show error
                    this.messages = this.messages.filter(m => m !== userMsg)
                    textarea.value = text
                    this.notice = err.message || 'Не удалось отправить сообщение. Текст сохранён, попробуйте ещё раз.'
                    this.$nextTick(() => this.scrollToBottom())
                } finally {
                    this.pending = false
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
                    const refresh = await fetch('/app/chat', {
                        headers: {
                            'X-Requested-With': 'XMLHttpRequest',
                            'Accept': 'application/json, text/plain, */*',
                        },
                    })
                    const refreshData = await refresh.json()
                    if (!refresh.ok) throw new Error(refreshData.error || 'Не удалось обновить диалог')
                    this.messages = this.visibleMessages(refreshData.messages || [])
                    this.$nextTick(() => this.scrollToBottom())
                } catch (err) {
                    console.error('Delete message error:', err)
                    this.notice = 'Не удалось удалить сообщение: ' + (err.message || '')
                } finally { this.pending = false }
            },

            scrollToBottom() {
                const el = this.$refs.messages
                if (el) {
                    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" })
                }
            },

            onScroll() {
                const el = this.$refs.messages
                if (!el) return
                // Disable auto-scroll if user scrolled up more than 100px from bottom
                this.autoScroll = el.scrollHeight - el.scrollTop - el.clientHeight < 100
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
