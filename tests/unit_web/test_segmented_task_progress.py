"""Offline record-progress regressions: no web package, ORM or app boot imports."""
import ast
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.jobs.progress import ProgressSession, read_snapshot, ProgressStopped
from app.jobs.progress_metrics import record_metrics, read_metrics

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('task_progress_isolated', ROOT / 'app/web/task_progress.py')
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)  # Avoid app.web.__init__: it imports real routers/models.


def job(rows=None, **changes):
    defaults = dict(id=31, job_type='analyze', status='done', attempts=1, error=None,
                    payload={}, result={'sources': 3, 'analyzed': 2, 'collection': {'sources': 3},
                    'per_source': rows if rows is not None else [
                        {'source_id': i, 'raw_total': 900, 'raw_selected': size, 'raw_processed': size,
                         'collection': {'items': size, 'new_items': size, 'outcome': 'collected'}}
                        for i, size in [(1, 40), (2, 35), (3, 25)]]})
    defaults.update(changes)
    return NS(**defaults)


def task(**changes):
    return NS(**(dict(id=21, name='Task', job_type='analyze', dry_run=False,
                     last_run_at=None, last_status=None, payload={'collect_before_analyze': True}) | changes))


def environment():
    env = Environment(loader=FileSystemLoader(ROOT / 'app/web/templates'), autoescape=select_autoescape(('html',)))
    env.filters['human_dt'] = lambda *_a, **_kw: 'сегодня, 12:42'
    return env


def test_final_record_counts_are_not_result_counts_or_backlog():
    view = ui.project_run(job(), task())
    assert [(p['processed'], p['total']) for p in view['phases']] == [(100, 100), (100, 100)]
    assert view['results'] == 2
    assert [s['weight'] for s in view['phases'][1]['segments']] == [40, 35, 25]


def test_partial_is_not_successful_source_traversal():
    j = job()
    j.result['per_source'][1].update(raw_processed=28, staged_error=True)
    view = ui.project_run(j, task())
    assert view['phases'][1]['processed'] == 93
    assert view['phases'][1]['percent'] == 93
    assert view['phases'][1]['segments'][1]['failed'] is True


def test_collection_failure_keeps_denominator_unknown():
    j = job()
    j.result['per_source'][2]['collection'] = {'items': 0, 'outcome': 'error'}
    phase = ui.project_run(j, task())['phases'][0]
    assert phase['processed'] == 75 and phase['total'] is None and phase['percent'] is None


def test_no_sources_is_zero_not_unknown():
    j = job([], result={'sources': 0, 'per_source': [], 'collection': {'sources': 0}})
    assert [(p['processed'], p['total']) for p in ui.project_run(j, task())['phases']] == [(0, 0), (0, 0)]


@pytest.mark.parametrize('value', [True, -1, '5', 10**10, {}, None])
def test_invalid_counts_are_never_success(value):
    m = record_metrics([NS(id=1)], [{'source_id': 1, 'raw_processed': value, 'raw_selected': 10}], 'analysis')
    assert m['processed'] is None


def test_over_window_totals_are_aggregated_before_bounding():
    rows = [{'source_id': i, 'raw_processed': i, 'raw_selected': i + 1} for i in range(1, 151)]
    m = record_metrics([NS(id=i) for i in range(1, 151)], rows, 'analysis', complete=True)
    assert len(m['segments']) == 51 and m['segments'][-1]['source_id'] == 0
    assert m['processed'] == sum(range(1, 151)) and m['total'] == sum(range(2, 152))
    assert sum(r['processed'] for r in m['segments']) == m['processed']


def test_final_over_window_projection_preserves_all_records():
    rows = [{'source_id': i, 'raw_processed': 2, 'raw_selected': 3} for i in range(1, 151)]
    view = ui.project_run(job(rows, result={'sources': 150, 'per_source': rows}), task())
    assert view['phases'][1]['processed'] == 300 and view['phases'][1]['total'] == 450


def test_final_missing_source_is_not_complete():
    j = job(); j.result['sources'] = 4
    assert ui.project_run(j, task())['phases'][1]['total'] is None


@pytest.mark.parametrize('status', ['pending', 'running', 'done', 'failed', 'cancelled'])
def test_dry_run_never_credits_analysis(status):
    j = job(status=status); j.result['dry_run'] = True
    view = ui.project_run(j, task())
    assert view['dry_run'] and view['results'] is None
    assert view['phases'][1]['processed'] is None and view['phases'][1]['segments'] == []
    html = str(environment().get_template('web/_run_progress.html').module.record_progress(view))
    assert 'модель не вызывается' in html


def test_actual_dry_run_evidence_wins_over_mutable_task():
    j = job(payload={'_effective_snapshot': {'dry_run': True}}); j.result['dry_run'] = False
    assert ui.project_run(j, task(dry_run=True))['dry_run'] is False


def test_pending_and_attempt_mismatch_do_not_reuse_old_progress():
    snapshot = dict(attempt=1, total=1, completed=1, first=0, phase='analysis', items=[
        {'source_id': 1, 'name': 'A', 'state': 'done', 'raw_selected': 10, 'raw_processed': 10}])
    for status, attempts in [('pending', 1), ('running', 2)]:
        view = ui.project_run(job(status=status, attempts=attempts, result={'progress': snapshot}), task())
        assert view['phases'][1]['processed'] is None


def test_legacy_window_is_never_summed_as_all_sources():
    snapshot = dict(attempt=1, total=100, completed=80, first=50, phase='analysis', items=[
        {'source_id': 51, 'name': 'A', 'state': 'done', 'raw_selected': 10, 'raw_processed': 10}])
    assert ui.project_run(job(status='running', result={'progress': snapshot}), task())['phases'][1]['processed'] is None


def test_running_snapshot_uses_all_record_metrics():
    m = record_metrics([NS(id=1)], [{'source_id': 1, 'raw_selected': 10, 'raw_processed': 6}], 'analysis')
    snapshot = dict(attempt=1, total=1, completed=0, first=0, phase='analysis', items=[], records={'analysis': m})
    assert ui.project_run(job(status='running', result={'progress': snapshot}), task())['phases'][1]['processed'] == 6


def test_unauthorized_projection_has_no_record_counts_or_result_counts():
    view = ui.project_run(job(), task(), authorized=False)
    assert all(p['processed'] is None and p['total'] is None and p['segments'] == [] for p in view['phases'])
    assert view['results'] is None


def test_sidebar_is_only_authorized_inventory_with_safe_labels_and_codes():
    j = job(); j.result['per_source'][1]['collection'].update(outcome='error', error_code='api_1051', error_hint='SECRET_SENTINEL')
    j.result['per_source'][2]['collection'].update(outcome='empty', error_code='SECRET_SENTINEL')
    inventory = [{'source_id': 2, 'name': '<script>alert(1)</script>'}, {'source_id': 3, 'name': 'A'}]
    rows = ui.sidebar_sources(inventory, j, admin=True)
    html = environment().get_template('web/_task_run_sources.html').render(can_view_sources=True, source_inventory=rows)
    assert '/app/sources/2' in html and 'api_1051' in html and 'SECRET_SENTINEL' not in html
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert 'Ошибка сбора' in html and 'нет данных' in html
    assert ui.sidebar_sources(inventory, j)[0]['code'] is None
    assert environment().get_template('web/_task_run_sources.html').render(can_view_sources=False, source_inventory=rows).strip() == ''


def test_full_and_compact_share_structure_without_any_source_list():
    env = environment(); view = ui.project_run(job(), task())
    for compact in (False, True):
        html = str(env.get_template('web/_run_progress.html').module.record_progress(view, compact=compact))
        assert html.count('role="progressbar"') == 2 and 'data-phase="analysis"' in html
        assert '/app/sources/' not in html and 'Источники этого запуска' not in html
        assert 'Результаты анализа: <b>2</b>' in html


def test_root_fragment_contains_all_content_and_oob_is_explicit():
    env = environment(); j = job()
    html = env.get_template('web/task_run_progress.html').render(task=task(), job=j, summary={'status':'done','label':'Готово'},
        run_progress=ui.project_run(j, task()), can_retry_analysis=True, running_job_id=None, csrf='fake', _tz='UTC')
    assert html.count('id="task-run-progress"') == 1 and 'role="progressbar"' in html
    assert 'retry-analysis' in html and 'hx-swap-oob' not in html
    for removed in ['Сырые данные источников задачи', 'Источники этого запуска', 'Охват задачи', '/app/sources/']:
        assert removed not in html


def test_cancelled_error_does_not_look_failed():
    assert ui.project_run(job(status='failed', error='Cancelled by operator'), task())['state'] == 'cancelled'


def test_metrics_reader_strips_extra_data_and_rejects_overcount():
    data = {'processed': 11, 'total': 10, 'source_total': 1, 'segments': [
        {'source_id':1,'processed':11,'total':10,'secret':'SYNTHETIC'}], 'token':'SYNTHETIC'}
    m = read_metrics(data)
    assert m['processed'] is None and m['segments'][0]['processed'] is None
    assert 'SYNTHETIC' not in str(m)


def test_phase_handoff_includes_last_collection_row_and_cancellation_propagates():
    async def run():
        snapshots=[]
        async def writer(_claim, value): snapshots.append(value); return True
        sources=[NS(id=1,name='A'),NS(id=2,name='B')]; rows=[]
        session=ProgressSession(NS(job_type='analyze'),writer=writer)
        await session.begin_phase('collection'); await session.step(sources,rows)
        rows.append({'source_id':1,'items':4,'outcome':'collected'})
        await session.step(sources,rows)
        rows.append({'source_id':2,'items':6,'outcome':'collected'})
        await session.begin_phase('analysis'); await session.step(sources,[])
        assert snapshots[-1]['records']['collection']['total'] == 10
        async def cancelled(*_a): raise asyncio.CancelledError()
        session=ProgressSession(NS(job_type='analyze'), writer=cancelled)
        with pytest.raises(asyncio.CancelledError): await session.step(sources,[])
        async def lost(*_a): return False
        session=ProgressSession(NS(job_type='analyze'),writer=lost)
        with pytest.raises(ProgressStopped): await session.step(sources,[])
    asyncio.run(run())


def test_handlers_and_mutation_admission_remain_existing_contracts():
    # Static guardrails, NOT endpoint/DB acceptance. No module import/startup.
    source=(ROOT/'app/web/tasks.py').read_text(); tree=ast.parse(source)
    functions={node.name:ast.get_source_segment(source,node) for node in tree.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))}
    latest=functions['task_latest_progress']
    assert '"agenttask", "view"' in latest and 'tenant_id=tenant_id' in latest
    assert 'status__in=["pending", "running"]' in latest and 'sidebar_oob=True' in latest
    assert 'include_raw=' not in latest
    assert 'queue_task_now' in functions['task_run_now'] and 'return_to_detail' in functions['task_run_now']
    edit=(ROOT/'app/web/templates/web/task_edit.html').read_text()
    assert 'return_to_detail' in edit and '_top' in edit
    page=(ROOT/'app/web/templates/web/task_detail.html').read_text()
    assert 'Дизайн-прототип' not in page and 'data-task-mutation' in page


def isolated_functions(*names):
    tree = ast.parse((ROOT/'app/web/tasks.py').read_text())
    class WithoutImports(ast.NodeTransformer):
        def visit_ImportFrom(self, node): return None
        def visit_Import(self, node): return None
    nodes=[]
    for node in tree.body:
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in names:
            node.decorator_list=[];nodes.append(WithoutImports().visit(node))
    module=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),*nodes],type_ignores=[])
    scope={};exec(compile(ast.fix_missing_locations(module),'isolated-tasks-functions','exec'),scope)
    return scope


@pytest.mark.parametrize('allowed,found', [(False,False),(True,False),(True,True)])
def test_actual_latest_endpoint_scopes_mocked_orm_and_prefers_busy(allowed,found):
    from unittest.mock import AsyncMock
    calls=[];task_row=task();task_row.tenant_id=7
    class Query:
        def __init__(self,value):self.value=value
        def order_by(self,*_a):return self
        async def first(self):return self.value
    def job_filter(**kwargs):calls.append(kwargs);return Query(job(status='pending'))
    scope=isolated_functions('task_latest_progress')
    scope.update(perms_can=lambda *_a:allowed,HTMLResponse=lambda text,status_code:NS(status_code=status_code),
        AgentTask=NS(objects=NS(filter=lambda **kw:Query(task_row if found else None))),
        Job=NS(objects=NS(filter=job_filter),created_at=NS(desc=lambda:None)),
        _task_run_scope=AsyncMock(return_value=None),_attach_task_llm_diagnostics=AsyncMock(),
        _task_run_panel_data=lambda _r,_t,j,**_k:{'job':j},render=lambda _r,name,**context:context)
    response=asyncio.run(scope['task_latest_progress'](NS(state=NS(tenant_id=7)),21))
    if not allowed or not found:
        assert calls==[] and response.status_code in {403,404}
    else:
        assert calls==[{'agent_task_id':21,'tenant_id':7,'status__in':['pending','running']}]
        assert response['job'].status=='pending' and response['sidebar_oob'] is True


def test_actual_scope_inventory_uses_task_binding_without_querying_backlog():
    from contextlib import nullcontext
    from unittest.mock import AsyncMock
    sources=[NS(id=1,name='A',is_active=True,platform_id=1),NS(id=2,name='B',is_active=False,platform_id=1)]
    class Query:
        def __await__(self):
            async def value():return sources
            return value().__await__()
    scope=isolated_functions('_task_run_scope')
    scope.update(tenant_scope=lambda *_a:nullcontext(),Source=NS(objects=NS(filter=lambda **kw:Query())),_task_source_ids=AsyncMock(return_value=[2]))
    t=task();t.tenant_id=7
    result=asyncio.run(scope['_task_run_scope'](t,include_sources=True))
    assert result['inventory']==[{'source_id':2,'name':'B'}] and 'raw_backlog' not in result
    scope['_task_source_ids']=AsyncMock(return_value=[])
    assert asyncio.run(scope['_task_run_scope'](t,include_sources=True))['inventory']==[{'source_id':1,'name':'A'}]


def test_updated_existing_template_contracts_without_importing_web_app():
    # Execute just three existing pure template tests; skip their module's ORM
    # imports/other functions. This is not execution of its DB-coupled tests.
    from datetime import datetime, timezone
    tree=ast.parse((ROOT/'tests/unit_web/test_task_detail_progress.py').read_text())
    names={'test_task_detail_template_renders_task_run_actions_and_progress_counters',
           'test_task_run_partial_shows_pre_run_status_scope_and_retry_only_for_prior_run',
           'test_task_run_partial_keeps_all_run_content_inside_htmx_root'}
    nodes=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in names]
    scope=dict(ROOT=ROOT,Environment=Environment,FileSystemLoader=FileSystemLoader,select_autoescape=select_autoescape,
               SimpleNamespace=NS,datetime=datetime,timezone=timezone)
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),'existing-template-contracts','exec'),scope)
    for name in names:scope[name]()
