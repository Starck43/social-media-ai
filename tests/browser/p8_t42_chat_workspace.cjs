// Render-only responsive and interaction checks for the real chat template.
// The browser fixture uses mocked fetch responses; it never starts the app/DB.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { pathToFileURL } = require('node:url');
const { createRequire } = require('node:module');

async function main() {
const root = path.resolve(__dirname, '../..');
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'p8-t42-chat-'));
const fixtureOnlyIndex = process.argv.indexOf('--fixture-only');
const fixtureOnlyPath = fixtureOnlyIndex >= 0 ? process.argv[fixtureOnlyIndex + 1] : null;
if (fixtureOnlyIndex >= 0 && !fixtureOnlyPath) throw Error('Expected output path after --fixture-only');
const fixture = fixtureOnlyPath ? path.resolve(fixtureOnlyPath) : path.join(scratch, 'chat.html');
const requireFromProject = createRequire(path.join(root, 'package.json'));
const render = String.raw`
from types import SimpleNamespace
from app.web.deps import templates
from app.web.nav import NAV_ITEMS
context = {
    "user": SimpleNamespace(username="owner", role=None, has_admin_access=lambda: False),
    "tenant": SimpleNamespace(id=1, name="Fixture workspace"),
    "workspaces": [], "memberships": [], "unread_notifications": 0,
    "connection_alerts": [], "perms": None, "member_level": None,
    "nav": NAV_ITEMS, "mobile_nav": NAV_ITEMS, "csrf": "fixture",
    "flashes": [], "path": "/app/chat", "section": "chat",
    "plan_labels": {}, "_tz": "UTC",
    "messages": [], "is_owner": True, "awaiting_confirmation": False,
    "request": SimpleNamespace(query_params={"embedded": "1"}),
    "url_for": lambda name, **kwargs: "/static/" + kwargs["path"],
    "static_v": lambda path: "/static/" + path,
}
print(templates.env.get_template("web/chat.html").render(**context))
`;

try {
    const py = spawnSync(process.env.PYTHON || 'python', ['-c', render], { cwd: root, encoding: 'utf8', timeout: 30000 });
    if (py.error) throw py.error;
    if (py.status !== 0) throw Error(py.stderr || 'Jinja chat template render failed');

    let html = py.stdout;
    html = html.replace(/<script\b[\s\S]*?<\/script>/gi, '');
    html = html.replace(/<link\b[^>]*rel="stylesheet"[^>]*>/gi, '');
    const css = ['theme.css', 'ui.css', 'shell.css', 'chat.css']
        .map(name => fs.readFileSync(path.join(root, 'app/static/css', name), 'utf8'))
        .join('\n');
    const js = fs.readFileSync(path.join(root, 'app/static/js/chat.js'), 'utf8');
    let alpinePath = process.env.ALPINE_JS;
    if (!alpinePath) {
        try { alpinePath = requireFromProject.resolve('alpinejs/dist/cdn.min.js'); } catch (_error) {}
    }
    if (!alpinePath && fs.existsSync(path.join(os.tmpdir(), 'alpinejs-3.14.1-cdn.min.js'))) {
        alpinePath = path.join(os.tmpdir(), 'alpinejs-3.14.1-cdn.min.js');
    }
    if (alpinePath && !fs.existsSync(alpinePath)) throw Error(`ALPINE_JS does not exist: ${alpinePath}`);
    if (!alpinePath) {
        const alpine = await fetch('https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js');
        if (!alpine.ok) throw Error(`Unable to fetch pinned Alpine.js fixture (${alpine.status})`);
        alpinePath = path.join(scratch, 'alpine.min.js');
        fs.writeFileSync(alpinePath, Buffer.from(await alpine.arrayBuffer()));
    }
    const alpineJs = fs.readFileSync(alpinePath, 'utf8');
    const browserChecks = String.raw`
        const root = document.querySelector('.chat-page');
        const panel = document.querySelector('#chat-workspace-panel');
        let app = null;
        let escapeAssertion = null;
        let submitted = null;
        const expressionErrors = [];
        const toggleTrace = [];
        window.addEventListener('error', event => {
          expressionErrors.push({type: 'error', message: event.message || 'window error', stack: event.error?.stack || ''});
        });
        window.addEventListener('unhandledrejection', event => {
          expressionErrors.push({type: 'unhandledrejection', message: String(event.reason?.message || event.reason)});
        });
        const diagnostic = () => {
        const toggle = root?.querySelector('.chat-panel-tab');
          const rect = toggle?.getBoundingClientRect();
          return {
            documentHasFocus: document.hasFocus(),
            expressionErrors: expressionErrors.slice(-5),
            toggleTrace: toggleTrace.slice(-4),
            panelOpen: app?.panelOpen ?? null,
            chatPending: app?.pending ?? null,
            chatSending: app?.sending ?? null,
            selectedTaskId: app?.workspaceTaskId ?? null,
            taskLoading: app?.workspaceTaskLoading ?? null,
            taskError: app?.workspaceTaskError ?? '',
            awaitingConfirmation: app?.awaitingConfirmation ?? null,
            notice: app?.notice || '',
            submitted: submitted ? Object.fromEntries(submitted.entries()) : null,
            chatSend: root?.querySelector('.chat-send') ? {
              disabled: root.querySelector('.chat-send').disabled,
              inputLength: root.querySelector('.chat-input')?.value.length ?? null,
            } : null,
            panelInlineDisplay: panel?.style.display || '',
            panelDisplay: panel ? getComputedStyle(panel).display : '',
            expanded: toggle?.getAttribute('aria-expanded') || '',
            activeElement: document.activeElement ? {tag: document.activeElement.tagName, id: document.activeElement.id, label: document.activeElement.getAttribute('aria-label')} : null,
            panelToggle: toggle ? {
              tag: toggle.tagName,
              tabindex: toggle.tabIndex,
              disabled: toggle.disabled,
              visible: Boolean(rect && rect.width > 0 && rect.height > 0),
              visibility: getComputedStyle(toggle).visibility,
              display: getComputedStyle(toggle).display,
              isAlpineRef: Boolean(app?.$refs && app.$refs.panelToggle === toggle),
            } : null,
            scrollGeometry: root?.querySelector('.chat-scroll-bottom') ? (() => {
              const buttonRect = root.querySelector('.chat-scroll-bottom').getBoundingClientRect();
              const chatRect = root.querySelector('.chat-messages').getBoundingClientRect();
              return {buttonCenter: buttonRect.left + buttonRect.width / 2, buttonLeft: buttonRect.left,
                inlineLeft: root.querySelector('.chat-scroll-bottom').style.left,
                chatCenter: chatRect.left + chatRect.width / 2, chatLeft: chatRect.left, chatWidth: chatRect.width,
                buttonTransform: getComputedStyle(root.querySelector('.chat-scroll-bottom')).transform};
            })() : null,
          };
        };
      (async () => {
        const checks = [];
        const check = (value, label) => { if (!value) throw Error(label + ' diagnostic=' + JSON.stringify(diagnostic())); checks.push(label); };
        const waitFor = async (predicate, label, timeout = 1500) => {
          const deadline = Date.now() + timeout;
          while (Date.now() < deadline) {
            if (predicate()) return true;
            await new Promise(resolve => setTimeout(resolve, 10));
          }
          throw Error('Timed out waiting for ' + label + ' diagnostic=' + JSON.stringify(diagnostic()));
        };
        check(root && panel, 'real chat template includes the task workspace');
        check(panel.querySelector('#chat-task-search'), 'task picker has a labelled search input');
        check(panel.textContent.includes('Быстрые действия'), 'persistent quick action section renders');
        check(!root.querySelector('nav[aria-label="Управление данными и задачами"]'), 'chat footer does not duplicate the persistent shell navigation');
        window.marked = {parse: text => text};
        check(panel.querySelector('[aria-labelledby="chat-selected-task-title"]'), 'selected task summary region renders');
        check(document.querySelector('.chat-attach'), 'attachment affordance remains visible');
        check(root.querySelector('.chat-panel-tab')?.getAttribute('aria-controls') === 'chat-workspace-panel', 'panel tab is associated accessibly');

        let failNextSnapshot = false;
        let releaseStaleSnapshot;
        let staleResponseParsed = false;
        window.fetch = async (url, options = {}) => {
          if (url.startsWith('/app/chat/workspace/tasks?')) {
            return {ok: true, json: async () => ({tasks: [{id: 57, name: 'Fixture task'}, {id: 58, name: 'Other task'}], next_offset: null})};
          }
          if (url === '/app/chat/workspace/tasks/58') {
            return new Promise(resolve => { releaseStaleSnapshot = resolve; });
          }
          if (url === '/app/chat/workspace/tasks/57' && failNextSnapshot) {
            failNextSnapshot = false;
            return {ok: false, status: 503, json: async () => ({error: 'hidden detail'})};
          }
          if (url === '/app/chat/workspace/tasks/57') {
            return {ok: true, json: async () => ({
              id: 57, name: 'Fixture task', task_url: '/app/tasks/57', plan_status: 'available',
              plan: {goal: 'Fixture goal', platforms: ['vk'], source_scope: 'selected', source_count: 2, stages: [],
                window: {type: 'rolling', days: 7}, trigger: {type: 'once'}, limit: 40},
              latest: {status: 'done', coverage: 'partial_or_bounded', progress: {phase: 'done', total: 2, completed: 2},
                analyses: [{title: 'Fixture analysis', url: '/app/analytics/9'}]},
            })};
          }
          if (url === '/app/chat' && options.method === 'POST') {
            submitted = new URLSearchParams(options.body);
            return {ok: true, json: async () => ({
              messages: [{id: 1, role: 'assistant', content: 'Done'}],
              awaiting_confirmation: submitted.get('text') === 'да',
            })};
          }
          throw Error('Unexpected fixture request: ' + url);
        };
        await waitFor(() => window.Alpine && typeof Alpine.$data(root)?.toggleWorkspacePanel === 'function', 'Alpine component initialization');
        app = Alpine.$data(root);
        check(Alpine && app, 'Alpine initializes the real workspace component');
        const panelReloadPhase = sessionStorage.getItem('__p8t42-panel-reload');
        if (panelReloadPhase !== 'reset' && panelReloadPhase !== 'closed') {
          sessionStorage.removeItem('chat.workspace.panel');
          sessionStorage.removeItem('__p8t42-default-open');
          sessionStorage.setItem('__p8t42-panel-reload', 'reset');
          location.reload();
          return;
        }
        if (panelReloadPhase === 'reset') {
          await waitFor(() => app.panelOpen, 'panel defaults open');
          check(app.panelOpen, 'workspace panel defaults open');
          sessionStorage.setItem('__p8t42-default-open', 'yes');
          root.querySelector('.chat-panel-close').click();
          await waitFor(() => !app.panelOpen && sessionStorage.getItem('chat.workspace.panel') === 'closed', 'panel collapse persists');
          sessionStorage.setItem('__p8t42-panel-reload', 'closed');
          location.reload();
          return;
        }
        sessionStorage.removeItem('__p8t42-panel-reload');
        await waitFor(() => !app.panelOpen, 'closed panel state restored after reload');
        check(sessionStorage.getItem('__p8t42-default-open') === 'yes', 'default-open state was observed before the persistence reload');
        sessionStorage.removeItem('__p8t42-default-open');
        check(!app.panelOpen && sessionStorage.getItem('chat.workspace.panel') === 'closed', 'panel state restores from this browser session');
        root.querySelector('.chat-panel-tab').click();
        await waitFor(() => app.panelOpen && sessionStorage.getItem('chat.workspace.panel') === 'open' &&
          document.activeElement === panel, 'panel expansion persists and receives focus');
        check(app.panelOpen && sessionStorage.getItem('chat.workspace.panel') === 'open', 'expanding panel updates saved session state');
        const originalToggleWorkspacePanel = app.toggleWorkspacePanel;
        app.toggleWorkspacePanel = function(...args) {
          const call = {
            before: this.panelOpen,
            args: args.map(value => value?.target?.tagName || value?.tagName || typeof value),
            argElementTag: args.find(value => value?.target?.tagName || value?.tagName)?.target?.tagName ||
              args.find(value => value?.tagName)?.tagName || document.activeElement?.tagName || null,
            rootClass: this.$root?.className || '',
            refsKeys: Object.keys(this.$refs || {}),
          };
          toggleTrace.push(call);
          try {
            const result = originalToggleWorkspacePanel.apply(this, args);
            call.after = this.panelOpen;
            call.rootClassAfter = this.$root?.className || '';
            return result;
          } catch (error) {
            call.error = {message: error.message, stack: error.stack || ''};
            throw error;
          }
        };
        const panelClose = panel.querySelector('.chat-panel-close');
        check(getComputedStyle(panelClose).backgroundColor !== getComputedStyle(panel).backgroundColor, 'panel close uses the darker secondary surface');
        const closeGlyph = document.createRange();
        closeGlyph.selectNodeContents(panelClose);
        const glyphRect = closeGlyph.getBoundingClientRect();
        const closeRect = panelClose.getBoundingClientRect();
        check(Math.abs((glyphRect.left + glyphRect.width / 2) - (closeRect.left + closeRect.width / 2)) <= 2,
          'panel close icon is centered inside its button');
        if (!app.panelOpen) root.querySelector('.chat-panel-tab').click();
        await waitFor(() => app.panelOpen && getComputedStyle(panel).display !== 'none' &&
          document.activeElement === panel, 'panel opening before Escape');
        panel.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
        await waitFor(() => !app.panelOpen && getComputedStyle(panel).display === 'none' &&
          document.activeElement === root.querySelector('.chat-panel-tab'), 'Escape closes the panel and restores focus');
        check(!app.panelOpen && getComputedStyle(panel).display === 'none' &&
          document.activeElement === root.querySelector('.chat-panel-tab'), 'Escape closes the workspace panel and focuses its collapsed tab');
        root.querySelector('.chat-panel-tab').click();
        await waitFor(() => app.panelOpen && getComputedStyle(panel).display !== 'none' &&
          document.activeElement === panel, 'panel visibility after opening');
        check(getComputedStyle(panel).display !== 'none', 'opening the panel updates the real Alpine state');
        panel.querySelector('.chat-panel-close').click();
        let closeFocusError = null;
        try {
          await waitFor(() => !app.panelOpen && getComputedStyle(panel).display === 'none' &&
            document.activeElement === root.querySelector('.chat-panel-tab'), 'panel close restores focus to the collapsed tab');
        } catch (error) { closeFocusError = error.message; }
        const closeFocusPassed = getComputedStyle(panel).display === 'none' &&
          document.activeElement === root.querySelector('.chat-panel-tab');
        escapeAssertion = {passed: closeFocusPassed, beforeProbe: diagnostic()};
        check(closeFocusPassed, closeFocusError || 'panel close restores focus to the collapsed tab');
        root.querySelector('.chat-panel-tab').click();
        await waitFor(() => app.panelOpen && getComputedStyle(panel).display !== 'none' &&
          document.activeElement === panel, 'panel opens after focus restore');

        const input = panel.closest('.chat-page').querySelector('.chat-input');
        const sampleBubble = document.createElement('div');
        sampleBubble.className = 'chat-bubble-user';
        root.append(sampleBubble);
        check(getComputedStyle(sampleBubble).backgroundColor === getComputedStyle(root.querySelector('.chat-send')).backgroundColor,
          'user message bubble uses the send button brand color');
        sampleBubble.remove();
        const composer = input.closest('.chat-composer');
        const borderBeforeFocus = getComputedStyle(composer).borderTopColor;
        input.focus();
        const inputStyle = getComputedStyle(input);
        check(inputStyle.outlineStyle === 'none' && inputStyle.borderTopStyle === 'none', 'textarea has no independent focus frame');
        await waitFor(() => getComputedStyle(composer).borderTopColor !== borderBeforeFocus, 'composer parent focus border transition');
        check(getComputedStyle(composer).borderTopColor !== borderBeforeFocus, 'composer parent carries the focus border');
        input.blur();
        input.value = 'Existing draft';
        [...panel.querySelectorAll('.chat-panel-actions button')].find(button => button.textContent.trim() === 'Подготовить задачу').click();
        await waitFor(() => input.value.startsWith('Existing draft\n\n'), 'prepared request appends to existing draft');
        check(input.value.startsWith('Existing draft\n\n'), 'quick action appends without replacing the draft');
        const appended = input.value;
        [...panel.querySelectorAll('.chat-panel-actions button')].find(button => button.textContent.trim() === 'Что дальше?').click();
        await waitFor(() => input.value.length > appended.length, 'next-step action prepares a draft');
        check(input.value.startsWith(appended + '\n\n') && input.value.includes('Предложи следующий шаг'), 'context action adds an actionable next-step draft');

        const search = panel.querySelector('#chat-task-search');
        search.value = 'Fixture';
        search.dispatchEvent(new Event('input', {bubbles: true}));
        await waitFor(() => [...panel.querySelectorAll('.chat-task-picker-list button')]
          .some(button => button.textContent === 'Fixture task'), 'searched tasks render');
        const taskButton = [...panel.querySelectorAll('.chat-task-picker-list button')].find(button => button.textContent === 'Fixture task');
        check(taskButton, 'search results render through the real task picker');
        taskButton.click();
        await waitFor(() => panel.textContent.includes('Fixture goal') && panel.textContent.includes('Fixture analysis'), 'selected task summary renders');
        check(panel.textContent.includes('Fixture goal') && panel.textContent.includes('Fixture analysis'), 'selected plan and latest result render');
        check(document.querySelector('.chat-context-label').textContent.includes('Fixture task'), 'selected task is visible beside chat');

        input.value = 'Ask about selected task';
        const chatSend = document.querySelector('.chat-send');
        await waitFor(() => !chatSend.disabled && !app.pending && !app.workspaceTaskLoading &&
          !app.workspaceTaskError && app.workspaceTask?.id === 57, 'send becomes available for the loaded selected task');
        chatSend.click();
        await waitFor(() => submitted?.get('task_id') === '57' && !app.pending, 'selected-task request completes');
        check(submitted?.get('task_id') === '57', 'real chat send includes the explicit selected task ID');
        check(submitted?.get('text') === 'Ask about selected task', 'real chat send preserves the typed request');

        app.awaitingConfirmation = true;
        app.workspaceTaskId = 57;
        app.workspaceTaskLoading = true;
        app.workspaceTaskError = 'temporary refresh error';
        input.value = 'да';
        await waitFor(() => !chatSend.disabled && app.awaitingConfirmation, 'confirmation remains sendable during task refresh');
        chatSend.click();
        await waitFor(() => submitted?.get('text') === 'да' && !app.pending, 'confirmation reply completes during task refresh');
        check(submitted?.get('text') === 'да', 'pending confirmation can be sent while task refresh is pending');
        check(submitted?.get('task_id') === '57', 'pending reply leaves authority to the existing server confirmation state');
        app.workspaceTaskLoading = false;
        app.workspaceTaskError = '';
        await waitFor(() => panel.querySelector('.chat-panel-actions button').disabled &&
          panel.querySelector('#chat-task-search').disabled, 'workspace controls reflect pending confirmation');
        check(panel.querySelector('.chat-panel-actions button').disabled, 'task actions disable while confirmation is pending');
        check(panel.querySelector('#chat-task-search').disabled, 'task picker disables while confirmation is pending');
        app.awaitingConfirmation = false;
        await waitFor(() => !panel.querySelector('#chat-task-search').disabled, 'task picker re-enables after confirmation');

        const clearSelection = document.querySelector('.chat-context-label button');
        clearSelection.click();
        await waitFor(() => app.workspaceTaskId === null && !app.workspaceTask, 'selected task clears');
        check(app.workspaceTaskId === null && !app.workspaceTask, 'real clear control removes the selected context');

        const buttons = [...panel.querySelectorAll('.chat-task-picker-list button')];
        buttons.find(button => button.textContent === 'Other task').click();
        await waitFor(() => app.workspaceTaskId === 58 && releaseStaleSnapshot, 'second task read begins');
        document.querySelector('.chat-context-label button').click();
        releaseStaleSnapshot({ok: true, json: async () => { staleResponseParsed = true; return {id: 58, name: 'Stale result'}; }});
        await waitFor(() => staleResponseParsed && !app.workspaceTask && app.workspaceTaskId === null, 'late task response cannot restore cleared selection');
        check(!app.workspaceTask && app.workspaceTaskId === null, 'late response cannot restore a cleared task');

        buttons.find(button => button.textContent === 'Fixture task').click();
        await waitFor(() => app.workspaceTask?.name === 'Fixture task', 'task selection reloads');
        failNextSnapshot = true;
        [...panel.querySelectorAll('button')].find(button => button.textContent.trim() === 'Обновить').click();
        await waitFor(() => Boolean(app.workspaceTaskError), 'refresh error state renders');
        check(!app.workspaceTask && panel.textContent.includes('Не удалось обновить задачу'), 'refresh failure hides stale snapshot and explains recovery');

        const draftBeforeModal = 'Черновик остаётся в чате';
        input.value = draftBeforeModal;
        const sourceAction = [...panel.querySelectorAll('.chat-panel-actions button')]
          .find(button => button.textContent.includes('Источники для задачи'));
        check(sourceAction, 'workspace exposes settings shortcut without duplicating shell navigation');
        input.focus();
        sourceAction.click();
        const modal = root.querySelector('.chat-content-dialog');
        const frame = modal.querySelector('iframe');
        await waitFor(() => modal.open && frame.contentDocument?.body.textContent.includes('Mock source settings'), 'settings page opens inside the chat modal');
        check(frame.contentWindow.location.pathname.startsWith('/app/sources') &&
          new URL(frame.src).searchParams.get('embedded') === '1', 'same-origin management page opens in the embedded modal');
        modal.querySelector('[aria-label="Закрыть окно"]').click();
        await waitFor(() => !modal.open && document.activeElement === sourceAction, 'modal close restores launcher focus');
        check(input.value === draftBeforeModal && document.activeElement === sourceAction, 'modal close preserves draft and restores focus');
        sourceAction.click();
        await waitFor(() => modal.open && frame.contentDocument?.body.textContent.includes('Mock source settings'), 'settings modal can reopen');
        frame.contentDocument.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
        await waitFor(() => !modal.open && document.activeElement === sourceAction, 'Escape in embedded page closes modal and restores focus');
        check(!modal.open && input.value === draftBeforeModal && document.activeElement === sourceAction, 'embedded Escape preserves chat draft and returns focus');
        app.openWorkspacePage('//evil.invalid/page', 'External page', sourceAction);
        app.openWorkspacePage('/app/chat', 'Chat page', sourceAction);
        check(!modal.open, 'modal rejects external origins and nested chat routes');

        const attachmentButton = root.querySelector('.chat-attach');
        attachmentButton.click();
        const attachmentDialog = root.querySelector('.chat-attachment-dialog');
        check(attachmentDialog.open && attachmentDialog.textContent.includes('Прикрепление файлов пока недоступно'), 'paperclip explains current file-upload availability');
        attachmentDialog.querySelector('button[type="submit"]').click();
        await waitFor(() => !attachmentDialog.open, 'attachment information dialog closes');

        const scrollButton = root.querySelector('.chat-scroll-bottom');
        const spacer = document.createElement('div');
        spacer.dataset.testSpacer = 'true';
        spacer.style.height = '1400px';
        composer.before(spacer);
        const scroller = app.scrollContainer();
        scroller.scrollTop = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
        scroller.dispatchEvent(new Event('scroll'));
        app.onScroll();
        const visibleBounds = scroller === document.scrollingElement || scroller === document.documentElement
          ? {top: 0, bottom: innerHeight} : scroller.getBoundingClientRect();
        check(composer.getBoundingClientRect().bottom <= visibleBounds.bottom + 1, 'composer is fully visible at the bottom of the conversation');
        await waitFor(() => !scrollButton.getClientRects().length, 'scroll shortcut remains hidden while composer is in view');
        check(!scrollButton.getClientRects().length, 'scroll shortcut stays hidden while composer is visible');
        scroller.scrollTop = 0;
        scroller.dispatchEvent(new Event('scroll'));
        app.onScroll();
        const awayRect = composer.getBoundingClientRect();
        check(awayRect.top >= visibleBounds.bottom + 80, 'test scroll places composer well below the viewport');
        await waitFor(() => scrollButton.getClientRects().length > 0, 'scroll shortcut appears after scrolling away from composer');
        const centerBefore = scrollButton.getBoundingClientRect().left + scrollButton.getBoundingClientRect().width / 2;
        await new Promise(resolve => setTimeout(resolve, 80));
        const rectAfter = scrollButton.getBoundingClientRect();
        const centerAfter = rectAfter.left + rectAfter.width / 2;
        check(Math.abs(centerAfter - centerBefore) <= 1, 'scroll shortcut stays horizontally centered while appearing');
        const chatRect = root.querySelector('.chat-messages').getBoundingClientRect();
        check(Math.abs(centerAfter - (chatRect.left + chatRect.width / 2)) <= 2, 'scroll shortcut tracks the visible chat center');
        spacer.remove();

        if (!app.panelOpen) root.querySelector('.chat-panel-tab').click();
        await waitFor(() => app.panelOpen && getComputedStyle(panel).display !== 'none' &&
          (innerWidth >= 1200
            ? getComputedStyle(document.querySelector('.chat-workspace-grid')).gridTemplateColumns.split(' ').length === 2
            : getComputedStyle(document.querySelector('.chat-workspace-grid')).gridTemplateColumns.split(' ').length === 1),
        'workspace layout settles at measured viewport');
        const columns = getComputedStyle(document.querySelector('.chat-workspace-grid')).gridTemplateColumns.split(' ').length;
        if (innerWidth >= 1200) check(columns === 2, 'desktop panel shares the chat workspace in two columns');
        else check(columns === 1, 'mobile panel stacks without a horizontal split');
        check(document.documentElement.scrollWidth <= innerWidth + 1, 'workspace does not cause horizontal page overflow');
        if (expressionErrors.length) {
          throw Error('Browser recorded window errors: ' + JSON.stringify(expressionErrors.slice(-5)));
        }

        const result = document.createElement('pre');
        result.id = 'p8t42-results'; result.textContent = 'PASS ' + checks.length + ' checks';
        document.body.append(result);
      })().catch(async error => {
        const result = document.createElement('pre');
        result.id = 'p8t42-results';
        const failure = 'FAIL ' + error.message;
        result.textContent = failure + ' focus-probes=pending';
        document.body.append(result);
        let failureProbes = null;
        if (escapeAssertion && !escapeAssertion.passed) {
          const toggle = document.querySelector('.chat-panel-tab');
          const input = document.querySelector('.chat-input');
          const focused = () => document.activeElement === toggle;
          failureProbes = {originalAssertion: escapeAssertion, rootRefKeys: Object.keys(app?.$refs || {})};
          try {
            toggle.focus();
            failureProbes.vanillaFocus = {focused: focused(), active: document.activeElement?.tagName + '#' + (document.activeElement?.id || '')};
            toggle.setAttribute('tabindex', '0');
            toggle.focus();
            failureProbes.explicitTabindexFocus = {focused: focused(), active: document.activeElement?.tagName + '#' + (document.activeElement?.id || '')};
            input.focus();
            failureProbes.inputFocus = {focused: document.activeElement === input, active: document.activeElement?.tagName + '#' + (document.activeElement?.id || '')};
            await Promise.race([
              new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))),
              new Promise(resolve => setTimeout(resolve, 200)),
            ]);
            toggle.focus();
            await Promise.race([
              new Promise(resolve => requestAnimationFrame(resolve)),
              new Promise(resolve => setTimeout(resolve, 200)),
            ]);
            failureProbes.toggleFocusNextFrame = {focused: focused(), active: document.activeElement?.tagName + '#' + (document.activeElement?.id || '')};
            failureProbes.afterProbe = diagnostic();
          } catch (probeError) {
            failureProbes.probeError = probeError.message;
          }
        }
        result.textContent = failure + (failureProbes ? ' focus-probes=' + JSON.stringify(failureProbes) : ' focus-probes=not-applicable');
      });
    `;
    new Function(browserChecks);
    html = html.replace('</head>', () => `<style>${css}</style></head>`);
    html = html.replace('</body>', () => `<script>${js}</script><script>${browserChecks}</script><script>${alpineJs}</script></body>`);
    if (!html.includes(alpineJs)) throw Error('Alpine.js fixture bytes changed during HTML insertion');
    fs.writeFileSync(fixture, html);
    if (fixtureOnlyPath) {
        console.log(`Fixture written: ${fixture}`);
        return;
    }

    const browserCandidates = [
        process.env.CHROMIUM_BIN,
        path.join(os.homedir(), 'Library/Caches/ms-playwright/chromium-1248/chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'),
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    ].filter(Boolean);
    const browser = browserCandidates.find(candidate => fs.existsSync(candidate)) || 'chromium';
    const profile = path.join(scratch, 'chrome-profile');
    fs.mkdirSync(profile);
    for (const width of [1280, 390]) {
        const run = spawnSync(browser, [
            '--headless', '--no-sandbox', '--disable-gpu', '--disable-network', '--virtual-time-budget=5000',
            `--user-data-dir=${profile}`, `--window-size=${width},900`, '--dump-dom', pathToFileURL(fixture).href,
        ], { encoding: 'utf8', timeout: 30000 });
        if (run.error) throw run.error;
        const dom = run.stdout || '';
        const result = dom.match(/<pre id="p8t42-results">([^<]+)<\/pre>/)?.[1];
        if (run.status !== 0 || !/^PASS \d+ checks$/.test(result || '') || /Alpine Expression Error|Uncaught/.test(run.stderr || '')) {
            throw Error(result || dom.match(/Uncaught[^<]*/)?.[0] || run.stderr || 'Browser checks failed');
        }
        console.log(`${result} at ${width}px`);
    }
} finally {
    fs.rmSync(scratch, { recursive: true, force: true });
}
}

main().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
