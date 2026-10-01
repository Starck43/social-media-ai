"""Learning-from-chat loop: fact extraction, memory hygiene, feedback signal.

Design notes:
- "Learning" is preference accumulation, not fine-tuning: the `learn` job
  extracts durable facts from recent chat turns and stores them in
  `agent_memory` with provenance (source='learn', confidence, evidence
  message id). The memory snapshot is injected into the system prompt, so the
  next conversation already behaves differently.
- `learn` is watermark-driven (scope='meta', key='learn_msg_wm'): it fires
  only after at least `min_messages` new user turns, otherwise it exits
  without an LLM call (cheap hourly cron).
- `reflect` (weekly) dedups/repairs memory from provenance + /bad notes and
  may *propose* prompt changes, but never applies them by itself.
- All functions must run inside `tenant_scope(...)`.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

_WM_KEY = "learn_msg_wm"
_META_SCOPE = "meta"
_MAX_FACTS_PER_RUN = 8


async def get_watermark() -> int:
    from app.models.managers.agent_memory_manager import agent_memory

    raw = await agent_memory.read(_WM_KEY, scope=_META_SCOPE)
    try:
        return int(raw or 0)
    except ValueError:
        return 0


async def set_watermark(message_id: int) -> None:
    from app.models.managers.agent_memory_manager import agent_memory

    await agent_memory.write(_WM_KEY, str(message_id), scope=_META_SCOPE, source="learn")


def extract_json(text: str) -> Optional[Any]:
    """Parse the first JSON object/array from an LLM reply (fences tolerated)."""
    if not text:
        return None
    candidates = [text.strip()]
    candidates += re.findall(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    match = re.search(r"[\[{].*[\]}]", text, re.DOTALL)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def _render_transcript(rows: list[Any], max_chars: int = 12000) -> str:
    lines = []
    total = 0
    for row in rows:
        if row.role not in ("user", "assistant") or not (row.content or "").strip():
            continue
        line = f"[{row.id}] {row.role}: {row.content.strip()[:500]}"
        total += len(line)
        if total > max_chars:
            break
        lines.append(line)
    return "\n".join(lines)


def _clamp_confidence(value: Any) -> float:
    try:
        return max(0.1, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.5


async def run_learn(min_messages: int = 8, window: int = 200) -> dict[str, Any]:
    """Extract durable facts/preferences from new chat turns into agent_memory.

    Returns the priced USD cost of the LLM call as ``llm_cost`` (0.0 when no
    call was made) so the job dispatcher can persist it for the daily cap.
    """
    from app.models import AgentMessage
    from app.models.managers.agent_memory_manager import agent_memory

    watermark = await get_watermark()
    rows = list(
        await AgentMessage.objects.filter(AgentMessage.id > watermark).order_by(AgentMessage.id.asc()).limit(window)
    )
    new_user_turns = sum(1 for r in rows if r.role == "user")
    if new_user_turns < max(1, int(min_messages)):
        return {"status": "skipped", "new_user_messages": new_user_turns, "required": min_messages, "llm_cost": 0.0}

    transcript = _render_transcript(rows)
    if not transcript:
        await set_watermark(max(r.id for r in rows) if rows else watermark)
        return {"status": "skipped", "reason": "empty transcript", "llm_cost": 0.0}

    from app.services.tenancy.resolver import current_daily_cost_limit, daily_cost_today

    limit = await current_daily_cost_limit()
    if limit and await daily_cost_today() >= limit:
        logger.warning("Daily LLM cost cap reached — learn skipped")
        return {"status": "skipped", "reason": "cost_cap", "llm_cost": 0.0}

    from app.services.ai.llm_client import chat_with_fallback

    known = await agent_memory.as_dict()
    messages = [
        {
            "role": "system",
            "content": (
                "Ты — модуль обучения персонального агента. Из диалога владельца с агентом "
                "извлеки УСТОЙЧИВЫЕ факты и предпочтения владельца (как он любит отчёты, "
                "что важно, стиль, часы тишины, о каких проектах/ресурсах он заботится). "
                "Не извлекай разовые запросы и догадки. Верни JSON без пояснений: "
                '{"facts": [{"key": "snake_case", "value": "кратко по-русски", '
                '"confidence": 0.0-1.0, "evidence_id": <id сообщения-основания>}]}. '
                "Максимум 8 фактов; если значимого нет — пустой список."
            ),
        },
        {
            "role": "user",
            "content": f"Уже известное (не дублировать):\n{json.dumps(known, ensure_ascii=False)}\n\nДиалог:\n{transcript}",
        },
    ]
    try:
        response = await chat_with_fallback(messages, max_tokens=800, temperature=0.2)
    except Exception as e:  # noqa: BLE001
        logger.error(f"learn: LLM call failed: {e}")
        return {"status": "failed", "error": str(e), "llm_cost": 0.0}

    llm_cost = float((response.get("usage") or {}).get("cost") or 0.0)
    parsed = extract_json(response.get("content") or "")
    facts = parsed.get("facts") if isinstance(parsed, dict) else None
    valid_ids = {r.id for r in rows}
    stored = 0
    if isinstance(facts, list):
        from app.models.managers.agent_memory_manager import agent_memory as mem

        for fact in facts[:_MAX_FACTS_PER_RUN]:
            if not isinstance(fact, dict):
                continue
            key = str(fact.get("key") or "").strip().lower().replace(" ", "_")[:100]
            value = str(fact.get("value") or "").strip()[:500]
            if not key or not value:
                continue
            evidence = fact.get("evidence_id")
            await mem.write(
                key,
                value,
                source="learn",
                confidence=_clamp_confidence(fact.get("confidence")),
                evidence_message_id=evidence if isinstance(evidence, int) and evidence in valid_ids else None,
            )
            stored += 1

    if rows:
        await set_watermark(max(r.id for r in rows))
    return {"status": "ok", "facts_stored": stored, "scanned_messages": len(rows), "llm_cost": llm_cost}


async def run_reflect(dedup: bool = True) -> dict[str, Any]:
    """Weekly hygiene: merge stale/contradicting facts, weigh them by feedback.

    Proposes prompt-evolution advice from /bad notes but applies nothing by itself.
    Returns the priced USD cost of the LLM call as ``llm_cost`` (0.0 when no
    call was made) so the job dispatcher can persist it for the daily cap.
    """
    from app.models import AgentMemory
    from app.models.managers.agent_feedback_manager import agent_feedback
    from app.models.managers.agent_memory_manager import agent_memory

    facts = list(await AgentMemory.objects.filter(AgentMemory.scope == "global"))
    notes = await agent_feedback.recent_notes("bad", limit=20)
    if not facts and not notes:
        return {"status": "skipped", "reason": "nothing to reflect on", "llm_cost": 0.0}

    from app.services.tenancy.resolver import current_daily_cost_limit, daily_cost_today

    limit = await current_daily_cost_limit()
    if limit and await daily_cost_today() >= limit:
        logger.warning("Daily LLM cost cap reached — reflect skipped")
        return {"status": "skipped", "reason": "cost_cap", "llm_cost": 0.0}

    facts_payload = [
        {
            "id": f.id,
            "key": f.key,
            "value": f.value,
            "source": f.source,
            "confidence": f.confidence,
            "updated_at": f.updated_at.date().isoformat() if f.updated_at else None,
        }
        for f in facts
    ]
    notes_payload = [{"vote": n.vote, "note": (n.note or "")[:300]} for n in notes]

    from app.services.ai.llm_client import chat_with_fallback

    messages = [
        {
            "role": "system",
            "content": (
                "Ты — модуль рефлексии персонального агента. Перед тобой список фактов "
                "о владельце (с provenance) и свежие негативные замечания к ответам. "
                "Предложи гигиену памяти: слить дубли, удалить устаревшее/ошибочное, "
                "понизить confidence спорного. Верни JSON без пояснений: "
                '{"ops": [{"op": "delete", "id": N, "reason": "..."}, '
                '{"op": "update", "id": N, "value": "...", "confidence": 0.0-1.0, "reason": "..."}], '
                '"prompt_advice": "что изменить в промптах задач/стиля по замечаниям (или пусто)"}'
            ),
        },
        {
            "role": "user",
            "content": json.dumps({"facts": facts_payload, "bad_notes": notes_payload}, ensure_ascii=False),
        },
    ]
    try:
        response = await chat_with_fallback(messages, max_tokens=900, temperature=0.1)
    except Exception as e:  # noqa: BLE001
        logger.error(f"reflect: LLM call failed: {e}")
        return {"status": "failed", "error": str(e), "llm_cost": 0.0}

    llm_cost = float((response.get("usage") or {}).get("cost") or 0.0)
    parsed = extract_json(response.get("content") or "")
    ops = parsed.get("ops") if isinstance(parsed, dict) else None
    advice = (parsed.get("prompt_advice") or "") if isinstance(parsed, dict) else ""
    fact_ids = {f.id for f in facts}
    applied = {"deleted": 0, "updated": 0}

    if dedup and isinstance(ops, list):
        for op in ops:
            if not isinstance(op, dict) or op.get("id") not in fact_ids:
                continue
            if op.get("op") == "delete":
                await agent_memory.delete_by_id(op["id"])
                applied["deleted"] += 1
            elif op.get("op") == "update" and str(op.get("value") or "").strip():
                await agent_memory.update_by_id(
                    op["id"],
                    value=str(op["value"]).strip()[:500],
                    confidence=_clamp_confidence(op.get("confidence")),
                    source="reflect",
                )
                applied["updated"] += 1

    return {
        "status": "ok",
        "facts": len(facts),
        "bad_notes": len(notes),
        **applied,
        "prompt_advice": advice[:500],
        "llm_cost": llm_cost,
    }
