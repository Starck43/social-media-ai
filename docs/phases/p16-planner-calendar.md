# P16 — Planner tasks and calendar sync (TZ module 3)

[Product direction](../product/PRODUCT_PLAN.md#business-modules) · [Adapted specification and review](../product/BUSINESS_MODULES.md) · [ROADMAP](../../ROADMAP.md#p16)

**Phase status: PLANNED** (registered by [P13-T01 accepted](../audit/p13/p13-t01.md); ROADMAP owns task status).
Owner estimate (TZ): 4–5 weeks — not a repository commitment.

**Goal:** Personal and team tasks with priorities, reminders and recurrence, plus two-way sync with Google Calendar and Yandex Calendar and automatic planning of contact events.

## Baseline: done and arriving

- In `dev`: notifications service and preferences, follow-up quiet hours (P12-T04), runtime scheduler. No user to-do model or calendar integration.
- Name collision: `agent_tasks`/`/api/v1/tasks`/`/app/tasks` are agent jobs; this phase uses the `planner` namespace (R-03).

## Adapted requirements

TZ 3.1–3.5, adapted:

- **Tasks:** title, description, due date, priority low/medium/high/critical, categories/tags, contact link, subtasks, recurrence, status todo/in_progress/done/cancelled (P16-T01, [../audit/p16-t01-p15-t09.md](../audit/p16/p16-t01-p15-t09.md)).
- **Reminders:** 1 day/week/month and custom, TG/MAX/email, snooze, reschedule, AI suggestions (P16-T02, [../audit/p16-t02.md](../audit/p16/p16-t02.md)).
- **Google Calendar:** OAuth, two-way, events from tasks, import, conflict resolution, 5-min sync ([P16-T03 accepted](../audit/p16/p16-t03.md), 2026-11-09; source `dd7571ebf940c6e2eecb2e3903373ec13858dcee`).
- **Yandex Calendar:** same behaviour over CalDAV (R-06, [P16-T04 accepted](../audit/p16/p16-t04.md), 2026-11-09; source `dd7571ebf940c6e2eecb2e3903373ec13858dcee`).
- **Event planning:** birthdays from contacts, auto tasks, templates, link to greeting drafts (P16-T05, [../audit/p16-t05.md](../audit/p16/p16-t05.md)).

## Data model (adapted)

`tasks` → `planner_tasks`; `task_reminders` → `planner_reminders`; `calendar_connections` → `workspace_connections` + secrets; `calendar_events` kept (encrypted personal fields, etag, unique per connection/external ID); conflict records.

Conventions: integer keys, `TenantScopedMixin`, `settings.DB_SCHEMA`, Alembic revision = next free after rebasing on `dev` (R-01, R-20).

## API (adapted)

`/api/v1/planner/tasks…` (instead of `/api/v1/tasks`), `/api/v1/planner/tasks/{id}/reminders`, `/api/v1/planner/reminders/{id}/snooze`; calendar routes as in TZ under `/api/v1/calendar/…`.

## Acceptance

TZ criteria: tasks with deadlines/priorities (T01); reminders 1 day/week ahead (T02); multi-channel notifications (T02); Google two-way (T03); Yandex two-way (T04); conflict resolution (T03/T04); recurring tasks (T01); subtasks (T01); birthday tasks (T05); snooze/reschedule (T02).

## Plan

1. P16-T01 — Planner tasks: model, CRUD, subtasks and recurrence · `P3` · `C4` · depends: [P13-T05 accepted](../audit/p13/p13-t05.md), [P13-T07 accepted](../audit/p13/p13-t07.md) (contact link)
2. P16-T02 — Reminders, snooze and multi-channel notifications · `P3` · `C3` · depends: P16-T01 accepted
3. P16-T03 — Calendar connections and Google Calendar two-way sync · `P3` · `C5` · [accepted 2026-11-09](../audit/p16/p16-t03.md), source `dd7571ebf940c6e2eecb2e3903373ec13858dcee`
4. P16-T04 — Yandex Calendar two-way sync over CalDAV · `P3` · `C4` · [accepted 2026-11-09](../audit/p16/p16-t04.md), source `dd7571ebf940c6e2eecb2e3903373ec13858dcee`
5. P16-T05 — Event planning: birthday tasks and templates · `P3` · `C3` · depends: P16-T01 accepted, [P14-T04 accepted](../audit/p13/p13-t04-p14-t04-business-crm.md)

## Dependencies and gates

Calendar writes follow D4. Google/Yandex test accounts are optional owner-provided sandboxes; CI uses mocks.
