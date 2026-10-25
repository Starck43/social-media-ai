"""Pure task-run presentation, shared by full and compact views."""

from __future__ import annotations

import re
from types import SimpleNamespace

from app.jobs.progress import read_snapshot
from app.jobs.recovery_policy import CANCELLED_BEFORE_START_ERROR, OPERATOR_CANCELLED_ERROR
from app.jobs.progress_metrics import count, record_metrics

# Colors identify sources, never success/failure. Outcome also has explicit text.
COLORS = ("#087f98", "#3579a2", "#6964a5", "#526c42", "#91604c", "#886080")
UNSUCCESSFUL = {"error", "auth_required", "rate_limited"}


def _rows(value):
    if not isinstance(value, list):
        return []
    result = {}
    for row in value:
        if isinstance(row, dict) and type(row.get('source_id')) is int and 0 < row['source_id'] <= 10**9:
            result[row['source_id']] = row
    return list(result.values())


def source_statuses(job) -> dict:
    """Latest run only, fixed action labels, never raw error messages or hints."""
    if job is None or job.status == 'pending':
        return {}
    result = job.result if isinstance(job.result, dict) else {}
    if job.status == 'done':
        rows = _rows(result.get('per_source'))
    else:
        snapshot = read_snapshot(result, getattr(job, 'attempts', None))
        rows = _rows((snapshot or {}).get('items'))
    statuses = {}
    for row in rows:
        collection = row.get('collection') if isinstance(row.get('collection'), dict) else row
        outcome = collection.get('outcome')
        label, failed = '', False
        if outcome in UNSUCCESSFUL or row.get('collection_error') is True:
            failed = True
            label = {'auth_required': 'Сбор: нужна авторизация',
                     'rate_limited': 'Сбор отложен: лимит запросов'}.get(outcome, 'Ошибка сбора')
        elif row.get('error') is True or row.get('staged_error') is True:
            failed, label = True, 'Ошибка анализа'
        elif outcome == 'empty':
            label = 'Сбор: нет данных за выбранный период'
        elif outcome == 'collected' and count(collection.get('new_items')) == 0:
            label = 'Сбор: новых данных нет'
        if label:
            # Even an identifier-shaped arbitrary string could be a secret.
            code = row.get('error_code') or collection.get('error_code')
            safe_code = code if isinstance(code, str) and (
                re.fullmatch(r'api_\d{1,6}', code) or code in {
                    'timeout', 'rate_limited', 'provider_failed', 'storage_operation_failed',
                    'failed_output', 'failed_transport', 'authorization_required'
                }) else None
            statuses[row['source_id']] = {'label': label, 'failed': failed, 'code': safe_code}
    return statuses


def sidebar_sources(inventory, job, *, admin=False):
    """Decorate only authorized current inventory, not historical foreign IDs."""
    statuses = source_statuses(job)
    return [dict(source, shade=source['source_id'] % len(COLORS), color=COLORS[source['source_id'] % len(COLORS)],
                 last_action=statuses.get(source['source_id'], {}).get('label'),
                 failed=statuses.get(source['source_id'], {}).get('failed', False),
                 code=statuses.get(source['source_id'], {}).get('code') if admin else None)
            for source in inventory]


def _final_metrics(result, phase, job_type):
    rows = _rows(result.get('per_source'))
    expected = count(result.get('sources'))
    if phase == 'collection' and job_type == 'analyze':
        collection = result.get('collection')
        if not isinstance(collection, dict):
            return None
        expected = count(collection.get('sources'))
        rows = [dict(row.get('collection') or {}, source_id=row['source_id']) for row in rows]
    sources = [SimpleNamespace(id=row['source_id']) for row in rows]
    metrics = record_metrics(sources, rows, phase, complete=True)
    if expected is None or expected != len(rows):
        metrics['total'] = None
        metrics['source_total'] = expected if expected is not None else len(rows)
        metrics['completed'] = False
    return metrics


def _phase(key, metrics, state, *, dry_run=False, disabled=False):
    metrics = metrics or {}
    done, total = metrics.get('processed'), metrics.get('total')
    if disabled:
        done, total = None, None
    segments = []
    raw_segments = metrics.get('segments', [])
    # Unknown totals use source slots, never a fabricated record percentage.
    sizes = [r.get('total') for r in raw_segments]
    weighted = bool(total) and all(size is not None for size in sizes) and sum(sizes) == total
    for row in raw_segments:
        size, value = row.get('total'), row.get('processed')
        fraction = min(100, int(value * 100 / size)) if value is not None and size else 0
        segments.append({'source_id': row['source_id'], 'shade': row['source_id'] % len(COLORS), 'color': COLORS[row['source_id'] % len(COLORS)],
                         'weight': size if weighted else 1, 'fill': fraction,
                         'failed': row.get('failed') is True, 'failed_width': max(2, 100 - fraction)})
    percent = int(done * 100 / total) if total and done is not None else None
    label = 'Сбор данных' if key == 'collection' else 'Анализ данных'
    if disabled:
        note = 'Сбор новых данных в этом запуске не включён.'
    elif dry_run:
        note = ('Получено для проверки. Данные не сохраняются.' if key == 'collection'
                else 'Модель не вызывается. Успешный анализ не засчитывается.')
        if key == 'analysis':
            done, total, percent, segments = None, None, None, []
    elif state == 'pending':
        note = 'Ожидает получения задания.'
    elif done is None:
        note = 'Счётчики этого этапа пока недоступны.'
    elif total is None:
        note = 'Общий объём уточняется; сегменты обозначают источники, а не доли записей.'
    elif total == 0:
        note = 'Нет записей для обработки.'
    elif key == 'collection':
        note = 'Успешно получено из источников для этого запуска.'
    else:
        note = f'Успешно обработано · из выборки этого запуска: осталось {max(0, total - done)}.'
    return {'key': key, 'label': label, 'processed': done, 'total': total, 'percent': percent,
            'segments': segments, 'note': note, 'state': state, 'disabled': disabled}


def project_run(job, task=None, *, authorized=True):
    """Job evidence wins over mutable task settings; pending ignores old attempts."""
    job_type = getattr(job, 'job_type', None) or getattr(task, 'job_type', None)
    if job_type not in {'collect', 'analyze'}:
        return None
    result = getattr(job, 'result', None)
    result = result if isinstance(result, dict) else {}
    payload = getattr(job, 'payload', None)
    payload = payload if isinstance(payload, dict) else {}
    snapshot_config = payload.get('_effective_snapshot')
    snapshot_config = snapshot_config if isinstance(snapshot_config, dict) else {}
    dry_run = next((config['dry_run'] for config in (result, snapshot_config, payload)
                    if type(config.get('dry_run')) is bool), False)
    if not job or getattr(job, 'status', None) == 'pending':
        dry_run = dry_run or getattr(task, 'dry_run', False) is True
    status = getattr(job, 'status', None)
    state = status if status in {'pending', 'running', 'done', 'failed', 'cancelled'} else 'unknown'
    if status == 'failed' and getattr(job, 'error', None) in {CANCELLED_BEFORE_START_ERROR, OPERATOR_CANCELLED_ERROR}:
        state = 'cancelled'
    metrics = {}
    if authorized and status == 'done':
        metrics = {key: _final_metrics(result, key, job_type) for key in ('collection', 'analysis')}
    elif authorized and status in {'running', 'failed', 'cancelled'}:
        snap = read_snapshot(result, getattr(job, 'attempts', None))
        if snap:
            metrics = snap.get('records') or {}
            if not metrics and snap['first'] == 0 and snap['total'] == len(snap['items']):
                phase = snap['phase'] if snap['phase'] != 'processing' else ('collection' if job_type == 'collect' else 'analysis')
                metrics[phase] = record_metrics([SimpleNamespace(id=r.get('source_id')) for r in snap['items']], snap['items'], phase)
    task_payload = getattr(task, 'payload', None)
    task_payload = task_payload if isinstance(task_payload, dict) else {}
    effective_payload = snapshot_config.get('task_payload')
    effective_payload = effective_payload if isinstance(effective_payload, dict) else task_payload
    collection_enabled = job_type == 'collect' or isinstance(result.get('collection'), dict) or 'collection' in metrics or effective_payload.get('collect_before_analyze') is True
    phases = [_phase('collection', metrics.get('collection'), state, dry_run=dry_run, disabled=not collection_enabled)]
    if job_type == 'analyze':
        phases.append(_phase('analysis', metrics.get('analysis'), state, dry_run=dry_run))
    results = count(result.get('analyzed')) if authorized and status == 'done' and not dry_run else None
    return {'phases': phases, 'dry_run': dry_run, 'results': results, 'state': state,
            'error': state == 'failed', 'authorized': authorized}
