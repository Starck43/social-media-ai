# Workspace experience and information architecture

Target design recorded by [P20-T01](tasks/p20-t01.md) at the owner's request
(2026-11-05). It is **not shipped**: every change lands through its own task and
acceptance. Current navigation is data in `app/web/nav.py`; current chat behavior
is in [AGENT](AGENT.md). Russian strings below are proposed UI labels, except
where owner decisions are recorded below.

## Principles

1. **Four daily entry points.** «Сегодня» answers *what needs me now*; «Чат с
   агентом» lets a user ask, delegate and decide in words; «Входящие» and «Задачи»
   are where correspondence and work items are handled. Monitoring, knowledge,
   CRM/1C and settings are specialist or setup areas reached from these or from
   the sidebar.
2. **Chat is the primary interaction, not the only place.** Contact cards,
   calendar, documents and inbox work without the agent; the agent sits beside
   them through context handoff ([P12-T06](tasks/p12-t06.md)). Nothing is
   available only through a prompt.
3. **The UI may join, storage may not** ([Q-20 D23](audit/q-20-notification-surface.md),
   [Q-21 D24/D25](audit/q-21-source-authority.md)). «Сегодня», the unified task
   list and the calendar view are read-time projections over the owning modules.
   Our own stored state is only what we decided: dismissed, snoozed, seen.
4. **One decision, one authority.** An approve/reject control anywhere (Today,
   chat card, inbox, document, bot) calls the owning ledger with the exact item;
   no surface keeps its own approval state.
5. **Personal by default, team on demand.** Lists default to «Моё»; «Команда»
   widens them only within the actor's permissions ([TENANCY](TENANCY.md#roles-and-permissions)).
6. **Honest states.** Unavailable source, partial coverage, low confidence,
   pending, failed and unknown outcomes are shown, never rendered as empty truth.
7. **Progressive disclosure.** Queue, action log, audit, models and operator
   tools sit under «Система» for roles that have them; daily users do not need them.

## Target navigation

| Group | Item | Owner / current route | Notes |
| --- | --- | --- | --- |
| Работа | Сегодня | Current dashboard `/app/`, [P20-T07](audit/p20-t07.md) | Default landing; user can choose another in settings |
| Работа | Чат с агентом | `/app/chat`, [P20-T08](audit/p20-t08.md) | Same conversation as the Telegram/MAX bot |
| Работа | Входящие | `/app/communications` inbox, [P15-T04](tasks/p15-t04.md), triage [P20-T09 accepted](audit/p20-t09.md) | Inbound and outbound in one thread |
| Работа | Задачи | hub over `/app/planner` ([P16-T01](tasks/p16-t01.md)) and `/app/tasks`, [P20-T10 accepted](audit/p20-t10.md) | Tabs «Мои дела», «Поручения агенту», «Календарь» |
| Клиенты | Контакты, Договорённости, Предложения | `/app/contacts`, `/app/followups`, `/app/cases` | CRM/1C data appears inside these as context |
| Знания | База знаний, Документы, Файлы | `/app/knowledge`, `/app/documents`, file workspace | |
| Мониторинг | Источники, Сценарии, Аналитика, Дайджесты | existing routes | Specialist area of the first vertical |
| Система | Действия, Очередь, Настройки | existing routes | Role-gated; Settings holds connections, team, notifications, models, billing |

Rules:

- A module adds a sidebar item only for a list of its own objects that people
  open daily. Integrations (mail, messengers, Bitrix24, 1C, calendars, local
  connector, models) are connections in Settings and surface as context and
  sources, not as sidebar items.
- Pending decisions appear in «Сегодня», as a header badge and in chat; a
  a separate «На согласовании» page handles volume ([P20-T03 acceptance](audit/p20-t03.md)),
  not as another daily entry point.
- Mobile bar: «Сегодня», «Чат», «Входящие», «Задачи», «Ещё» (drawer). Chat is
  included because it is the primary workspace; decided by the owner on
  2026-11-07 (O-3, see below).

## Chat page

| Zone | Contents | Does not contain |
| --- | --- | --- |
| Header | Workspace and role line (existing), boundary badge (which model class will see the next message: cloud/company/local), clear history | Settings forms |
| Left | Global sidebar, collapsible to icons on this page | A second conversation list in v1 |
| Center | Messages and typed inline cards (below) | Full lists of inbox, tasks or calendar |
| Composer | Removable context chips (task, thread, contact, document, case), attachments, quick actions («Подготовить задачу», «Разобрать результат», «Что дальше?») | Hidden context the user cannot see |
| Right panel | Tabbed «Рабочий контекст», collapsible; a drawer below `lg` | Admin tables, global inbox |

**Inline cards** are rendered from owning services and refresh from them; each
links to its authoritative record: plan preview (run/edit), decision (exact
payload, expiry, approve/reject), progress (phase, cancel), result (summary,
evidence, coverage and limitations), draft (reply or document, opens in its
editor), brief (attention items).

**Right panel tabs** (at most four, state remembered):

1. «Контекст» — the selected object's summary, its own quick actions and «Открыть
   страницу». Extends today's task picker/current task to threads, contacts,
   documents and cases. Settings links («Источники», «Сценарии») move here or to
   empty states.
2. «Ждут решения» — the actor's pending decisions, filtered to the selected
   context when there is one.
3. «В работе» — running and recent agent runs and delegated steps with progress
   and cancel ([P20-T04](tasks/p20-t04.md)).
4. «Материалы» — citations/evidence of the last answer and attachments.

**Chat dock.** Every object page offers «Спросить агента», which opens the same
conversation as a slide-over with the object as a context chip. One conversation
per user and workspace stays the model; topic threads are a later, measured
decision.

## Today page

Section order: brief → «Ждут решения» → «По плану на сегодня» (planner due and
overdue, calendar events, scheduled agent runs) → «Ждут ответа» (inbox triage) →
«Требует внимания» (reconnect, failed or unknown delivery, budget limits) →
«Последние результаты». Empty or unavailable providers collapse with their reason.
Existing dashboard sections remain accessible. The user can select a start page
in settings; «Сегодня» is the default (O-2).

**Attention item projection** (read-time, no copy): provider and kind,
authoritative reference and route, redacted title, reason, recommended priority
with its basis, due/expiry, owner-reported state, allowed actions (links to the
owner's actions), data boundary and freshness. Message bodies are never copied
([D11](audit/q-19-messaging-decisions.md)). Each module contributes a provider
when its own task lands; [P20-T02](audit/p20-t02.md) defines the contract with
social results as the first provider.

## Correspondence

Connected mailboxes ([P15-T01](tasks/p15-t01.md)) and Telegram/MAX
([P15-T02](tasks/p15-t02.md); personal Telegram behind P12-T14) form threads per
contact. Personal VK messages need their own authorized capability; none is
planned. Flow: ingest metadata → thread → triage («Нужен ответ», «К сведению»,
«Подождёт», «Не уверен» with a reason) → the agent proposes a reply draft, a
planner task, a follow-up, a document or a CRM note → the user edits and approves
→ outbox ([P15-T05](tasks/p15-t05.md)) and senders ([P15-T06](audit/p15-t06.md))
→ delivery status in the same thread. The agent never sends without the owning
approval; notifying a workspace user never uses the outbox (D19).

## Tasks as the universal tool

One surface, two kinds kept separate in storage ([R-03](BUSINESS_MODULES.md#review-findings-corrections-to-the-tz)):

- **Planner task** — a human to-do with due date, priority, reminders,
  recurrence and links to a contact, thread or document; its source may be
  external (calendar, CRM, tracker) per D25.
- **Agent task** — an executable plan with preview, schedule and run history:
  social collection/analysis today; later message analysis, web research
  ([P20-T12 audit](audit/p20-t12.md)), 1C queries ([P17-T03 accepted](audit/p17-t03.md)), document
  drafts ([P19-T02](audit/p19-t02.md)) and delegated research.

«Поручить агенту» on a planner task creates a linked agent task; its result may
annotate or complete the planner task after the user confirms. The calendar is a
view over planner items, calendar events and scheduled runs, not a second store.
Tasks can be created from chat, a message, a document or «Сегодня».

## Team work

A workspace is the team space. Assignee, watchers and the «Моё / Команда» switch
use existing roles. Users choose their task service; Bitrix24 is the first
supported task source by owner choice ([P20-T11](audit/p20-t11.md)). The Tasks
hub shows assigned and watched items from the current user's authorized portal
connection when that connection has separate task-read consent. Values are read
live and linked to Bitrix24; unsupported services are never shown as connected.
Team scope does not broaden the provider identity. This increment is read-only;
future writes require command-ledger preview and approval. Mentions and reminders
use the one notification surface (D18).

## Documents, CRM and 1C

These open as context from a contact, case, thread or task; the agent works on
them through context chips. Writes use their existing previews and approvals
([P13-T04](audit/p13-t04-p14-t04-business-crm.md), [P17-T04](tasks/p17-t04.md),
[P19-T04](tasks/p19-t04.md)).

## Surface parity

Every user capability is one permission-checked service behind several surfaces.

| Surface | Required for | Notes |
| --- | --- | --- |
| Web page | Every end-user capability | Primary visual surface |
| Chat tool (web, Telegram, MAX) | Reads, previews and drafts an agent can usefully perform | Writes stop at the owning preview/approval |
| API `/api/v1` | Every web capability | Typed schemas, generated route tables |
| CLI | Operator, bulk, automation and every recovery operation | Same services, dry-run where the service has one |
| Admin | Operator configuration and inspection | Not an end-user workflow |
| Bot decision buttons | Later task | Until then the bot links to the web decision |
| Desktop | Future | See [PLATFORM_TOPOLOGY](PLATFORM_TOPOLOGY.md#access-clients) |

## Owner decisions and open questions

Source: [owner answers](https://app.notion.com/chat?t=40fa7457748980d094a100a930619250).

- **O-1 decided:** one «Задачи» section with «Мои дела», «Поручения агенту» and «Календарь». Coordinate with the active P16-T01 owner.
- **O-2 decided:** users choose the start page in settings; «Сегодня» is the default. Route and migration details are for implementation.
- **O-3 decided (owner, 2026-11-07):** the mobile bar is «Сегодня», «Чат», «Входящие» (when permitted and ready), «Задачи», «Ещё» — exactly as proposed above.
- **O-4 decided in principle:** users choose their own task service. The first supported connector and rollout order remain open; no universal provider mandated.
- **O-5 decided for now:** use a stub for web search; prioritize Yandex Search API for a future provider. Real-provider activation and spending have not been approved.
- **O-6 decided (owner, 2026-11-11):** enable a bounded public-web research step in the delegated task workflow. This resolves the P20-T12 ↔ P20-T04 circular gate: [P20-T12](audit/p20-t12.md) delivers an agent-task web-research step, while accepted [P20-T04](tasks/p20-t04.md) refuses such steps until P20-T12 is accepted. Bounds stay as scoped there: public pages only, no login, form submission or CAPTCHA handling, the O-5 stub remains clearly marked and is never presented as live evidence, no paid provider activation and no new spend. The step is admitted only when the web-research capability is available and under the existing delegation caps. The accepted v1 workflow keeps rejecting the step; the capability ships as the next workflow version, so pinning callers to v1 remains the rollback.
