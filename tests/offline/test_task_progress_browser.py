"""Offline Chromium checks on production component/templates; no app/DB/ASGI.

Run render_task_progress.py first. TASK_PROGRESS_FIXTURES overrides /tmp path.
External requests are aborted. Polling/HTMX events are simulated, not a backend.
"""
import os
from pathlib import Path
import unittest
import shutil
import pytest
sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

ROOT=Path(__file__).resolve().parents[2]
FIXTURES=Path(os.environ.get('TASK_PROGRESS_FIXTURES','/tmp/task-progress-fixtures'))

@unittest.skipUnless((FIXTURES/"analysis.html").is_file(), "Generate the explicit offline UI fixtures first")
class TaskProgressBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw=sync_playwright().start()
        cls.browser=cls.pw.chromium.launch(executable_path=os.environ.get('TASK_PROGRESS_CHROMIUM') or shutil.which('chromium'),headless=True,args=['--no-sandbox'])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.pw.stop()

    def setUp(self):
        self.context=self.browser.new_context(viewport={'width':1440,'height':1050},reduced_motion='reduce')
        self.external=[];self.errors=[]
        def route(request):
            if request.request.url.startswith(('http:','https:','ws:','wss:')):
                self.external.append(request.request.url);request.abort()
            else:request.continue_()
        self.context.route('**/*',route)
        self.page=self.context.new_page();self.page.on('pageerror',lambda error:self.errors.append(str(error)))

    def tearDown(self):
        self.assertEqual(self.errors,[]);self.assertEqual(self.external,[]);self.context.close()

    def open(self,state):
        self.page.goto((FIXTURES/f'{state}.html').as_uri());self.page.wait_for_timeout(60)

    def test_all_states_have_no_source_list_except_sidebar_at_both_widths(self):
        for width in (1440,390):
            self.page.set_viewport_size({'width':width,'height':1050})
            for state in ('pending','collection','analysis','partial','dry-run','success','empty','failed','unconfirmed'):
                with self.subTest(width=width,state=state):
                    self.open(state)
                    self.assertEqual(self.page.locator('#task-run-sources a').count(),3)
                    self.assertEqual(self.page.locator('#task-run-progress a[href^="/app/sources/"]').count(),0)
                    self.assertEqual(self.page.locator('[role=progressbar]').count(),2)
                    self.assertNotIn('Источники этого запуска',self.page.inner_text('body'))
                    self.assertNotIn('Дизайн-прототип',self.page.inner_text('body'))
                    self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                    self.assertEqual(self.page.locator('#task-run-progress').count(),1)

    def test_active_states_lock_all_mutations_and_resume_on_swap(self):
        for state in ('pending','collection','analysis','unconfirmed'):
            self.open(state)
            self.assertEqual(self.page.locator('[data-task-mutation][aria-disabled="true"]').count(),4)
            self.assertTrue(self.page.locator('[data-task-run] button').is_disabled())
            before=self.page.url
            self.page.locator('[data-task-editor-open]').click(force=True)
            self.assertEqual(self.page.url,before)
            self.assertFalse(self.page.locator('#task-editor-dialog').evaluate('node=>node.open'))
        success=(FIXTURES/'success.html').read_text()
        self.page.evaluate('html=>{const doc=new DOMParser().parseFromString(html,"text/html");document.querySelector("#task-run-progress").replaceWith(doc.querySelector("#task-run-progress"));document.dispatchEvent(new Event("htmx:afterSwap"));}',success)
        self.assertFalse(self.page.locator('[data-task-run] button').is_disabled())
        self.assertEqual(self.page.locator('[data-task-mutation][aria-disabled="true"]').count(),0)

    def test_double_submit_has_one_admitted_browser_form(self):
        self.open('success')
        value=self.page.evaluate('''async()=>{let count=0;const form=document.querySelector('[data-task-run]');form.addEventListener('submit',event=>{event.preventDefault();count++});form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));await Promise.resolve();return {count,disabled:form.querySelector('button').disabled}}''')
        self.assertEqual(value,{'count':1,'disabled':True})

    def test_pending_and_unconfirmed_are_not_running_animation(self):
        for state in ('pending','unconfirmed'):
            self.open(state)
            self.assertNotIn('task-run-status',self.page.locator('[data-task-run-label]').get_attribute('class') or '')
        self.assertIn('Проверяем статус',self.page.locator('[data-task-run-label]').inner_text())

    def test_error_empty_and_dry_run_notes_are_sidebar_only(self):
        self.open('dry-run')
        self.assertIn('Ошибка сбора',self.page.locator('#task-run-sources').inner_text())
        self.assertIn('нет данных',self.page.locator('#task-run-sources').inner_text())
        self.assertIn('api_1051',self.page.locator('#task-run-sources').inner_text())
        self.assertNotIn('SYNTHETIC_SECRET',self.page.inner_text('body'))
        self.assertIn('модель не вызывается',self.page.locator('#task-run-progress').inner_text())
        self.assertEqual(self.page.locator('[data-phase="analysis"] .run-records-fill').count(),0)
        self.assertEqual(self.page.locator('form[action$="retry-analysis"]').count(),0)

    def test_known_counts_and_partial_share_same_denominator(self):
        self.open('partial')
        self.assertEqual(self.page.locator('[data-phase="analysis"] [role=progressbar]').get_attribute('aria-valuenow'),'88')
        self.assertEqual(self.page.locator('.run-records-failure').count(),2)
        self.assertIn('88 / 100',self.page.locator('[data-phase="analysis"] .run-records-count').inner_text())
        boundaries=self.page.evaluate('''()=>[...document.querySelectorAll('.run-records-bar')].map(bar=>[...bar.children].map(segment=>Math.round(segment.getBoundingClientRect().width)))''')
        self.assertEqual(boundaries[0],boundaries[1])

    def test_unknown_collection_has_no_fabricated_percent(self):
        self.open('collection')
        self.assertIsNone(self.page.locator('[data-phase="collection"] [role=progressbar]').get_attribute('aria-valuenow'))
        self.assertIn('уточняется',self.page.locator('[data-phase="collection"] .run-records-count').inner_text())

    def test_compact_uses_same_macro_with_no_sources(self):
        for width in (1440,390):
            self.page.set_viewport_size({'width':width,'height':1050});self.open('compact')
            self.assertEqual(self.page.locator('.run-records--compact [role=progressbar]').count(),2)
            self.assertEqual(self.page.locator('a[href^="/app/sources/"]').count(),0)
            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
            self.assertEqual(self.page.locator('a[href="/app/tasks/2027"]').count(),1)

    def test_failed_detail_poll_preserves_busy_and_last_counters(self):
        self.open('analysis')
        self.page.evaluate("document.dispatchEvent(new CustomEvent('htmx:sendError',{detail:{elt:document.querySelector('#task-run-progress')}}))")
        self.assertTrue(self.page.locator('[data-task-run] button').is_disabled())
        self.assertIn('Проверяем статус',self.page.locator('[data-task-run-label]').inner_text())
        self.assertEqual(self.page.locator('[data-progress-connection-note]').count(),1)
        self.assertIn('62 / 100',self.page.locator('[data-phase="analysis"] .run-records-count').inner_text())

    def test_failed_poll_after_terminal_snapshot_locks_until_successful_swap(self):
        self.open('success')
        self.assertFalse(self.page.locator('[data-task-run] button').is_disabled())
        self.page.evaluate("document.dispatchEvent(new CustomEvent('htmx:sendError',{detail:{elt:document.querySelector('#task-run-progress')}}))")
        panel=self.page.locator('#task-run-progress')
        self.assertEqual(panel.get_attribute('data-task-busy'),'true')
        self.assertEqual(panel.get_attribute('data-task-status'),'unconfirmed')
        self.assertTrue(self.page.locator('[data-task-run] button').is_disabled())
        self.assertEqual(self.page.locator('[data-progress-connection-note]').count(),1)
        success=(FIXTURES/'success.html').read_text()
        self.page.evaluate('html=>{const doc=new DOMParser().parseFromString(html,"text/html");document.querySelector("#task-run-progress").replaceWith(doc.querySelector("#task-run-progress"));document.dispatchEvent(new Event("htmx:afterSwap"));}',success)
        self.assertEqual(panel.get_attribute('data-task-busy'),'false')
        self.assertFalse(self.page.locator('[data-task-run] button').is_disabled())

    def test_dark_identity_palette_and_accessible_text_contrast(self):
        self.open('dark')
        self.assertTrue(self.page.locator('html').evaluate('node=>node.classList.contains("dark")'))
        colors=self.page.locator('.run-records-fill').first.evaluate('node=>({fill:getComputedStyle(node).backgroundColor,text:getComputedStyle(node.closest(".run-records")).color})')
        self.assertEqual(colors['fill'],'rgb(128, 173, 211)')
        self.assertEqual(colors['text'],'rgb(237, 242, 247)')

    def test_reduced_motion_disables_spinner(self):
        self.open('analysis')
        self.assertEqual(self.page.locator('[data-task-run-label]').evaluate('node=>getComputedStyle(node,"::before").animationName'),'none')

    def test_real_modal_poll_logic_keeps_last_snapshot_on_network_failure(self):
        # Execute the real tasksPage state machine; fetch mocked before any call.
        source=(ROOT/'app/web/templates/web/tasks.html').read_text()
        function=source[source.index('    function tasksPage()'):source.rindex('</script>')]
        setup='const RUN_JOB_ID=1980,OPEN_ADD_MODAL=false,RUNNING_JOBS={},RUN_CSRF="fake",RUN_TERMINAL=["done","failed","cancelled","not_found"];'
        self.page.goto('about:blank')
        self.page.evaluate(setup+function+';window.state=tasksPage();window.fetch=async()=>({ok:true,json:async()=>({status:"running",run_progress_html:"<p>62/100</p>"})});')
        self.page.evaluate('state.pollOnce()');self.page.wait_for_timeout(30)
        self.assertEqual(self.page.evaluate('state.runModal.data.run_progress_html'),'<p>62/100</p>')
        self.page.evaluate('window.fetch=async()=>{throw new Error("offline")};state.pollOnce()');self.page.wait_for_timeout(30)
        self.assertTrue(self.page.evaluate('state.runModal.networkError'))
        self.assertEqual(self.page.evaluate('state.runModal.data.run_progress_html'),'<p>62/100</p>')
        self.assertTrue(self.page.evaluate('state.runModal.loading'))
        self.page.evaluate('window.fetch=async()=>({ok:true,json:async()=>({status:"done",run_progress_html:"<p>100/100</p>"})});state.pollOnce()');self.page.wait_for_timeout(30)
        self.assertFalse(self.page.evaluate('state.runModal.loading'))

if __name__=='__main__':unittest.main()
