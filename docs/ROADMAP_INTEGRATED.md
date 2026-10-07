# Integrated Roadmap: analogs research × project reality

Метод: каждое предложение из comparative analysis сверено с фактическим
состоянием кода/доков. Статусы: DONE (уже есть) / PARTIAL (фундамент есть,
дожать) / GAP (нет ничего) / REJECT (противоречит философии проекта).

Философия-ограничения (не нарушать):
- Один VPS, без новых обязательных сервисов (Redis/Celery уже отвергнуты).
- Стоимость под потолком: tenant.daily_cost_limit + AGENT_DAILY_COST_LIMIT.
- Privacy: сырой текст не хранится вечно (collected_items — write-ahead).
- Сценарий = методология; задача = реакция и цели; группировка = запрос.

## Phase 0 — Гигиена (без кода фич, разблокирует всё остальное)
| # | Задача | Статус сейчас |
|---|--------|---------------|
| 0.1 | Синхронизировать доки с кодом: DIGEST.md (analyze_type → payload.group_by/time_breakdown), MODELS.md (триггеры/guards на agent_tasks; tenant_users.role_id; head миграций), API.md (base_prompt/media_overrides/summary_prompt; /analytics/aggregate/grouped) | GAP в доках |
| 0.2 | Верифицировать в коде: финальный состав GroupingAxis (DAYS/MONITORED_USERS удалены, CHAINS→TOPIC_CHAINS); дайджест читает payload.group_by | не подтверждено |
| 0.3 | Верифицировать/доделать chain_resolver (заполнение topic_chain_id/chain_label по topic_hint) | PARTIAL/не видно |

## Phase 1 — Надёжность и безопасность (high impact, low risk)
| # | Задача | Аналог | Статус проекта | Почему сейчас |
|---|--------|--------|----------------|---------------|
| 1.1 | Pydantic-модели ответов LLM (AnalysisResult и т.д.) вместо regex extract_json(); модели зеркалируют контракт JSONSchemaBuilder (tests/test_reporting_contract.py пинит пару) | SmIA, Ratatoskr, IntelFlow | PARTIAL: схема есть, парсинг regex | Делает надёжным relevance-фильтр (is_meaningful/confidence) и специализированные агрегации |
| 1.2 | Prompt-injection guard: санитайзер + instruction-defense framing ПЕРЕД подстановкой {text} (собранный соц-контент — недоверенный текст внутри промпта анализа) | Iva, Agent Life Space | GAP | Дайджест-нарратив и цепочки иначе отравляемы; дёшево |
| 1.3 | Аудит-снимок: prompt_hash + scope/payload snapshot + latency_ms в ai_analytics (response_payload.request) | socmon | PARTIAL: llm_model и response_payload есть | Отвечает «почему анализ изменился» без внешнего трейсинга |
| 1.4 | Заставить работать llm_strategy (cost_efficient/quality/multimodal): выбор модели по стратегии + LLMModel.role (selector/writer) | ai-news-digest, rss-bot | PARTIAL: поле есть, enforcement не виден | Не строить параллельный механизм двух моделей |

## Phase 2 — Embedding-платформа: один сервис, три потребителя
Сервис `app/services/ai/embeddings.py` (модель типа embedding из llm_models;
расход учитывается в daily cap; на starter-тарифе отключён — см. PLAN_LIMITS
«LLM model types»).
| # | Потребитель | Задача | Аналог | Статус |
|---|-------------|--------|--------|--------|
| 2.1 | Semantic dedup | cosine-порог против недавних embedding при collect; точный content-hash остаётся первым контуром | Telo-watch-tower, Syne | GAP (hash есть) |
| 2.2 | Chain resolver | сопоставление topic_hint с живыми цепочками вместо нормализации строк | — (наша архитектура) | PARTIAL |
| 2.3 | Agent memory | колонка embedding в agent_memory + semantic_search tool; access_count/last_accessed для reflect (decay становится data-driven, не новый job) | Mem0, Syne, Lethe | PARTIAL: confidence+provenance+reflect есть |
REJECT: knowledge graph (Syne/Cognee) — entities[] + topic_chain_id уже дают
связи «сущность↔тема»; граф оверкилл на этом объёме данных.

## Phase 3 — Стоимость и отказоустойчивость
| # | Задача | Аналог | Статус |
|---|--------|--------|--------|
| 3.1 | Алгоритмический pre-filter перед LLM (regex/engagement-порог) — экономия токенов | BuzzAgent | GAP |
| 3.2 | Probe-job здоровья моделей в HANDLERS registry (без миграции); авто-disable деградировавших | rss-bot AUTOMODEL | PARTIAL: chat_with_fallback есть |
| 3.3 | Error-learning: авто-захват провальных tool_calls + реплики пользователя → Correction-строки; применение по-прежнему ручное (prompt_advice) | Kit | PARTIAL: /bad + reflect есть |

## Phase 4 — Продукт
| # | Задача | Аналог | Статус |
|---|--------|--------|--------|
| 4.1 | Билингвальность: пронести tenants.agent_style.language в нарратив дайджеста и render | IntelFlow, BuzzAgent | PARTIAL: поле language есть |
| 4.2 | Source suggestion: LLM предлагает источники по описанию темы (сценарные шаблоны + suggest_prompt УЖЕ есть) | twidgest-bot | PARTIAL |
| 4.3 | Web search tool в реестр агента (required_permission, confirm не нужен) | Kit, IntelFlow | GAP |
| 4.4 | Telegram Stars billing для pro-тарифа | claude-digest | GAP (планы есть, оплаты нет) |

## REJECT-лист (с обоснованием)
| Предложение | Почему нет |
|---|---|
| skill_steps JSON в AgentScenario | Сценарий только что вычищен до «методологии»; процедурные навыки = инструменты агента (tools и есть skill-система). Размывать границу сценарий/задача нельзя |
| Raw payload retention по умолчанию | Противоречит privacy-позиции (collected_items удаляется после сохранения анализа). Допустимо только opt-in флагом тарифа business |
| Knowledge graph | entities + chains покрывают ~80% ценности при нулевой новой инфраструктуре |
| Отдельный job памяти-decay | reflect уже точка гигиены; добавлять статистику доступа туда |

## Решения по open questions
1. **Memory scope:** additive — KV остаётся, embedding-колонка добавляется; ОДИН embedding-сервис на dedup + chains + memory (иначе три тарификации и три интеграции).
2. **Observability:** сначала in-DB (1.3: prompt_hash, latency, tool-trace уже в agent_messages.tool_calls); Langfuse — опционально за LANGFUSE_SECRET_KEY, не раньше Phase 3 (философия: без лишних сервисов).
3. **Skills:** не расширять сценарий; реестр инструментов = skill-система.
4. **Payments:** Stars сначала, веб-оплаты позже; не блокирует ничего технического.

## Validation gate на каждую фазу
- pytest зелёный (сейчас 255 тестов — планка не ниже).
- Daily cost cap не сломан: embedding-расходы входят в daily_cost_today().
- Tenant-изоляция: embedding-колонки и новые таблицы — TenantScopedMixin где применимо.
- Док-синхронизация: каждая фаза заканчивается правкой соответствующего docs/*.md.
