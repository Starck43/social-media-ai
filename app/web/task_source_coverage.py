"""Source coverage from persisted task-run evidence."""

_COLLECTION_ISSUES = {
    "auth_required": "Нужно переподключить источник",
    "rate_limited": "Достигнут лимит запросов",
    "error": "Ошибка сбора",
    "narrowed": "Источник проверен с ограниченным доступом",
}


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _source_ids(value):
    if isinstance(value, list) and all(type(source_id) is int and source_id > 0 for source_id in value):
        return set(value)
    return None


def source_coverage(job):
    """Summarize recorded collection outcomes for this task run."""
    if job.status != "done" or job.job_type not in ("collect", "analyze"):
        return None
    result = _mapping(job.result)
    payload = _mapping(getattr(job, "payload", None))
    snapshot = _mapping(payload.get("_effective_snapshot"))
    if any(config.get("dry_run") is True for config in (result, payload, snapshot)):
        return None

    stages = _mapping(_mapping(result.get("pipeline")).get("stages"))
    if stages:
        collection = _mapping(_mapping(stages.get("collect")).get("result"))
        counts = _mapping(collection.get("counts"))
        rows = collection.get("per_source")
    elif job.job_type == "analyze":
        collection = _mapping(result.get("collection"))
        counts = collection
        per_source = result.get("per_source")
        if not isinstance(per_source, list):
            return None
        rows = [
            {**row["collection"], "source_id": row.get("source_id"), "name": row.get("name")}
            for row in per_source
            if isinstance(row, dict) and isinstance(row.get("collection"), dict)
        ]
    else:
        collection = counts = result
        rows = result.get("per_source")

    expected = counts.get("sources")
    if type(expected) is not int or expected < 0 or not isinstance(rows, list) or len(rows) != expected:
        return None

    planned = _source_ids(_mapping(snapshot.get("task_payload")).get("source_ids"))
    resolved = _source_ids(collection.get("coverage_source_ids"))
    if planned is None and resolved is None:
        return None

    seen = set()
    checked = 0
    issues = []
    for row in rows:
        if not isinstance(row, dict):
            return None
        source_id = row.get("source_id")
        if type(source_id) is not int or source_id <= 0 or source_id in seen:
            return None
        seen.add(source_id)
        outcome = row.get("outcome")
        if outcome in ("collected", "empty") and not row.get("narrowed_access"):
            checked += 1
            continue
        if outcome in ("collected", "empty"):
            outcome = "narrowed"
        if isinstance(outcome, str) and outcome in _COLLECTION_ISSUES:
            name = row.get("name")
            issues.append((name[:80] if isinstance(name, str) and name else None, _COLLECTION_ISSUES[outcome]))

    total = len(seen.union(planned or (), resolved or ()))
    skipped = _mapping(collection.get("task_budget")).get("sources_skipped")
    if not total or (type(skipped) is int and expected + skipped > total):
        return None
    return {
        "total": total,
        "checked": checked,
        "issues": issues[:2],
        "issue_count": len(issues),
        "unconfirmed": total - checked - len(issues),
    }
