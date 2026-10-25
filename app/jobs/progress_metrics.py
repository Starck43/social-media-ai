"""Bounded record counters for display; no database, payloads or provider text."""

from __future__ import annotations

from typing import Any

MAX_SEGMENTS = 50
LIMIT = 10**9


def count(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value <= LIMIT else None


def record_metrics(sources, rows, phase: str, *, current=None, complete=False) -> dict:
    """Aggregate BEFORE windowing; unknown/unvisited records are never zeroes."""
    by_id = {r['source_id']: r for r in rows if isinstance(r, dict) and type(r.get('source_id')) is int}
    if isinstance(current, dict) and type(current.get('source_id')) is int:
        by_id[current['source_id']] = current
    segments = []
    for source in sources:
        row = by_id.get(source.id, {})
        if phase == 'collection':
            processed = count(row.get('items'))
            unsuccessful = row.get('outcome') in {'error', 'auth_required', 'rate_limited'}
            total = processed if processed is not None and not unsuccessful else None
        else:
            processed, total = count(row.get('raw_processed')), count(row.get('raw_selected'))
            unsuccessful = row.get('staged_error') is True
        if processed is not None and total is not None and processed > total:
            processed = None
        segments.append({'source_id': source.id, 'processed': processed, 'total': total,
                         'failed': unsuccessful})
    known = [r['processed'] for r in segments if r['processed'] is not None]
    processed = min(LIMIT, sum(known)) if known else (0 if complete and not segments else None)
    all_totals = (bool(segments) or complete) and all(r['total'] is not None for r in segments)
    # Collection has no up-front platform denominator; finish discovery first.
    total = min(LIMIT, sum(r['total'] for r in segments)) if all_totals and (complete or phase == 'analysis') else None
    shown = segments[:MAX_SEGMENTS]
    if len(segments) > MAX_SEGMENTS:
        rest = segments[MAX_SEGMENTS:]
        shown.append({'source_id': 0,
                      'processed': min(LIMIT, sum(r['processed'] or 0 for r in rest)) if any(r['processed'] is not None for r in rest) else None,
                      'total': min(LIMIT, sum(r['total'] for r in rest)) if all(r['total'] is not None for r in rest) else None,
                      'failed': any(r['failed'] for r in rest)})
    return {'processed': processed, 'total': total, 'source_total': len(segments),
            'completed': bool(complete), 'segments': shown}


def read_metrics(value: Any) -> dict | None:
    """Allowlist persisted display counters; malformed counts stay unknown."""
    if not isinstance(value, dict) or not isinstance(value.get('segments'), list):
        return None
    source_total = count(value.get('source_total'))
    if source_total is None:
        return None
    processed, total = count(value.get('processed')), count(value.get('total'))
    if total is not None and processed is not None and processed > total:
        processed = None
    segments = []
    for row in value['segments'][:MAX_SEGMENTS + 1]:
        if not isinstance(row, dict) or count(row.get('source_id')) is None:
            continue
        done, size = count(row.get('processed')), count(row.get('total'))
        if done is not None and size is not None and done > size:
            done = None
        segments.append({'source_id': row['source_id'], 'processed': done, 'total': size, 'failed': row.get('failed') is True})
    return {'processed': processed, 'total': total, 'source_total': source_total,
            'completed': value.get('completed') is True, 'segments': segments}
