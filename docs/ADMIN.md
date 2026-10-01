# Admin Panel Reference

The application includes a sqladmin panel for managing all database entities.

**Base URL:** `http://localhost:8000/admin`

## Authentication

Session-based authentication. The admin panel requires a logged-in user session.

- **Login:** `GET /admin/login`
- **Logout:** handled by sqladmin's built-in logout
- **Password Reset:** `GET /admin/reset-password` → `POST /admin/reset-password`

### Password Reset Flow

```
GET  /admin/reset-password     → form (email + CSRF token)
POST /admin/reset-password     → generates temp password, logs to console
GET  /admin/login?message=password_reset → login with temp password
```

### Change Password (logged-in user)

```
GET  /admin/user/change-password/{user_id}
POST /admin/user/change-password/{user_id}
```

Requires: `current_password`, `new_password`, `confirm_password`, `csrf_token`.

### Verify Current Password (AJAX)

```
POST /admin/verify-current-password
Content-Type: application/json

{"current_password": "pass123"}
```

Returns: `{"valid": true/false}`

---

## Authorization (Django-style per-model permissions)

The console is gated by the **platform role** of the signed-in operator, the
same way Django gates a `ModelAdmin`. There is no separate admin permission
model: the answer to "may this operator do this" comes from `role_permission` →
`permissions.model_type_id` + `action_type`, read through
`User.model_permissions()` — one source of truth shared with `/api` and the CLI.

**Who may enter:** a superuser (`users.is_superuser` or the `SUPERUSER` role),
or any user whose role carries at least one model `view`
(`User.has_admin_access()`). Anyone else is sent back to the login form.

**What they may do:** per model, the role's rights map onto the console actions
one to one, and sqladmin asks about every one of them — hiding a button and
blocking the route behind it are the same check.

| Console action | Required `ActionType` |
|---|---|
| list, details | `VIEW` |
| create, import | `CREATE` |
| edit | `UPDATE` |
| delete | `DELETE` |
| export | `EXPORT` |
| `@action` buttons | whatever the view declares (below) |

Two rules on top of the table, both Django's:

- **any right implies the change list** — an operator who may only *create* a
  record still has to open the list to reach the form, so `create` / `update` /
  `delete` / `export` also grant `list` and `details`;
- **no right, no model** — a model the role says nothing about is not in the
  menu (`ModelView.is_accessible`) and its routes answer 403.

A model view can switch an action off entirely with the usual `can_create`,
`can_edit`, `can_delete`, `can_export` flags (e.g. `AIAnalyticsAdmin` has
`can_create = False`); a flag off means the action opens nothing, right or not.

**Custom actions** are not model CRUD, so each view declares what its buttons
need in `BaseAdmin.action_permissions`, keyed by the slug sqladmin generates
(`@action` slugifies the function name, so `mark_read` is `"mark-read"`):

| View | Action | Right | Why |
|---|---|---|---|
| `UserAdmin` | `change-password` | `UPDATE` | it edits a user record |
| `SourceAdmin` | `check-source` | `UPDATE` | it enqueues a collection run |
| `AgentScenarioAdmin` | `view-prompts` / `toggle-active` | `VIEW` / `UPDATE` | read / toggles a flag |
| `AgentTaskAdmin` | `run-now` | `UPDATE` | it enqueues a job |
| `NotificationAdmin` | `mark-read`, `send-to-messenger` | `UPDATE` | both change state |
| `AIAnalyticsAdmin` | `view-analysis` | `VIEW` | it opens the details page |
| `LLMProviderAdmin` | `test-connection` | `CONFIGURE` | it calls the provider with the stored key |
| `LLMModelAdmin` | `test-model` | `CONFIGURE` | same, for a model |

Unlisted actions default to `UPDATE` (they are mutations by nature), and a key
that names no action on the view is logged as a warning at startup — the
spelling mistake is otherwise silent.

**Where the code lives**

| File | Role |
|---|---|
| `app/models/user.py` | `model_permissions()` / `has_perm_for()` — the canonical check |
| `app/admin/authorization.py` | `AdminAuthorizationBackend` — turns those rights into grants; also `require_admin_perm()` for routes outside the console |
| `app/admin/base.py` | `action_permissions` declaration + `get_admin_user()` |
| `app/admin/views.py` | per-view flags and action declarations |
| `app/admin/auth.py` | who may enter the console at all |
| `scripts/setup/assign_roles_permissions.py` | the canonical role → permission matrix |

The backend derives the `identity → model` mapping from the registered views
(`AgentTask` → `agent-task` → the `model_types` row `agenttask`), so a newly
added admin view is gated with no extra wiring. A superuser is granted the
wildcard pair and passes everything.

The routes in `app/admin/endpoints.py` sit outside the sqladmin view tree
(`/dashboard`, `/dashboard/topic-chains`, the password pages), so they call
`require_admin_perm()` explicitly; changing *someone else's* password needs
`user.update`, changing your own does not.

---

## CSRF Protection

The admin panel uses CSRF tokens for all state-changing operations. Tokens are
managed by `CSRFTokenManager` and injected into all forms.

- Token name: `csrf_token`
- Token lifetime: `SCRF_TOKEN_EXPIRY_MINUTES` (default 15 min)
- Token length: `SCRF_TOKEN_LENGTH` (default 32 bytes)

---

## Rate Limiting

| Endpoint | Rate Limit |
|---|---|
| Password Reset | `3/hour` |
| Change Password | `5/minute` |

Rate limiting is disabled in `DEBUG` mode.

---

## Admin Pages

The following model pages are available (all under `/admin/<identity>`):

| Page | Model | Icon | Description |
|---|---|---|---|
| Пользователи | `User` | 👤 | User management |
| Роли | `Role` | 🛡️ | Role definitions |
| Разрешения | `Permission` | 🔑 | Permission entries |
| Платформа | `Platform` | 🌐 | VK, Telegram, MAX platforms |
| Источник | `Source` | 📡 | Content sources |
| Сценарий агента | `AgentScenario` | 🤖 | AI analysis scenarios |
| AI Аналитика | `AIAnalytics` | 📊 | AI analysis results |
| Уведомление | `Notification` | 🔔 | System notifications |
| Провайдер LLM | `LLMProvider` | 🖥️ | LLM provider config |
| Модель LLM | `LLMModel` | 💾 | LLM model definitions |
| Личные креды | `UserCredential` | 🔐 | Personal (per-user) L2 credentials vault |

---

## Custom Actions

Each admin page has custom actions available in list and detail views.

### User

| Action | Description |
|---|---|
| **Изменить пароль** | Redirects to change-password form for the selected user |

### Platform

| Action | Description |
|---|---|
| **Синхронизировать** | Placeholder for platform sync (TODO: implement) |

### Source

| Action | Description |
|---|---|
| **Проверить сейчас** | Collects content from this source in real-time and displays results with stats (likes, comments, views) and pagination. Handles VK privacy errors gracefully. |

### AgentScenario

| Action | Description |
|---|---|
| **👁️ Просмотр промптов** | Shows all prompts (text, image, video, audio, unified) with JSON instructions and scope config |
| **Активировать/Деактивировать** | Toggles `is_active` status for selected scenarios |

### AIAnalytics

| Action | Description |
|---|---|
| **Просмотр анализа** | Redirects to the analytics detail page |

### Notification

| Action | Description |
|---|---|
| **Пометить прочитанным** | Marks selected notifications as read |
| **Отправить в мессенджер** | Sends notification via Telegram messenger |

### LLMProvider

No custom actions (managed via CRUD).

### LLMModel

| Action | Description |
|---|---|
| **🧪 Тестировать модель** | Sends a test prompt to the model (mock or real) and displays the response, latency, token usage, and estimated cost. Can auto-update model prices from the response. |

### Задача (AgentTask)

| Action | Description |
|---|---|
| **Выполнить сейчас** | Enqueues the selected task's job immediately; a one-time (`@once`) task is completed (deactivated) in the process |

The task create/edit form exposes **Источники** (multi-select over the
`agent_task_sources` m2m table) and **Сценарий бота** (`agent_scenario_id`) in
addition to the `payload` JSON (flat keys such as `period`, `monitored_users`,
`excluded_users`).

---

## Analytics Dashboard

Two custom dashboard pages (outside sqladmin):

### Main Dashboard

```
GET /dashboard
```

Displays aggregated analytics:
- Sentiment trends
- Top topics
- LLM statistics and costs
- Content mix
- Engagement metrics

Requires authentication via session (redirects to `/admin/login` if not logged in).

### Topic Chains Dashboard

```
GET /dashboard/topic-chains
```

Displays topic chain evolution with:
- List of topic chains
- Topic evolution over time
- Links to sources in social networks
- Timeline of analyses

---

## Static Files

```
GET /static/<file>
```

Serves static assets (logo, CSS, JS) from `app/static/`.

---

## Configuration

### Enable/Disable

Controlled by `ADMIN_ENABLED` env var (default `true`).

### Templates

Custom templates are loaded from:
1. `app/templates/` (project templates)
2. `sqladmin/templates/` (sqladmin default templates)

Custom create/edit/detail templates:
- `sqladmin/source_create.html` / `source_edit.html` / `source_details.html`
- `sqladmin/bot_scenario_create.html` / `bot_scenario_edit.html`
- `sqladmin/scenario_prompts.html`
- `sqladmin/ai_analytics_detail.html`
- `llm_provider/create.html` / `edit.html`
- `llm_model/test_results.html` / `test_error.html`

### Template Globals

All templates have access to:
- `csrf_token()` — generates a new CSRF token
- `settings` — application settings
- `debug` — debug mode flag

---

## Security

### Session Authentication

- Uses sqladmin's `AuthenticationBackend` with session storage
- Token stored in browser session cookies
- Configurable timeout via `LOGIN_TIMEOUT_MINUTES`

### Permission Checks

- All admin operations run with `PlatformScopeMiddleware` (bypass tenancy)
- The admin panel is the operator's console, not a client API
- Password reset requires the user to exist and have admin-like permissions

### Password Policy

Password changes enforce the same policy as API:
- Minimum length: `PASSWORD_MIN_LENGTH`
- Uppercase, lowercase, digits required
- Special characters optional (`PASSWORD_REQUIRE_SPECIAL`)

---

## Architecture

```
app/admin/
├── __init__.py          # Empty
├── actions.py           # LLMModelActions (test model logic)
├── auth.py              # AdminAuthBackend (session auth)
├── base.py              # BaseAdmin (common config)
├── csrf.py              # CSRFTokenManager
├── endpoints.py         # Custom endpoints (reset-password, change-password, dashboards)
├── middleware.py        # CSRF middleware for non-admin paths
├── setup.py             # setup_admin() — initializes sqladmin, registers views
├── views.py             # All admin view classes (UserAdmin, SourceAdmin, etc.)
└── widgets.py           # Custom WTForms widgets (EuropeanDateField)
```

### Setup Flow

```python
# app/admin/setup.py
def setup_admin(app):
    # 1. Create CSRF manager
    # 2. Include admin router (custom endpoints)
    # 3. Create AdminAuthBackend
    # 4. Create sqladmin instance
    # 5. Register all view classes
    # 6. Mount static files
    # 7. Attach admin to app.state
```
