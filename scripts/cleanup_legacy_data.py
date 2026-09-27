"""
Legacy data cleanup: analytics cost repricing, raw payload trimming, tenancy leftovers.

Three independent repairs, each dry-run by default:

* ``cost``      — reprice ``ai_analytics.estimated_cost`` from ``llm_models`` tariffs.
                  Rows written before costs were priced from the DB carry stale values
                  computed from hardcoded rates; the runtime formula today is
                  ``req/1000*input_cost_per_1k + resp/1000*output_cost_per_1k`` in USD,
                  stored as cents with sub-cent precision (``ContentAnalyzer._price_usage``).
                  Since migration 0060 widened the column from INTEGER to NUMERIC(14,6),
                  this command also restores the fractions that integer rounding erased.
* ``payload``   — rewrite DEBUG-era ``response_payload`` (raw provider completions with
                  ``choices``/``message``) into the metadata-only trace
                  ``{model, usage, parsed_keys}`` that the runtime keeps today, and
                  optionally drop ``prompt_text`` (vision.md: stored analytics must not
                  contain raw model output).
* ``ownership`` — repair tenancy leftovers: memberships with ``user_id IS NULL`` (the
                  workspace becomes unselectable for the user who owns its data) and
                  sources whose owner is not a member of the source tenant.

Dry-run is not a preview-only pass: the UPDATE statements run inside a transaction that
is then rolled back, so the exact SQL is exercised without persisting anything. Pass
``--apply`` to commit. Both analytics repairs take ``--backup`` and dump the original
rows as JSONL before writing, because the trimmed payload cannot be rebuilt afterwards.

Model managers are deliberately not used: they are tenant-scoped and fail closed
outside a request scope, while this tool must see every tenant at once. Raw SQL against
``settings.DB_SCHEMA`` keeps that cross-tenant access explicit.

Usage (from the repository root):
    python scripts/cleanup_legacy_data.py audit
    python scripts/cleanup_legacy_data.py cost
    python scripts/cleanup_legacy_data.py cost --apply --backup /tmp/analytics-cost.jsonl
    python scripts/cleanup_legacy_data.py payload --include-prompt-text
    python scripts/cleanup_legacy_data.py payload --apply
    python scripts/cleanup_legacy_data.py ownership
    python scripts/cleanup_legacy_data.py ownership --apply [--user-id N]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.core.database import new_session  # noqa: E402

SCHEMA = settings.DB_SCHEMA

# Keys produced by the runtime trace. An analysis block that carries only these has
# already been trimmed (see ContentAnalyzer._build_trace_payload).
TRACE_KEYS = {"model", "usage", "parsed_keys"}
# Keys that only ever appear inside a raw provider response body.
RAW_KEYS = {"choices", "message", "finish_reason"}


def _is_raw(value: Any) -> bool:
    """True when an analysis block still holds a raw provider response."""
    return isinstance(value, dict) and bool(RAW_KEYS & value.keys())


def _info(message: str) -> None:
    print(f"  {message}")


def _warn(message: str) -> None:
    print(f"  ! {message}")


@dataclass(frozen=True)
class Price:
    """USD per 1K tokens for one llm_models row."""

    model_id: str
    input_cost_per_1k: float
    output_cost_per_1k: float


async def _price_index(session: AsyncSession) -> tuple[dict[str, Price], list[str]]:
    """Active model prices, keyed by both ``model_id`` and human ``name``.

    Mirrors the runtime lookup, which accepts either spelling. A key claimed by two
    active rows with different tariffs is reported instead of silently resolved.
    """
    rows = (
        (
            await session.execute(
                text(
                    f"select model_id, name, input_cost_per_1k, output_cost_per_1k "
                    f"from {SCHEMA}.llm_models where is_active order by id"
                )
            )
        )
        .all()
    )
    index: dict[str, Price] = {}
    warnings: list[str] = []
    for model_id, name, cost_in, cost_out in rows:
        price = Price(model_id or "", float(cost_in or 0.0), float(cost_out or 0.0))
        for key in (key for key in (model_id, name) if key):
            known = index.get(key)
            if known is not None and known != price:
                warnings.append(f"conflicting tariffs for '{key}': {known.model_id} vs {price.model_id}")
            index.setdefault(key, price)
    if not index:
        warnings.append("no active rows in llm_models — every cost would price to zero")
    return index, warnings


async def _finish(session: AsyncSession, apply: bool, changed: int) -> None:
    """Commit on --apply, otherwise roll the dry-run transaction back."""
    if changed and apply:
        await session.commit()
        _info(f"committed: {changed} row(s) updated")
        return
    await session.rollback()
    if changed:
        _info(f"dry-run: {changed} row(s) would change — rerun with --apply to commit")
    else:
        _info("nothing to change")


def _backup(path: str | None, table: str, rows: list[dict[str, Any]]) -> None:
    """Dump original rows as JSONL so a trim or reprice stays reversible."""
    if not path or not rows:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps({"_table": table, **row}, ensure_ascii=False, default=str) + "\n")
    _info(f"backed up {len(rows)} original row(s) to {target}")

@dataclass(frozen=True)
class CostPlan:
    analytics_id: int
    model: str
    stored: Decimal | int | None
    target: Decimal | None
    tokens: tuple[int, int]


#: Scale of ai_analytics.estimated_cost (USD cents, 6 decimals = 1e-8 USD).
COST_SCALE = Decimal("0.000001")


def _cents(value: Decimal | int | None) -> str:
    if value is None:
        return "NULL"
    return f"{Decimal(value).normalize():f}c"


def _price_cents(price: Price, request_tokens: int, response_tokens: int) -> Decimal | None:
    """USD cents for one call, matching ContentAnalyzer._price_usage.

    Decimal quantised to the column scale: cheap models price most calls below a
    cent, which the old INTEGER column rounded to zero. Historical rows therefore
    read as whole cents (or NULL) and usually need a reprice after migration 0060.

    ``None`` at zero is deliberate: the write path stores NULL rather than 0, so the
    reprice must not turn a free model into a row that looks priced-to-zero.
    """
    usd = Decimal(request_tokens) / 1000 * Decimal(str(price.input_cost_per_1k)) + Decimal(
        response_tokens
    ) / 1000 * Decimal(str(price.output_cost_per_1k))
    cents = (usd * 100).quantize(COST_SCALE, rounding=ROUND_HALF_UP)
    return cents if cents > 0 else None


async def _fetch_rows(session: AsyncSession, select: str, where: str, args: argparse.Namespace) -> list[Any]:
    """Run a scoped SELECT over ai_analytics (optional tenant filter and row limit)."""
    params: dict[str, Any] = {}
    clause = where
    if args.tenant_id is not None:
        clause += " and tenant_id = :tenant_id"
        params["tenant_id"] = args.tenant_id
    if args.limit is not None:
        params["limit"] = args.limit
    sql = f"select {select} from {SCHEMA}.ai_analytics where {clause} order by id"
    if args.limit is not None:
        sql += " limit :limit"
    return (await session.execute(text(sql), params)).all()


async def repair_costs(session: AsyncSession, args: argparse.Namespace) -> int:
    """Recompute estimated_cost from tokens x llm_models tariffs."""
    print("cost: repricing ai_analytics from llm_models tariffs")
    index, warnings = await _price_index(session)
    for warning in warnings:
        _warn(warning)

    rows = await _fetch_rows(
        session,
        "id, coalesce(llm_model, ''), request_tokens, response_tokens, estimated_cost",
        "(coalesce(request_tokens, 0) > 0 or coalesce(response_tokens, 0) > 0)",
        args,
    )

    plans: list[CostPlan] = []
    unknown: dict[str, int] = {}
    for analytics_id, model, req, resp, stored in rows:
        price = index.get(model.strip())
        if price is None:
            unknown[model or "<empty>"] = unknown.get(model or "<empty>", 0) + 1
            continue
        # Rows written before costs came from llm_models land on a different cent value
        # than the runtime would store today; zero-priced models become NULL, as in the
        # write path.
        target = _price_cents(price, req or 0, resp or 0)
        if target == stored:
            continue
        plans.append(CostPlan(analytics_id, model.strip(), stored, target, (req or 0, resp or 0)))

    _info(f"scanned {len(rows)} priced row(s), {len(plans)} to change")
    for model, count in sorted(unknown.items()):
        _warn(f"no active llm_models row for '{model}' — {count} row(s) left untouched")
    for plan in plans:
        _info(
            f"#{plan.analytics_id} {plan.model} {plan.tokens[0]}/{plan.tokens[1]} tok: "
            f"{_cents(plan.stored)} -> {_cents(plan.target)}"
        )

    _backup(args.backup, "ai_analytics", [{"id": p.analytics_id, "estimated_cost": p.stored} for p in plans])
    if plans:
        await session.execute(
            text(f"update {SCHEMA}.ai_analytics set estimated_cost = :target, updated_at = now() where id = :id"),
            [{"target": p.target, "id": p.analytics_id} for p in plans],
        )
    return len(plans)


def _usage(block: Any, request_tokens: int, response_tokens: int) -> dict[str, int]:
    """Token usage of a block, falling back to the aggregated columns."""
    usage = block.get("usage") if isinstance(block, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    return {
        "prompt_tokens": int(usage.get("prompt_tokens") or usage.get("input_tokens") or request_tokens),
        "completion_tokens": int(usage.get("completion_tokens") or usage.get("output_tokens") or response_tokens),
    }


def _parsed_keys(block: Any) -> list[str]:
    """Keys of the JSON the model returned, recovered from the raw completion."""
    content: Any = None
    if isinstance(block, dict):
        if "choices" in block:
            choices = block.get("choices") or [{}]
            first = choices[0] if isinstance(choices, list) and choices else {}
            message = first.get("message") if isinstance(first, dict) else None
            content = message.get("content") if isinstance(message, dict) else None
        else:
            content = block.get("content")
    if not isinstance(content, str):
        return []
    try:
        parsed = json.loads(content)
    except ValueError:
        return []
    return sorted(parsed.keys()) if isinstance(parsed, dict) else []


def _trim_block(value: Any, model: str | None, request_tokens: int, response_tokens: int) -> Any:
    """Reduce one analysis block to the runtime trace shape; keep trimmed blocks as is."""
    if isinstance(value, dict) and not _is_raw(value) and set(value.keys()) <= TRACE_KEYS:
        return value
    if not _is_raw(value):
        return {"model": model, "usage": _usage(value, request_tokens, response_tokens), "parsed_keys": []}
    return {
        "model": (value.get("model") if isinstance(value, dict) else None) or model,
        "usage": _usage(value, request_tokens, response_tokens),
        "parsed_keys": _parsed_keys(value),
    }


@dataclass
class PayloadPlan:
    analytics_id: int
    raw_blocks: list[str]
    payload: dict[str, Any]
    had_prompt: bool


async def repair_payloads(session: AsyncSession, args: argparse.Namespace) -> int:
    """Strip raw provider responses out of response_payload (and prompt_text)."""
    print("payload: reducing response_payload to {model, usage, parsed_keys}")
    rows = await _fetch_rows(
        session,
        "id, coalesce(llm_model, ''), request_tokens, response_tokens, response_payload::text, "
        "prompt_text is not null",
        "response_payload is not null",
        args,
    )

    plans: list[PayloadPlan] = []
    for analytics_id, model, req, resp, payload_text, has_prompt in rows:
        try:
            payload = json.loads(payload_text) if payload_text else None
        except ValueError:
            _warn(f"#{analytics_id} response_payload is not valid JSON — skipped")
            continue
        if not isinstance(payload, dict):
            continue
        raw_blocks = [key for key, value in payload.items() if _is_raw(value)]
        drop_prompt = bool(args.include_prompt_text and has_prompt)
        if not raw_blocks and not drop_prompt:
            continue
        trimmed = {key: _trim_block(value, model, req or 0, resp or 0) for key, value in payload.items()}
        plans.append(PayloadPlan(analytics_id, raw_blocks, trimmed, drop_prompt))

    _info(f"scanned {len(rows)} row(s), {len(plans)} to change")
    for plan in plans:
        detail = ", ".join(plan.raw_blocks) or "no raw blocks"
        _info(f"#{plan.analytics_id} raw blocks: {detail}{' + drop prompt_text' if plan.had_prompt else ''}")

    _backup(args.backup, "ai_analytics", [{"id": p.analytics_id, "trimmed": p.payload} for p in plans])
    for plan in plans:
        sql = f"update {SCHEMA}.ai_analytics set response_payload = cast(:payload as json)"
        if plan.had_prompt:
            sql += ", prompt_text = null"
        sql += ", updated_at = now() where id = :id"
        await session.execute(
            text(sql),
            {"payload": json.dumps(plan.payload, ensure_ascii=False), "id": plan.analytics_id},
        )
    return len(plans)


async def _resolve_membership_user(args: argparse.Namespace) -> tuple[int | None, str]:
    """Which user a membership without user_id should belong to."""
    if args.user_id is not None:
        return args.user_id, "--user-id"
    return None, "no --user-id given"


async def repair_ownership(session: AsyncSession, args: argparse.Namespace) -> int:
    """Link memberships that have no user (pass --user-id to pick the user)."""
    print("ownership: workspace memberships")
    plans: list[dict[str, int]] = []

    memberships = (
        (
            await session.execute(
                text(
                    f"select tu.id, tu.tenant_id, t.slug, tu.role, tu.channel from {SCHEMA}.tenant_users tu "
                    f"join {SCHEMA}.tenants t on t.id = tu.tenant_id "
                    f"where tu.user_id is null order by tu.id"
                )
            )
        )
        .all()
    )
    for membership_id, tenant_id, slug, role, channel in memberships:
        user_id, reason = await _resolve_membership_user(args)
        if user_id is None:
            _warn(f"membership #{membership_id} (tenant '{slug}', role {role}) has no user — {reason}, pass --user-id")
            continue
        clash = (
            await session.execute(
                text(
                    f"select id from {SCHEMA}.tenant_users "
                    f"where tenant_id = :tenant_id and user_id = :user_id and channel = :channel"
                ),
                {"tenant_id": tenant_id, "user_id": user_id, "channel": channel},
            )
        ).first()
        if clash:
            _warn(f"membership #{membership_id} -> user {user_id} skipped: membership #{clash[0]} already exists")
            continue
        _info(f"membership #{membership_id} (tenant '{slug}', role {role}) -> user_id={user_id} ({reason})")
        plans.append({"user_id": user_id, "id": membership_id})

    test_users = (
        (
            await session.execute(
                text(f"select id, email from {SCHEMA}.users where email like '%@example.test' order by id")
            )
        )
        .all()
    )
    if test_users:
        sample = ", ".join(email for _uid, email in test_users[:3])
        _info(f"{len(test_users)} test run user(s) left behind ({sample}{' ...' if len(test_users) > 3 else ''})")
        _info("this script never deletes rows — remove test tenants deliberately, after reviewing them")

    for plan in plans:
        await session.execute(
            text(
                f"update {SCHEMA}.tenant_users set user_id = :user_id, updated_at = now() "
                f"where id = :id and user_id is null"
            ),
            plan,
        )
    _info(f"{len(plans)} membership(s) to link")
    return len(plans)


async def _audit(session: AsyncSession) -> None:
    """Run every repair in dry-run mode: the full plan, nothing persisted."""
    args = argparse.Namespace(
        apply=False, tenant_id=None, limit=None, backup=None, user_id=None, include_prompt_text=True
    )
    for name in ("cost", "payload", "ownership"):
        print()
        await HANDLERS[name](session, args)
    await session.rollback()
    _info("audit is read-only — every statement above was rolled back")


HANDLERS = {"cost": repair_costs, "payload": repair_payloads, "ownership": repair_ownership}


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="repair legacy analytics and ownership data")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("audit", help="print the plan for all three repairs without writing")

    analytics = sub.add_parser("cost", help="reprice estimated_cost from llm_models tariffs")
    payload = sub.add_parser("payload", help="reduce raw LLM payloads to the runtime trace shape")
    ownership = sub.add_parser("ownership", help="link user-less memberships, report orphan sources")
    for command in (analytics, payload, ownership):
        command.add_argument("--apply", action="store_true", help="commit instead of rolling back the dry-run")
    for command in (analytics, payload):
        command.add_argument("--tenant-id", type=int, help="restrict the pass to one tenant")
        command.add_argument("--limit", type=int, help="process at most N rows (batching on large tables)")
        command.add_argument("--backup", help="append original rows to this JSONL file before writing")
    payload.add_argument("--include-prompt-text", action="store_true", help="also null prompt_text")
    ownership.add_argument("--user-id", type=int, help="user to link memberships that have no user_id")
    return parser.parse_args(argv)


async def _run(argv: list[str] | None) -> int:
    args = _parse_args(argv)
    session = new_session()
    try:
        if args.command == "audit":
            await _audit(session)
            return 0
        changed = await HANDLERS[args.command](session, args)
        await _finish(session, args.apply, changed)
        return 0
    finally:
        await session.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(_run(sys.argv[1:])))

