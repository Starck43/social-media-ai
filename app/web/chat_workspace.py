"""Read-only workspace panel for chat; shared readers own plan/result access."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from .deps import perms_can

router = APIRouter(prefix="/workspace")
PAGE_SIZE = 20


def workspace_denied(request: Request):
    """Fail before any model imports or reads when identity/grants are absent."""
    user = getattr(request.state, "web_user", None)
    tenant_id = getattr(request.state, "tenant_id", None)
    perms = getattr(request.state, "web_perms", None)
    if (
        user is None
        or type(getattr(user, "id", None)) is not int
        or type(tenant_id) is not int
        or perms is None
        or not getattr(perms, "can_open_chat", False)
        or not perms_can(request, "agenttask", "view")
    ):
        return JSONResponse({"error": "Задачи недоступны в текущем пространстве"}, status_code=403)
    return None


async def selected_task(request: Request, task_id: int) -> dict | None:
    """Revalidate selected context on every read/send, using the canonical reader."""
    from app.core.tenant_context import tenant_scope
    from app.services.task_results import task_configuration

    with tenant_scope(request.state.tenant_id):
        definition = await task_configuration(task_id)
    # The panel deliberately exposes only the social task lane, matching its
    # picker. Retention/operator tasks keep their existing management gate.
    if definition is None or definition.get("job_type") not in {"collect", "analyze"}:
        return None
    return definition


@router.get("/tasks")
async def workspace_tasks(
    request: Request,
    search: str = Query("", max_length=100),
    offset: int = Query(0, ge=0, le=10000),
):
    denied = workspace_denied(request)
    if denied is not None:
        return denied
    from app.core.tenant_context import tenant_scope
    from app.models import AgentTask

    with tenant_scope(request.state.tenant_id):
        query = AgentTask.objects.filter(tenant_id=request.state.tenant_id, job_type__in=["collect", "analyze"])
        if search.strip():
            # Escape SQL LIKE metacharacters: the picker searches literal names.
            escaped = search.strip().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
            query = query.filter(AgentTask.name.ilike(f"%{escaped}%", escape="\\"))
        rows = await query.order_by(AgentTask.id.desc()).offset(offset).limit(PAGE_SIZE + 1)
    return JSONResponse(
        {
            "tasks": [{"id": row.id, "name": row.name[:160]} for row in rows[:PAGE_SIZE]],
            "next_offset": offset + PAGE_SIZE if len(rows) > PAGE_SIZE and offset < 10000 else None,
        },
        headers={"Cache-Control": "no-store"},
    )


def task_snapshot(definition: dict, result: dict | None, *, can_view_results: bool) -> dict:
    """Project only panel fields; never forward credentials, raw errors or payloads."""
    task_id = definition["id"]
    plan = definition.get("plan") if definition.get("plan_status") == "available" else None
    safe_plan = None
    if isinstance(plan, dict):
        safe_plan = {
            "goal": (plan.get("goal") or definition["name"])[:1000],
            "platforms": plan.get("platforms", []),
            "source_scope": plan.get("source_scope"),
            "source_count": len(plan.get("source_ids", [])),
            "window": plan.get("window"),
            "trigger": plan.get("trigger"),
            "stages": plan.get("pipeline", {}).get("stages", []),
            "limit": plan.get("collection", {}).get("limit"),
        }
    latest = None
    if result is not None:
        latest = {
            "status": result.get("job_status", "unavailable"),
            "coverage": result.get("coverage"),
            "progress": None,
            "analyses": [],
        }
        progress = result.get("progress")
        if isinstance(progress, dict):
            latest["progress"] = {key: progress.get(key) for key in ("phase", "total", "completed", "updated_at")}
        if can_view_results:
            latest["analyses"] = [
                {"title": row["title"], "url": f"/app/analytics/{row['id']}"} for row in result.get("analyses", [])[:3]
            ]
    return {
        "id": task_id,
        "name": definition["name"][:160],
        "task_url": f"/app/tasks/{task_id}",
        "plan_status": definition.get("plan_status", "unavailable"),
        "plan": safe_plan,
        "latest": latest,
    }


@router.get("/tasks/{task_id}")
async def workspace_task(request: Request, task_id: int):
    denied = workspace_denied(request)
    if denied is not None:
        return denied
    if task_id <= 0:
        return JSONResponse({"error": "Задача недоступна"}, status_code=404)
    try:
        definition = await selected_task(request, task_id)
        if definition is None:
            return JSONResponse({"error": "Задача недоступна"}, status_code=404)
        from app.core.tenant_context import tenant_scope
        from app.services.task_results import task_result

        with tenant_scope(request.state.tenant_id):
            result = await task_result(task_id, limit=3)
    except PermissionError:
        return JSONResponse({"error": "Доступ к задаче изменился"}, status_code=403)
    return JSONResponse(
        task_snapshot(
            definition,
            result,
            can_view_results=perms_can(request, "source", "view") and perms_can(request, "aianalytics", "view"),
        ),
        headers={"Cache-Control": "no-store"},
    )


CARD_TYPES = ("plan_preview", "decision", "progress", "result", "draft", "brief")
PANEL_TABS = ("context", "decisions", "runs", "materials")

_FORBIDDEN_CARD_KEYS = {
    "credential",
    "credential_id",
    "credentials",
    "token",
    "access_token",
    "refresh_token",
    "oauth_token",
    "secret",
    "client_secret",
    "password",
    "api_key",
    "raw",
    "raw_error",
    "raw_payload",
    "provider_payload",
    "sensitive_payload",
    "private_metadata",
    "vk_access",
    "notes_encrypted",
    "body_encrypted",
    "cookie",
}


def chat_panel_denied(request: Request):
    """Verify chat workspace identity and can_open_chat before any panel/card reads."""
    user = getattr(request.state, "web_user", None)
    tenant_id = getattr(request.state, "tenant_id", None)
    perms = getattr(request.state, "web_perms", None)
    if (
        user is None
        or type(getattr(user, "id", None)) is not int
        or type(tenant_id) is not int
        or perms is None
        or not getattr(perms, "can_open_chat", False)
    ):
        return JSONResponse({"error": "Рабочий контекст чата недоступен"}, status_code=403)
    return None


def sanitize_card_payload(value):
    """Strip any raw payloads, credentials, secrets, or internal error dumps recursively."""
    if isinstance(value, dict):
        cleaned = {}
        for k, v in value.items():
            key_str = str(k).lower()
            if key_str in _FORBIDDEN_CARD_KEYS or any(
                marker in key_str for marker in ("secret", "token", "password", "credential", "raw_", "_encrypted")
            ):
                continue
            cleaned[k] = sanitize_card_payload(v)
        return cleaned
    if isinstance(value, list):
        return [sanitize_card_payload(item) for item in value]
    return value


def build_task_cards(definition: dict, result: dict | None, *, can_view_results: bool) -> list[dict]:
    """Build typed inline cards (plan_preview, progress, result) from a canonical task snapshot."""
    snap = task_snapshot(definition, result, can_view_results=can_view_results)
    task_id = snap["id"]
    task_url = snap["task_url"]
    cards: list[dict] = []

    plan = snap.get("plan")
    cards.append(
        sanitize_card_payload(
            {
                "id": f"plan_preview:task:{task_id}",
                "card_type": "plan_preview",
                "title": f"План задачи: {snap['name']}",
                "status": snap.get("plan_status", "unavailable"),
                "summary": (plan.get("goal") if isinstance(plan, dict) else None)
                or "План недоступен или требует доступа к источникам.",
                "plan": plan,
                "authoritative_ref": f"agent_task:{task_id}",
                "authoritative_url": task_url,
                "actions": [
                    {"kind": "link", "label": "Открыть страницу задачи", "href": task_url},
                    {
                        "kind": "draft",
                        "label": "Обсудить запуск",
                        "prompt": (
                            "Покажи актуальный план выбранной задачи, проверь доступ и ограничения. "
                            "Подготовь разовый запуск и дождись моего подтверждения."
                        ),
                    },
                ],
            }
        )
    )

    latest = snap.get("latest")
    if isinstance(latest, dict):
        status = latest.get("status", "unavailable")
        if status in {"pending", "running"} or isinstance(latest.get("progress"), dict):
            cards.append(
                sanitize_card_payload(
                    {
                        "id": f"progress:task:{task_id}",
                        "card_type": "progress",
                        "title": f"Выполнение: {snap['name']}",
                        "status": status,
                        "progress": latest.get("progress"),
                        "authoritative_ref": f"agent_task:{task_id}",
                        "authoritative_url": task_url,
                        "actions": [
                            {"kind": "link", "label": "Управление запуском", "href": task_url},
                        ],
                    }
                )
            )
        cards.append(
            sanitize_card_payload(
                {
                    "id": f"result:task:{task_id}",
                    "card_type": "result",
                    "title": f"Результат: {snap['name']}",
                    "status": status,
                    "coverage": latest.get("coverage"),
                    "limitations": (
                        "Охват ограничен параметрами запуска; полнота данных не подтверждена."
                        if latest.get("coverage") == "partial_or_bounded"
                        else ""
                    ),
                    "evidence": latest.get("analyses", []),
                    "authoritative_ref": f"agent_task:{task_id}",
                    "authoritative_url": task_url,
                    "actions": [
                        {"kind": "link", "label": "Открыть результаты", "href": task_url},
                        {
                            "kind": "draft",
                            "label": "Разобрать результат",
                            "prompt": (
                                "Покажи результаты выбранной задачи со ссылками на материалы, "
                                "охватом и ограничениями. Предложи следующий шаг."
                            ),
                        },
                    ],
                }
            )
        )
    return cards


def build_context_cards(scoped_context: dict | None) -> list[dict]:
    """Build typed inline cards (draft / context reference) from a resolved context object."""
    if not isinstance(scoped_context, dict):
        return []
    kind = str(scoped_context.get("type") or scoped_context.get("context_type") or "knowledge")
    ctx_id = str(scoped_context.get("context_id") or scoped_context.get("id") or "")
    boundary = str(scoped_context.get("boundary") or scoped_context.get("data_boundary") or "cloud")
    route_map = {
        "document": f"/app/documents/{scoped_context.get('id', ctx_id)}",
        "communication": scoped_context.get("href") or "/app/communications",
        "contact": scoped_context.get("href") or f"/app/contacts/{scoped_context.get('id', ctx_id)}",
        "case": "/app/cases",
        "followup": "/app/followups",
        "knowledge": "/app/knowledge",
        "task": scoped_context.get("href") or f"/app/tasks/{scoped_context.get('id', ctx_id)}",
    }
    auth_url = route_map.get(kind, "/app/knowledge")
    card_type = "draft" if kind in {"document", "communication"} else "result"
    return [
        sanitize_card_payload(
            {
                "id": f"{card_type}:{kind}:{ctx_id}",
                "card_type": card_type,
                "context_type": kind,
                "title": str(scoped_context.get("title") or f"Объект #{ctx_id}")[:160],
                "status": str(scoped_context.get("status") or ("local_only" if boundary == "local_only" else "ready")),
                "boundary": boundary,
                "is_local_only": boundary == "local_only",
                "summary": str(scoped_context.get("excerpt") or "")[:400],
                "limitations": str(
                    scoped_context.get("extraction_limit_note") or scoped_context.get("unsupported_terms_note") or ""
                )[:240],
                "citation_anchor": str(scoped_context.get("citation_anchor") or ""),
                "revision_hash": str(scoped_context.get("revision_hash") or ""),
                "authoritative_ref": str(
                    scoped_context.get("ref") or scoped_context.get("source_ref") or f"{kind}:{ctx_id}"
                ),
                "authoritative_url": auth_url,
                "actions": [
                    {"kind": "link", "label": "Открыть страницу", "href": auth_url},
                ],
            }
        )
    ]


def build_decision_cards(
    decisions: list[dict] | None = None,
    *,
    context_key: str | None = None,
) -> list[dict]:
    """Project pending decision cards from P20-T03 when present; never execute unapproved writes."""
    if not decisions:
        return []
    cards: list[dict] = []
    for item in decisions[:10]:
        if not isinstance(item, dict):
            continue
        target_ctx = str(item.get("context_key") or item.get("authoritative_ref") or "")
        if context_key and target_ctx and context_key not in target_ctx:
            continue
        dec_id = item.get("id") or item.get("key") or "pending"
        auth_url = str(item.get("authoritative_url") or item.get("href") or "/app/chat")
        cards.append(
            sanitize_card_payload(
                {
                    "id": f"decision:{dec_id}",
                    "card_type": "decision",
                    "title": str(item.get("title") or "Ожидает решения")[:160],
                    "status": str(item.get("status") or "pending"),
                    "summary": str(item.get("summary") or item.get("reason") or "")[:300],
                    "expires_at": item.get("expires_at") or item.get("due_iso"),
                    "authoritative_ref": str(item.get("authoritative_ref") or f"decision:{dec_id}"),
                    "authoritative_url": auth_url,
                    "actions": [
                        {"kind": "link", "label": "Открыть решение", "href": auth_url},
                    ],
                }
            )
        )
    return cards


def build_brief_card(items: list[dict] | None = None) -> dict | None:
    """Project a brief inline card summarizing top attention items linked to owning routes."""
    if not items:
        return None
    safe_items = []
    for item in items[:5]:
        if not isinstance(item, dict):
            continue
        safe_items.append(
            {
                "title": str(item.get("title") or "")[:160],
                "reason": str(item.get("reason") or "")[:200],
                "priority": str(item.get("priority") or "medium"),
                "authoritative_url": str(item.get("route") or item.get("authoritative_url") or "/app/"),
                "authoritative_ref": str(item.get("authoritative_ref") or item.get("key") or ""),
            }
        )
    if not safe_items:
        return None
    return sanitize_card_payload(
        {
            "id": "brief:today",
            "card_type": "brief",
            "title": "Утренняя сводка",
            "status": "ready",
            "summary": f"Сигналов, требующих внимания: {len(safe_items)}",
            "items": safe_items,
            "authoritative_ref": "workspace:today",
            "authoritative_url": "/app/?stay=1",
            "actions": [
                {"kind": "link", "label": "Открыть «Сегодня»", "href": "/app/?stay=1"},
            ],
        }
    )


@router.get("/cards")
async def workspace_cards(
    request: Request,
    task_id: int | None = Query(None),
    context_type: str | None = Query(None),
    context_id: str | None = Query(None),
    boundary: str | None = Query(None),
):
    """Refresh typed inline cards from owning services on open; fail closed on revoked access."""
    denied = chat_panel_denied(request)
    if denied is not None:
        return denied

    cards: list[dict] = []
    if task_id is not None:
        task_gate = workspace_denied(request)
        if task_gate is not None:
            return task_gate
        if task_id <= 0:
            return JSONResponse({"error": "Задача недоступна", "cards": []}, status_code=404)
        try:
            definition = await selected_task(request, task_id)
            if definition is None:
                return JSONResponse({"error": "Задача недоступна", "cards": []}, status_code=404)
            from app.core.tenant_context import tenant_scope
            from app.services.task_results import task_result

            with tenant_scope(request.state.tenant_id):
                result = await task_result(task_id, limit=3)
        except PermissionError:
            return JSONResponse({"error": "Доступ к задаче изменился", "cards": []}, status_code=403)
        can_view_results = perms_can(request, "source", "view") and perms_can(request, "aianalytics", "view")
        cards.extend(build_task_cards(definition, result, can_view_results=can_view_results))

    if context_type or context_id or boundary:
        from fastapi import HTTPException

        from app.core.tenant_context import tenant_scope
        from app.services.knowledge.workspace import resolve_context

        try:
            with tenant_scope(request.state.tenant_id):
                resolved = await resolve_context(context_type, context_id, boundary or "cloud")
        except HTTPException as exc:
            return JSONResponse({"error": str(exc.detail), "cards": []}, status_code=exc.status_code)
        except PermissionError:
            return JSONResponse({"error": "Доступ к контексту изменился", "cards": []}, status_code=403)
        cards.extend(build_context_cards(resolved))

    return JSONResponse(
        {"cards": cards},
        headers={"Cache-Control": "no-store"},
    )


@router.get("/panel")
async def workspace_panel(
    request: Request,
    task_id: int | None = Query(None),
    context_type: str | None = Query(None),
    context_id: str | None = Query(None),
    boundary: str | None = Query(None),
):
    """Return data for the 4 right-panel tabs: Контекст, Ждут решения, В работе, Материалы."""
    denied = chat_panel_denied(request)
    if denied is not None:
        return denied

    tenant_id = request.state.tenant_id
    user = request.state.web_user
    can_view_tasks = perms_can(request, "agenttask", "view")
    can_view_results = perms_can(request, "source", "view") and perms_can(request, "aianalytics", "view")

    resolved_context = None
    context_error = None
    if context_type or context_id or boundary:
        try:
            from app.core.tenant_context import tenant_scope
            from app.services.knowledge.workspace import resolve_context

            with tenant_scope(tenant_id):
                resolved_context = await resolve_context(context_type, context_id, boundary or "cloud")
        except Exception:
            context_error = "Выбранный контекст больше недоступен или доступ к нему изменился."

    runs: list[dict] = []
    if can_view_tasks:
        try:
            from app.core.tenant_context import tenant_scope
            from app.models import AgentTask
            from app.services.task_results import task_result

            with tenant_scope(tenant_id):
                query = AgentTask.objects.filter(tenant_id=tenant_id, job_type__in=["collect", "analyze"])
                task_rows = await query.order_by(AgentTask.id.desc()).offset(0).limit(5)
                for row in task_rows[:5]:
                    res = await task_result(row.id, limit=1)
                    status = (res or {}).get("job_status", "unavailable")
                    progress = (res or {}).get("progress")
                    safe_prog = None
                    if isinstance(progress, dict):
                        safe_prog = {k: progress.get(k) for k in ("phase", "total", "completed", "updated_at")}
                    runs.append(
                        sanitize_card_payload(
                            {
                                "id": row.id,
                                "name": str(getattr(row, "name", f"Задача #{row.id}"))[:160],
                                "status": status,
                                "progress": safe_prog,
                                "task_url": f"/app/tasks/{row.id}",
                            }
                        )
                    )
        except Exception:
            runs = []

    decisions: list[dict] = []
    try:
        from app.agent.runtime import web_session_id
        from app.models import AgentSession

        session_id = await web_session_id(user.id)
        if session_id is not None:
            row = await AgentSession.objects.get(id=session_id)
            state = row.state if row is not None and isinstance(row.state, dict) else {}
            if isinstance(state.get("pending_confirmation"), dict):
                decisions.append(
                    {
                        "id": "session_confirmation",
                        "title": "Ожидает подтверждения действия в чате",
                        "summary": "Ответьте «да» или «нет» в диалоге для продолжения.",
                        "status": "pending",
                        "authoritative_ref": f"agent_session:{session_id}",
                        "authoritative_url": "/app/chat",
                    }
                )
            if isinstance(state.get("pending_attachment_confirmation"), dict):
                decisions.append(
                    {
                        "id": "attachment_confirmation",
                        "title": "Подтверждение крупного вложения",
                        "summary": "Подтвердите отправку крупного вложения в диалоге.",
                        "status": "pending",
                        "authoritative_ref": f"agent_session:{session_id}",
                        "authoritative_url": "/app/chat",
                    }
                )
    except Exception:
        pass

    context_key = None
    if task_id:
        context_key = f"agent_task:{task_id}"
    elif resolved_context:
        context_key = str(resolved_context.get("ref") or "")

    materials: list[dict] = []
    if resolved_context:
        materials.extend(build_context_cards(resolved_context))
    try:
        from app.services.chat_attachments import attachment_store

        staged = attachment_store.list_staged(tenant_id=tenant_id, user_id=user.id)
        for item in staged:
            pub = item.to_public_dict()
            materials.append(
                sanitize_card_payload(
                    {
                        "id": f"attachment:{pub.get('id')}",
                        "kind": "attachment",
                        "title": str(pub.get("filename") or "Файл")[:160],
                        "summary": str(pub.get("summary_label") or ""),
                        "status": "staged",
                        "authoritative_ref": f"attachment:{pub.get('id')}",
                        "authoritative_url": "/app/chat",
                    }
                )
            )
    except Exception:
        pass

    return JSONResponse(
        {
            "tabs": list(PANEL_TABS),
            "context": {
                "scoped_context": sanitize_card_payload(resolved_context) if resolved_context else None,
                "error": context_error,
            },
            "decisions": build_decision_cards(decisions, context_key=context_key),
            "runs": runs,
            "materials": materials,
            "can_view_results": can_view_results,
        },
        headers={"Cache-Control": "no-store"},
    )
