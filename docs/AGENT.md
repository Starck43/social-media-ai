# Agent runtime

Персональный AI-агент в личном чате мессенджера (Telegram/MAX): инструменты,
диалог с памятью, подтверждения опасных операций.

## Как это работает

```
messenger message
  -> app/channels/listener.py        poll (long polling), per-channel
  -> app/agent/runtime.py            handle_inbound()
      1. resolve_inbound: chat -> tenant (bound chat / invite code / owner)
      2. tenant_scope(tenant_id): все менеджеры фильтруют по workspace
      3. active membership + bound User + chat -> RuntimeIdentity (deny if invalid)
      4. permission_scope(User): matching session -> LLM/tools; fresh rights per dispatch
      5. actor-bound confirmation -> fresh identity/right -> one-use tool approval
      6. ответ отправляется обратно в тот же чат
```

## Модули

| Модуль | Роль |
|---|---|
| `app/agent/runtime.py` | Шагающий цикл агента: сессия, лимиты, подтверждения, loop, сборка системного промпта (`build_system_prompt`) |
| `app/agent/prompts.py` | Системный промпт + текст `/help`; `render_style_block` (tenants.agent_style) |
| `app/agent/learning.py` | `run_learn` (факты из чата, watermark), `run_reflect` (гигиена памяти) |
| `app/agent/session.py` | Загрузка/нормализация истории для LLM |
| `app/agent/tools.py` | Реестр, схемы, проверка объявленного права и одноразового подтверждения |
| `app/agent/identity.py` | Активная identity в разрешённом tenant; загрузка User/roles/permissions, повторная проверка |
| `app/agent/confirmation.py` | Привязанные к actor/session intents и одноразовый in-process approval |
| `app/agent/toolset/` | Реализации тулов: `system`, `collect`, `sources`, `tasks`, `reports`, `actions`, `scenarios` |
| `app/models/agent_session.py` | Одна строка на (tenant, channel, chat_id); volatile `state` |
| `app/models/agent_message.py` | Транскрипт диалога (user/assistant/tool) + токены/стоимость |
| `app/models/agent_feedback.py` | Оценки `/good`,`/bad` (vote, note, сообщение-основание) |
| `app/models/agent_memory.py` | Факты в постоянной памяти: (tenant, scope, key) -> value + provenance |

## Инструменты

Все инструменты зарегистрированы в `TOOL_REGISTRY` в `app/agent/tools.py`
(импорт `toolset` срабатывает по побочному эффекту). Схемы уходят в LLM как
OpenAI function calling.

### Permission gates (prepared in draft PR #22)

Runtime допускает только активного участника с привязанным активным User и
согласованными tenant/channel/chat. Роли и права загружаются явно; authority
повторно читается перед инструментами. Весь turn находится в permission_scope.
Owner в runtime — только явная membership-роль SUPERUSER, не NULL и не env-флаг.
Owner override ограничен source/agenttask/agentscenario; глобальные права не даёт.

Оба registry-пути `call_tool`/`execute` проверяют `required_permission` до handler.
Отсутствующее объявление пока не означает полное покрытие: такие инструменты и
raw manager writes требуют отдельного аудита. Trusted service/operator scopes
не используются как замена интерактивной identity. `confirm=True` требует
одноразовый approval даже при прямом registry-вызове с правами.

### Confirmation flow

1. Fresh identity/right + `confirm=True` создают intent: actor/tenant/membership,
   session, role IDs, текущий contract/schema инструмента, аргументы и expiry (1 час).
2. Runtime возвращает preview и не исполняет следующие эффекты в том же batch.
3. Тот же actor отвечает «да»/«нет»; другой actor не может потребить/заменить intent,
   отменить его или удалить через `/stop`.
4. При «да» identity/right/contract/args/session проверяются заново. Pending
   очищается, затем перед эффектом выполняется ещё одна проверка и одноразовый
   approval потребляется registry до handler. При отзыве/изменении/expiry отказ.
5. Старые intents без binding не принимаются: запросить действие заново.

Это in-process защита, не DB CAS/transaction fence или гарантия exactly-once при
одновременных «да». Revoke→restore не имеет epoch. Общая политика shared-session
и durable confirmation требуют отдельного контракта.

`actions_log` и `action_send` требуют `botaction.view`. Последний — только
подтверждаемый локальный preview с literal `dry_run=True`: состояние PENDING,
без approval/result/attempt writes и без отправки. Live request отклоняется.
Другие publication paths этим пакетом глобально не перепроектированы.

Контракт подготовлен, тесты не запускались агентом; команды и ограничения — в
[handoff](design/identity_permissions_handoff.md). PR #22 ещё не слит в dev.

### LLM модели и провайдеры

Инструменты в `app/agent/toolset/llm.py` (permission prefix `llmmodel.*`,
`llmprovider.*`):

| Tool | Назначение | Confirm | Право |
|---|---|---|---|
| `llm_providers_list` | список провайдеров (id, name, api_format, base_url, is_active, is_default) | нет | `llmprovider.view` |
| `llm_models_list` | список моделей с фильтрами provider_id, model_type, active_only | нет | `llmmodel.view` |
| `llm_model_test` | тест модели (промпт → ответ, usage, нет секретов) | нет | `llmmodel.view` |
| `llm_model_add` | создать модель (provider_id, name, api_model_id, model_type, costs, is_default) | да | `llmmodel.create` |
| `llm_model_update` | частичное обновление модели | да | `llmmodel.update` |
| `llm_model_delete` | удалить модель с preview перераспределения default (dry_run=true по умолчанию) | да | `llmmodel.delete` |

Удаление модели (`llm_model_delete`) показывает план перед выполнением:
- какая модель удаляется
- был ли она default для своего `model_type`
- сколько сценариев (`AgentScenario.text_llm_model_id` / `image_llm_model_id` / `video_llm_model_id`) сбросится в NULL
- кто станет новым default (по приоритету: `last_success_at` → имя в `ai_analytics.llm_model` → любой активный)
- предупреждение, если модели этого типа не останется

### Команды чата

- `/help` — список возможностей
- `/stop` — очистить историю (session остаётся)
- `/good` — отметить последний ответ как удачный (в `agent_feedback`)
- `/bad <заметка>` — жалоба на последний ответ; заметки копятся для рефлексии
- `/memory clear` — стереть выученные факты (только владелец; watermark `learn` остаётся)

## Память и обучение

`memory_set`/`memory_get` — durable KV, скоуп на workspace
(см. `agent_memory_manager`). Теперь память **подмешивается в системный промпт**
автоматически: `build_system_prompt()` (`app/agent/runtime.py`) добавляет
соглашение о стиле (`tenants.agent_style`) и top-N фактов по `confidence`
(`agent_memory.snapshot()`, scope `meta` исключён).

Каждый факт несёт provenance: `source` (`manual`|`learn`|`reflect`),
`confidence` (0.1–1.0), `evidence_message_id` (сообщение-основание, FK SET NULL).

Обучение — два фоновых jobs (см. `docs/AGENT_TASKS.md`):

- `learn` (`app/agent/learning.py::run_learn`) — вытаскивает
  устойчивые факты/предпочтения из новых реплик чата. Watermark в
  `agent_memory(scope=meta,key=learn_msg_wm)`: LLM дёргается только когда
  накопилось `min_messages` (default 8) новых user-реплик — иначе дешёвый skip.
- `reflect` (`run_reflect`) — еженедельная гигиена: сливает
  дубли, чистит устаревшее, понижает `confidence` спорного; из `/bad`-заметок
  готовит **предложение** правки промптов (`prompt_advice`) — применяется
  вручную, автоматической точки правки нет.

Дефолтные cron для обоих заданы в `app/tasks/bootstrap.py`
(`DEFAULT_TASKS`) — там и смотреть актуальные значения.

Промпты агента живут в `AgentScenario`:
`base_prompt` (основная инструкция) + `media_overrides` (JSON: `image`/`video`/`audio` для мультимедиа) + `summary_prompt` (сводка).
Правка — через чат (`scenario_*`), `PUT /api/v1/ai/scenarios/{id}`, веб-мастер
(`/app/scenarios/new`) или админку (`AgentScenarioAdmin`); изменение
подхватывается со следующего запуска задачи (сценарий читается на каждый
run). Автоматическая эволюция промптов **не происходит**: `prompt_advice` из
`reflect` владелец применяет сам.

## Сценарии в чате

Инструменты в `app/agent/toolset/scenarios.py` (см. `docs/CHAT_BOT_SCENARIOS.md`):

| Tool | Назначение | Confirm |
|---|---|---|
| `scenario_list` / `scenario_get` | список и полная карточка сценария | нет |
| `scenario_templates` | готовые пресеты (`app/services/ai/scenario_templates.py`) | нет |
| `scenario_suggest_prompt` | описание задачи → `base_prompt` через LLM | нет |
| `scenario_validate_prompt` | неизвестные переменные в промпте | нет |
| `scenario_create` | из пресета или с нуля | да |
| `scenario_update` | частичное обновление, `scope` сливается по типам | да |
| `scenario_clone` | копия под новым именем (без флага default) | да |
| `scenario_delete` | удаление; возвращает число задач, оставшихся без сценария | да |

Подтверждение — общее для всех write-tools: runtime кладёт вызов в
`session.state['pending_confirmation']` и ждёт «да».

### Web chat

`/app/chat` использует тот же runtime (`handle_web_message`), что и Telegram/MAX.
Workspace разрешается `TenantUIMiddleware` из `tenant_users` web membership.
Роль передаётся как `role_id` в `handle_web_message()`, permission checks
работают через `permission_scope()` так же, как в messenger.

Сессия агента — одна на (tenant, channel, chat_id): один и тот же web-пользователь
в двух workspaces ведёт два разных диалога, а не общий транскрипт.

### Telegram/MAX users

Для telegram/MAX users User разрешается через `tenant_users` → `user_id` → `User`.
Если `user_id = NULL` (unmessenger user), permission checks пропускаются
(`has_permission(None, ...) = True` via bypass for legacy rows).

## Задачи в чате

Инструменты в `app/agent/toolset/tasks.py`:

| Tool | Назначение | Confirm |
|---|---|---|
| `task_list` | список всех cron-задач с активностью и следующим запуском | нет |
| `task_add` | создать задачу: cron, job_type, sources, payload (brands, competitors, hashtags, influencer_names, keywords_list, topic_list) | да |
| `task_update` | изменить задачу: job_type, cron, payload, scenario_id, source_ids (частичное, только переданные поля) | да |
| `task_remove` | удалить по имени | да |
| `task_pause` | поставить на паузу / возобновить | да |

При создании/изменении задачи проверяется соответствие payload сценарию:
если сценарий использует `brand_mentions`, а `brands` не переданы — возвращается
`warnings` (не ошибка). Это позволяет анализу работать без фокуса, просто шире.

## Источники в чате

Предпочтения сценариев (`app/services/ai/scenario_prefs.py`) пишутся в
`agent_memory(scope=scenario_prefs)`: режим группировки и список брендов/конкурентов.
Читаются как дефолты в `scenario_create` и в первом шаге веб-мастера — пустые
поля заполняются, явно выбранные — нет.

Порядок работы с новым сценарием описан в системном промпте
(`SCENARIO_SECTION` в `app/agent/prompts.py`, добавляется в
`build_system_prompt()` независимо от `AGENT_SYSTEM_PROMPT`): цель → шаблон →
поля → промпт → валидация → превью → подтверждение.

Подробности: `docs/CHAT_BOT_SCENARIOS.md` (архитектура, переменные, валидация).
Исторический контекст: `docs/PROMPT_AND_SCOPE_EXPLAINED.md`.

## Лимиты и стоимость

| Параметр | Назначение |
|---|---|
| `AGENT_DAILY_COST_LIMIT` | USD-потолок за сегодня (UTC-день): агент + дайджесты + обучение (`learn`/`reflect`); проверка до и во время цикла |
| `tenant.daily_cost_limit` | Персональный лимит рабочего пространства (перекрывает глобальный) |
| `AGENT_MAX_ITERATIONS` | Макс. раундов tool-calls на одно сообщение |
| `AGENT_HISTORY_LIMIT` | Сколько сообщений истории уходит в LLM |
| `AGENT_MAX_TOKENS` / `AGENT_TEMPERATURE` | Параметры `chat()` |
| `AGENT_MODEL` | Явная модель; пусто = авто-выбор первой активной |

Метрика расхода — `tenancy.resolver.daily_cost_today()`: сумма
`AgentMessage.cost` (чат, пишется из `usage["cost"]`), `DigestRun.llm_cost`
(LLM-сводка дайджеста) и `Job.llm_cost` (`learn`/`reflect`, пишется из
`usage["cost"]` ответа `chat_with_fallback` через результат job'а).
Тарифы — из `llm_models` (`llm_client.price_usage_usd`),
столько же записывает `analyze()` в top-level `usage` дайджеста. Дайджест и
`learn`/`reflect` при исчерпанном лимите пропускают LLM-вызов (дайджест при
этом публикуется без LLM-сводки). См. `docs/AGENT_TASKS.md` для
общей картины очереди.

## Tenancy

Каждый чат принадлежит ровно одному рабочему пространству
(`tenant_channels`, резолвится в `app/services/tenancy/resolver.py`).
Вся работа агента выполняется внутри `tenant_scope(tenant_id)` — система
лишний раз фильтрует и пишет только своё, даже если инструмент забудет
указать tenant_id. Подробности: `docs/TENANCY.md`.

## Тесты

`tests/test_agent.py`: confirmation round-trip, отмена, plain answer, лимит
стоимости, незнакомцы игнорируются, listener шлёт ответ в тот же чат, `/help`,
проброс лимитов в `chat()`. Модель и HTTP не дёргаются — `_chat` стабится.

## LLM lifecycle reliability

`llm_model_add/update/delete/test` already exist in `toolset/llm.py` and are
registered by `toolset/__init__.py`. Writes require confirmation and the matching
`llmmodel.create/update/delete` right; the runtime rechecks permissions after
confirmation. Connection testing is read-gated by `llmmodel.view`.

Model tools support `decision` and `custom_endpoint_path` as well as the existing
model types. Delete preview excludes the model being deleted and models on
inactive providers. A non-default deletion does not claim that the fleet has
lost its default. Actual deletion still runs through the shared model manager.


## Aggregate-first analytics tools

The `entities` axis is displayed to users as **По упоминаниям** (people, brands,
organizations). The tool/query value `entities` and `entity_type` filters are unchanged.

`report_period(period="day"|"week", group_by="themes"|"sources"|"entities"|
"intent"|"topic_chains", limit=10, sentiment=None, media=None)` returns compact
zero-LLM groups `{key, count, avg_sentiment}`, total group count and period metadata.
Optional `time_breakdown` includes only per-date counts and average sentiment.
`analytics_chains(days=30, source_id=None, limit=10, sentiment=None, media=None)`
returns the same compact shape with chain IDs as keys. `analytics_chain_detail`
returns daily aggregates. Explicit `limit=0` requests the complete aggregate list.
Positive limits request top N. Raw analyses, prompts and source posts are not tool
results. Preview reports do not build/publish digests or write weekly rollups.

The system prompt requires aggregate-first context, including for custom base
prompts. The existing chat loop selects the cost-efficient text model; no second
narrative LLM call is introduced. `digest_send_now` remains confirmation-gated,
uses the configured quality/default text model for narrative and honors caps.
Quality is configuration, not a price-based estimate. Existing permission gates
(`digestrun.view`, `aianalytics.view`, `digestrun.update`) remain unchanged.
