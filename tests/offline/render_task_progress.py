"""Render real task-detail content with a minimal offline shell, never boot app.

Usage: python tests/offline/render_task_progress.py /tmp/task-progress-fixtures
The shell replaces CDN Tailwind/Alpine only for offline QA. Component templates,
component CSS, header mutation controls and mutation JS are production files.
"""
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.jobs.progress_metrics import record_metrics
spec = importlib.util.spec_from_file_location('offline_task_progress', ROOT/'app/web/task_progress.py')
ui = importlib.util.module_from_spec(spec); spec.loader.exec_module(ui)

# Layout-only utility subset, not a second progress renderer.
SHELL = '''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Offline task template</title>
<style>*{box-sizing:border-box}body{margin:0;background:var(--color-bg);color:var(--color-text);font:16px/1.5 Arial,sans-serif}h1,h2,p,dl,dd{margin:0}h1{font-size:26px;line-height:1.3}h2{font-size:16px}a{color:inherit}button{font:inherit;cursor:pointer;min-height:44px;background:transparent;color:inherit}button:disabled{cursor:not-allowed;opacity:.6}button,a:focus-visible{outline-offset:4px}header{padding:24px;border-bottom:1px solid var(--color-border);background:var(--color-surface);display:flex;gap:24px;align-items:center;justify-content:space-between}main{max-width:1360px;margin:auto;padding:24px}.flex{display:flex}.flex-wrap{flex-wrap:wrap}.items-center{align-items:center}.items-start{align-items:start}.justify-between{justify-content:space-between}.gap-2{gap:8px}.gap-3{gap:12px}.gap-1\\.5{gap:6px}.px-2{padding-left:8px;padding-right:8px}.py-1{padding-top:4px;padding-bottom:4px}.rounded-full{border-radius:999px}.gap-4{gap:16px}.grid{display:grid}.grid-cols-2{grid-template-columns:repeat(2,minmax(0,1fr))}.min-w-0{min-width:0}.mt-1{margin-top:4px}.mt-3{margin-top:12px}.mt-4{margin-top:16px}.mt-6{margin-top:24px}.space-y-3>div+div{margin-top:20px}.text-sm{font-size:14px}.text-xs{font-size:14px}.font-medium{font-weight:600}.border-t{border-top:1px solid var(--color-border)}.border{border:1px solid var(--color-border-strong)}.rounded-lg{border-radius:8px}.px-4{padding-left:16px;padding-right:16px}.py-2{padding-top:8px;padding-bottom:8px}.pt-3{padding-top:12px}.uppercase{text-transform:uppercase;letter-spacing:.6px}.text-slate-600,.text-slate-500{color:var(--color-text-secondary)}.bg-cyan-500{background:#06b6d4;color:#082b37}.text-amber-800{color:#815000}.text-rose-700{color:#ad3340}.bg-slate-100{background:var(--color-surface-secondary)}.shell-page-actions{display:flex;flex-wrap:wrap;gap:8px}footer{padding:24px;font-size:14px;color:var(--color-text-secondary)}.compact{max-width:580px;margin:40px auto;padding:24px;border:1px solid var(--color-border);border-radius:12px;background:var(--color-surface)}@media(min-width:1024px){.lg\\:grid-cols-3{grid-template-columns:2fr 1fr}}@media(max-width:640px){header{align-items:flex-start;flex-direction:column;padding:20px 16px}main{padding:20px 16px}h1{font-size:22px}.compact{margin:16px}.shell-page-actions{width:100%}}
</style><link rel="stylesheet" href="{{ static_v('css/theme.css') }}"><link rel="stylesheet" href="{{ static_v('css/ui.css') }}">{% block extra_head %}{% endblock %}</head><body><header><div><h1>{% block page_heading %}{% endblock %}</h1><p class="text-sm text-slate-600">work · Своё пространство</p></div><div id="page-actions"></div></header><main>{% block content %}{% endblock %}</main><footer>Изолированный HTML-стенд · синтетические данные · без приложения, БД и провайдеров. Оболочка адаптирована для офлайн-проверки.</footer><script>document.querySelectorAll('template[x-teleport]').forEach(t=>{document.querySelector(t.getAttribute('x-teleport')).append(t.content.cloneNode(true));t.remove()});</script></body></html>'''


def build(output):
    output.mkdir(parents=True, exist_ok=True)
    env = Environment(loader=ChoiceLoader([DictLoader({'web/base.html':SHELL}),FileSystemLoader(ROOT/'app/web/templates')]),autoescape=select_autoescape(('html',)))
    env.filters['ui_label']=lambda value:value
    env.filters['human_dt']=lambda *_a,**_kw:'сегодня, 12:42'
    static=lambda path:(ROOT/'app/static'/path).as_uri()
    task=NS(id=2027,name='Поездка в Италию · Кабанов',tenant=NS(name='work'),job_type='analyze',last_run_at=None,last_status=None,
            payload={'collect_before_analyze':True,'keywords_list':['Поездка','Италия']},cron_expr='',next_run_at=None,dry_run=False,is_active=False)
    inventory=[{'source_id':i,'name':name} for i,name in [(1,'Александр Кабанов'),(2,'ГК Древиз (Киров)'),(3,'Поиск VK: Древиз Италия')]]
    for state in ['pending','collection','analysis','partial','dry-run','success','empty','failed','unconfirmed']:
        rows=[{'source_id':i,'name':inventory[i-1]['name'],'raw_total':900,'raw_selected':n,'raw_processed':n,
               'collection':{'source_id':i,'items':n,'new_items':n,'outcome':'collected'}} for i,n in [(1,40),(2,35),(3,25)]]
        status='done'; result={'sources':3,'analyzed':3,'collection':{'sources':3},'per_source':rows}
        if state in {'analysis','unconfirmed','failed'}: rows[1]['raw_processed']=22;rows[2]['raw_processed']=0
        if state=='partial':rows[1].update(raw_processed=28,staged_error=True);rows[2].update(raw_processed=20,staged_error=True)
        if state=='empty':
            for row in rows:row.update(raw_selected=0,raw_processed=0);row['collection'].update(items=0,new_items=0,outcome='empty')
            result['analyzed']=0
        if state=='dry-run':
            result['dry_run']=True;result['analyzed']=0
            for row in rows:row['raw_processed']=0;row['collection'].update(items=0,new_items=0,outcome='empty')
            rows[2]['collection'].update(outcome='error',error_code='api_1051',error_hint='SYNTHETIC_SECRET_MUST_NOT_RENDER')
        if state in {'analysis','collection','unconfirmed','failed'}:
            status='failed' if state=='failed' else 'running'
            sources=[NS(id=i) for i in range(1,4)]
            phase='collection' if state=='collection' else 'analysis'
            records={'collection':record_metrics(sources,[dict(row['collection'],source_id=row['source_id']) for row in rows],'collection',complete=phase!='collection')}
            if phase=='analysis': records['analysis']=record_metrics(sources,rows,'analysis')
            result={'progress':{'attempt':1,'total':3,'completed':1,'first':0,'phase':phase,'items':[],'records':records}}
        if state=='pending':status='pending';result={}
        j=NS(id=1980,status=status,job_type='analyze',result=result,payload={},attempts=1,error='SYNTHETIC_SECRET_MUST_NOT_RENDER' if state=='failed' else None,created_at=datetime(2026,10,20,tzinfo=timezone.utc))
        view=ui.project_run(j,task); sidebar=ui.sidebar_sources(inventory,j,admin=True)
        summary={'status':status,'label':{'done':'Выполнено частично' if state in {'partial','dry-run'} else 'Анализ завершён','failed':'Анализ прерван','pending':'В очереди','running':'В работе'}[status],
                 'outcome':'partial' if state in {'partial','dry-run'} else 'ok','heartbeat_unconfirmed':state=='unconfirmed',
                 'error':'Сервис анализа временно недоступен. Повторите позже.' if state=='failed' else None}
        panel={'job':j,'summary':summary,'run_progress':view,'source_inventory':sidebar,'can_view_sources':True,'sources':[],'run_scope':None,'legacy_status_label':'','can_retry_analysis':True}
        html=env.get_template('web/task_detail.html').render(task=task,request=NS(query_params={}),perms=None,static_v=static,perms_can=lambda *_a:True,
            running_job_id=j.id if status in {'pending','running'} else None,running_job_status=status,effective_active=set(),csrf='offline-fake-csrf',
            latest_run=panel,recent_jobs=[],scenario=NS(name='Поездка дизайнеров в Италию'),JOB_TYPE_TITLES={'analyze':'Анализ соцсетей'},cron_to_human=lambda _v:'Разовый запуск',_tz='UTC')
        assert 'SYNTHETIC_SECRET_MUST_NOT_RENDER' not in html
        (output/f'{state}.html').write_text(html)
        if state=='analysis':
            compact=str(env.get_template('web/_run_progress.html').module.record_progress(view,compact=True))
            modal=SHELL.replace('{% block extra_head %}{% endblock %}', '<link rel="stylesheet" href="{{ static_v(\'css/task-run-progress.css\') }}">').replace('{% block content %}{% endblock %}', '<section class="compact" role="dialog" aria-modal="true"><h2>Выполнение анализа</h2><p class="mt-3">Поездка в Италию · Кабанов</p>'+compact+'<p class="mt-4"><a href="/app/tasks/2027">Открыть задачу →</a></p></section>')
            (output/'compact.html').write_text(env.from_string(modal).render(static_v=static))
    (output/'dark.html').write_text((output/'analysis.html').read_text().replace('<html lang="ru">','<html lang="ru" class="dark">'))
    print(json.dumps({'fixtures':11,'output':str(output),'application_started':False}))

if __name__=='__main__':build(Path(sys.argv[1]))
