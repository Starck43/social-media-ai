# UI для владельцев workspace — концепция и план реализации

Ниже — описание того, как должен выглядеть веб-интерфейс для пользователей, которые работают с платформой не через Telegram/MAX, а через браузер. Это **клиентский UI**, а не операторская админка (`/admin` уже есть и остаётся для платформы).

---

## 1. Принципы

1. **UI — альтернатива чату, а не замена.** То, что можно сделать в Telegram, можно сделать в UI. И наоборот. Оба интерфейса работают с одной БД через один API.
2. **Один бот — один UI.** Если пользователь привязал чат через invite-код, тот же код (или e-mail-логин) пускает его в веб.
3. **Tenant-first.** Каждый запрос из UI идёт с JWT, из которого middleware резолвит `tenant_id`. Никакого `bypass` — пользователь видит только своё.
4. **Никаких новых API под UI.** Используем существующие эндпоинты из [`docs/API.md`](./docs/API.md) и расширяем их только если чего-то объективно не хватает.
5. **Чат в UI — это тот же агент.** WebSocket-тоннель над `agent_sessions`, те же инструменты, те же подтверждения.

---

## 2. Технологический стек (рекомендация)

**Основа: HTMX + Alpine.js + Tailwind CSS + Jinja2 templates на FastAPI.**

Почему не React/Vue SPA:
- Платформа — personal agent на одном VPS. Добавлять Node.js build chain и второй деплой-артефакт — overkill.
- SSR из FastAPI + Jinja2 = мгновенный first paint, работает без JS там, где это возможно.
- HTMX даёт «SPA-ощущение» (partial updates, infinite scroll, modals) без фреймворка.
- Alpine.js — реактивность там, где она нужна (dropdowns, forms, toasts).

Альтернатива, если хочется богатый интерактив на аналитике: **React + shadcn/ui** для раздела `/app/analytics`, остальное — на HTMX. Но начинать рекомендую с HTMX.

Графики: **Chart.js** (уже в проекте) или **uPlot** для больших таймсерий.

---

## 3. Архитектурное место UI

```
                    ┌──────────────────────────────────┐
                    │         Browser (HTMX)           │
                    └───────────────┬──────────────────┘
                                    │ HTTPS
                    ┌───────────────▼──────────────────┐
                    │      Nginx (reverse proxy)       │
                    └──┬──────────────┬────────────────┘
                       │              │
          GET /app/*   │              │  GET /api/v1/*
          (HTML+HTMX)  │              │  (JSON, JWT)
                       │              │
         ┌─────────────▼──────────────▼─────────────┐
         │              FastAPI app                  │
         │                                          │
         │  ├─ app/web/          ← UI routes (HTML)  │
         │  │   ├─ auth.py       login/invite/register
         │  │   ├─ dashboard.py  overview
         │  │   ├─ sources.py    CRUD sources
         │  │   ├─ analytics.py  charts, trends
         │  │   ├─ schedules.py  cron editor
         │  │   ├─ digests.py    preview + history
         │  │   ├─ chat.py       agent chat (WS + HTMX)
         │  │   └─ settings.py   workspace, team, creds
         │  │                                        │
         │  ├─ app/api/v1/       ← JSON API (есть)   │
         │  │                                        │
         │  ├─ app/admin/        ← операторы (есть)  │
         │  │                                        │
         │  └─ TenantUIMiddleware ← JWT → tenant_id  │
         └──────────────┬───────────────────────────┘
                        │
              ┌─────────▼──────────┐
              │    PostgreSQL      │
              └────────────────────┘
```

**Новый middleware:** `TenantUIMiddleware` — для запросов на `/app/*` достаёт JWT, валидирует, резолвит `tenant_id` через `tenant_users` и выставляет `current_tenant_id()`. Это зеркало существующего `PlatformScopeMiddleware`, но без `bypass`.

---

## 4. Ключевые экраны

### 4.1. Auth & onboarding

| Экран | Назначение |
|-------|-----------|
| `/app/login` | Email + пароль. После успеха — редирект на dashboard. |
| `/app/register` | Саморегистрация создаёт новый `tenant` + `tenant_user` (role=`owner`). |
| `/app/invite` | Форма ввода invite-кода (из Telegram `/invite`). Редиректит в созданный workspace. |
| `/app/onboarding` | Первый вход: чек-лист из двух шагов — источник и расписание. Каждая форма постит в существующий эндпоинт создания с `next=/app/onboarding`, так что «создать» имеет одну реализацию. Шаг помечен «готово», когда источник / задача уже есть; форма не показывается без права `create`. |

Шаблон навигации — `app/web/nav.py` (`NAV_ITEMS`), а не литерал в HTML: пункт с
неготовой страницей несёт `ready=False` и рисуется как «скоро», поэтому в меню
нет ссылки `#`, которая ничего не делает. Мобильный бар (`MOBILE_NAV_ITEMS`)
содержит только `ready` — под ним нет места для «скоро».

### 4.2. Dashboard (`/app/`)

Лендинг после логина. Один экран — вся оперативная обстановка:

- **4 KPI-карточки:** источники активны / постов за сутки / средний sentiment / LLM-расход за сутки.
- **Timeline последних 10 событий** (собранные посты, сработавшие триггеры, дайджесты).
- **График sentiment за 7 дней** (Chart.js line).
- **Состояние источников:** список с индикатором `ok` / `error` / `stale`.
- **Быстрые действия:** «Собрать сейчас», «Отправить дайджест», «Добавить источник».

### 4.3. Sources (`/app/sources`)

| Действие | Описание |
|---------|---------|
| List | Таблица: название, платформа, тип, сценарий, статус, последний сбор, кнопка «Собрать». |
| Add | Wizard: платформа → режим (L1/L2/L3) → ввод идентификатора → выбор сценария → тест подключения. |
| Detail | История сборов, последние посты (только метаданные, без сырых текстов), привязанный сценарий. |
| Credentials | Кнопка «Подключить через vault» → модалка ввода токена → `user_credentials` (Fernet, личные L2). |

### 4.4. Analytics (`/app/analytics`)

Вкладки:

- **Sentiment** — тренд, распределение, фильтр по источнику и периоду.
- **Topics** — top-N тем с примерами (ссылки на `ai_analytics.id`).
- **Engagement** — reactions/comments per post, heatmap по времени суток.
- **LLM cost** — расход по провайдерам и моделям, прогноз на месяц.
- **Content mix** — pie chart: text / image / video / audio.

Все эндпоинты уже есть в [`API.md`](./docs/API.md) (`/analytics/aggregate/*`) — UI только визуализирует.

### 4.5. Schedules & Jobs (`/app/schedules`)

- Список расписаний с cron-редактором (визуальный, не текстовый).
- История джобов: статус, `attempts`, `error`, кнопка «Retry».
- Кнопка «Pause / Resume».
- Создание нового — через выбор handler (`collect` / `digest` / `prune` / `analyze`).

### 4.6. Digests (`/app/digests`)

- История отправок (`digest_runs`): период, статус, канал, время, сам текст.
- **Preview**: кнопка «Предпросмотр» → собрать текущий дайджест без отправки.
- Ручная отправка: выбор периода (day/week) и канала.

### 4.7. Chat with agent (`/app/chat`)

Это важно: UI-чат = тот же агент, что и в Telegram.

- Слева — список `agent_sessions` пользователя (если у него несколько чатов).
- Справа — транскрипт (`agent_messages`): user / assistant / tool / confirmation.
- Внизу — поле ввода. Отправка идёт через WebSocket в `agent.runtime`.
- Confirmation-запросы рендерятся как кнопки «Да» / «Нет» прямо в транскрипте.
- `/help`, `/stop`, `/good`, `/bad`, `/memory clear` — те же команды, что и в Telegram.

### 4.8. Bot Scenarios (`/app/scenarios`)

- Список сценариев с фильтром по источнику.
- Редактор: промпты (text/image/video/audio/unified), `scope` JSON-schema, триггеры, `action_type`.
- Тест: «Прогнать на последних 5 постах источника X» → preview результата.

### 4.9. Settings (`/app/settings`)

Вкладки:

- **Workspace:** slug, daily cost limit, agent style (тон, язык, тихие часы).
- **Team:** список `tenant_users` + их роли. Генерация invite-кода.
- **Credentials:** список записей `user_credentials` (личные L2, без plaintext!), кнопки `disable` / `test` / `rotate`.
- **LLM providers:** глобальные (переопределения per-tenant не реализованы).
- **Notifications:** на что подписан (алерты, дайджесты, подтверждения).
- **Export / Delete:** выгрузка данных workspace, самоуничтожение (soft-delete tenant).

---

## 5. Пошаговый план реализации

### **Milestone 0. Фундамент идентичности — сделано до M1**

План писался поверх расхождения с реальностью, которое закрыто отдельно:

- JWT несёт только `sub` (user id), `tenant_id` в токене нет — резолв workspace
  идёт из БД, а не из claims;
- `TenantUser` был ключом messenger-identity (channel + external_user_id) и не
  был связан с таблицей `users` — добавлена веб-мэмбершип та же строка с
  `user_id` FK и `channel='web'` (миграция 0054);
- `PlatformScopeMiddleware` включал bypass на весь HTTP — `/app/*` исключён,
  его скоупом владеет `TenantUIMiddleware` (`app/web/middleware.py`);
- веб-redeem invite-кода: `TenantInviteManager.redeem_web` + страница
  `/app/invite`;
- CSRF на POST-формах UI через существующий `CSRFTokenManager`, параметр
  `?next=` фильтруется от open-redirect.

---

### **Milestone 1. Фундамент (1 неделя)**

1. Добавить `app/web/` модуль с базовым layout (header, sidebar, footer).
2. `TenantUIMiddleware`: JWT → `tenant_id`, 401/403 при отсутствии.
3. Страницы `/app/login`, `/app/register`, `/app/invite`.
4. Jinja2 base template с Tailwind (через CDN на старте, потом Vite).
5. Flash-сообщения, CSRF-токены.

**Definition of done:** можно залогиниться, зарегистрироваться, привязаться по invite-коду и увидеть пустой layout.

---

### **Milestone 2. Dashboard + Sources (1 неделя)**

1. `/app/` — 4 KPI-карточки (агрегация на SQL, без LLM).
2. `/app/sources` — list + detail + delete.
3. Модалка добавления источника: выбор платформы, ввод ID, тест через API.
4. Кнопка «Collect now» → создаёт `jobs` row → HTMX-poll статуса.
5. Индикаторы статуса (`ok` / `error` / `stale`).

**Definition of done:** пользователь может добавлять источники и запускать сбор руками.

---

### **Milestone 3. Analytics (1 неделя)**

1. `/app/analytics/sentiment` — line chart (Chart.js).
2. `/app/analytics/topics` — top-N таблица.
3. `/app/analytics/engagement` — heatmap.
4. `/app/analytics/llm-cost` — bar chart по провайдерам + прогноз.
5. `/app/analytics/content-mix` — pie chart.
6. Общий фильтр периода (1d / 7d / 30d / custom) в шапке раздела.

**Definition of done:** все 5 эндпоинтов `/analytics/aggregate/*` визуализированы, работают фильтры.

---

### **Milestone 4. Schedules + Jobs (1 неделя)**

1. `/app/schedules` — таблица + визуальный cron-редактор (например, `cronstrue` для отображения human-readable).
2. Pause / Resume / Remove (HTMX POST).
3. `/app/jobs` — история, статусы, retry.
4. Мастер создания расписания: выбор handler + payload.

**Definition of done:** пользователь управляет расписаниями без CLI.

---

### **Milestone 5. Digests (3 дня)**

1. `/app/digests` — таблица `digest_runs`.
2. Кнопка «Preview» → модалка с рендером текущего дайджеста (HTML-safe).
3. Кнопка «Send now» с выбором периода и канала.
4. Подсветка идемпотентности: если уже отправлен — показать «already sent at …».

---

### **Milestone 6. Chat with agent (1 неделя)**

1. `/app/chat` — layout с транскриптом.
2. WebSocket endpoint `/app/chat/ws` → `agent.runtime.handle_inbound()`.
3. Рендер tool-вызовов и confirmations (кнопки «Да»/«Нет»).
4. Slash-команды (`/help`, `/stop`, `/good`, `/bad`, `/memory clear`).
5. История `agent_messages` с пагинацией.

**Definition of done:** весь функционал Telegram-чата доступен в браузере, включая подтверждения.

---

### **Milestone 7. Bot Scenarios (3 дня)**

1. `/app/scenarios` — list + detail.
2. Редактор промптов (textarea с превью переменных `{content}`, `{date_range}`).
3. JSON-schema editor для `scope`.
4. Триггеры: visual builder (keyword / sentiment threshold / mention).
5. Тест-прогон: выбор источника → preview результата анализа.

---

### **Milestone 8. Settings + Team (1 неделя)**

1. `/app/settings/workspace` — slug, agent style, daily cost limit.
2. `/app/settings/team` — список members, generate invite code, revoke.
3. `/app/settings/credentials` — masked list, disable, test, rotate (без показа plaintext).
4. `/app/settings/notifications` — toggle каналов уведомлений.
5. `/app/settings/danger` — export (ZIP), delete workspace (soft + confirm).

---

### **Milestone 9. Polish (1 неделя)**

1. Mobile-friendly: sidebar → bottom nav, таблицы → карточки.
2. Keyboard shortcuts (`/` — фокус на чат, `g s` — sources, `g a` — analytics).
3. Toast-уведомления (HTMX events).
4. Skeleton loaders на тяжёлых страницах.
5. Empty states с подсказками.
6. Dark mode (через `prefers-color-scheme`).
7. i18n: русский по умолчанию, английский опционально.

---

## 6. Что нужно добавить в бэкенд

| Что | Зачем | Сложность |
|-----|-------|-----------|
| `TenantUIMiddleware` | Резолвить tenant из JWT для UI-запросов | Низкая |
| `app/web/` модуль | UI routes на FastAPI + Jinja2 | Средняя |
| WebSocket для чата | Стримить ответы агента в UI | Средняя |
| `POST /api/v1/auth/invite` | Принимать invite-код и создавать tenant_user | Низкая |
| `GET /api/v1/analytics/aggregate/preview-digest` | Рендер дайджеста без отправки | Низкая |
| `POST /api/v1/scenarios/{id}/test` | Прогнать сценарий на выборке | Средняя |
| `GET /api/v1/sources/{id}/health` | Детальный health-check источника | Низкая |

Существующий API в 90% случаев подходит без изменений — он уже tenant-scoped через `current_tenant_id()`.

---

## 7. Безопасность

- **Права в `/app` — `app/web/perms.py`** (`WebPerms.can(model, action)`): владелец
  workspace (`tenant_users.role == "owner"`) управляет своим workspace всегда,
  остальные — по платформенной роли (`User.has_perm_for`, структурированные
  `permissions.model_type_id` + `action_type`), суперюзер — всё. То же правило,
  что `Resolution.is_owner` применяет в чате. Мутации закрыты серверно
  (`guard_web` в `app/web/deps.py`), кнопки в шаблонах лишь скрываются — это не
  проверка. Шаблон: `perms.can('source', 'create')`.

- **JWT короткого života** (15 мин access + 30 дней refresh, уже настроено в `CONFIGURATION.md`).
- **HttpOnly cookie** для refresh-токена, access — в памяти.
- **CSRF** на всех POST (`SCRF_TOKEN_*` уже в конфиге).
- **Rate limit** на login и invite redeem.
- **Tenant isolation** проверяется не только в middleware, но и в `BaseManager` — даже если middleware обойдут, данные не утекут.
- **Никаких plaintext credentials в UI.** Vault показывает только `••••••` и «последние 4 символа».
- **Audit log** действий владельца (смена лимитов, удаление workspace, добавление credentials) — в `notifications`.

---

## 8. Роадмап в одну картинку

```
Week 1 ──── Week 2 ──── Week 3 ──── Week 4 ──── Week 5 ──── Week 6
   │           │           │           │           │           │
   M1          M2          M3          M4          M5+M6       M7+M8+M9
Foundation  Dashboard   Analytics  Schedules    Digests    Scenarios
+ Auth      + Sources   charts     + Jobs       + Chat     + Settings
                                                            + Polish
```

**Итого: 6 недель до production-ready UI** при полной занятости. Параллельно можно продолжать развивать агентский runtime — UI и runtime связаны только через API и БД.

---

## 9. Что НЕ делаем в UI

- **Не дублируем** функционал sqladmin (`/admin`). Операторы используют админку, клиенты — UI.
- **Не делаем** визуальный редактор cron «drag-n-drop» — текстовый cron + human-readable preview достаточно.
- **Не делаем** raw-контент preview (сырые тексты постов) — только метаданные и аналитика. Это инвариант продукта.
- **Не делаем** multi-select массовых операций на первом этапе — добавим, когда появится реальный use case.
- **Не делаем** PWA / offline mode — платформа живёт на VPS, offline-сценариев нет.

---
