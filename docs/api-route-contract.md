# Public API route and compatibility inventory

This is a source inventory for P11-T08, read with the mounted route index in
[`generated/api-routes.md`](generated/api-routes.md). The generated index is
metadata only; it does not describe authorization or hidden legacy routes.

## Mounted surface and authentication

`app.main.create_application()` mounts `app.api.v1.entry.router` at `/api/v1`.
Its router groups are `/auth`, `/users`, `/users/roles`, `/monitoring`, `/ai`,
`/notifications`, `/dashboard`, `/llm`, `/social`, `/sources`, `/tasks`, and
`/credentials`. The entry router contributes `/api/v1/health`. A separate
infrastructure router is mounted without a prefix and serves `/livez`,
`/readyz`, `/health`, and `/runtimez`; its `/health` does not collide with the
prefixed API route.

`ApiScopeMiddleware` permits only exact normalized paths in
`PUBLIC_API_PATHS`: login, registration, refresh-token, health, and the social
OAuth callback. Refresh still requires a refresh Bearer token; callback checks
signed state. Other `/api/*` paths require an active bearer identity. Workspace
routes resolve `X-Tenant-Id` or `X-Tenant-Slug` only against the caller's active
memberships; these headers select a workspace and do not grant access. Account
self-service and personal LLM catalog routes are explicit authenticated
exceptions to workspace membership. Endpoint model/action checks remain in
place. `GET /api/v1/test-db` is hidden from OpenAPI but authenticated;
`GET /api/v1/test-auth` is also hidden and authenticates through its dependency.

The current route/auth groups are:

| Group | Contract |
| --- | --- |
| Auth | Login, registration, refresh, password change. Only login/register/refresh are middleware-public; refresh validates its refresh token, and password change is account-authenticated. |
| Users and roles | `/users/me` is account-authenticated; user administration and role management require operator or route-specific rights. |
| Sources, tasks, scenarios, notifications, monitoring, dashboard | Active identity, resolved workspace where data is workspace-owned, plus endpoint model/action permission. |
| LLM providers/models | Authenticated owner-scoped personal catalog endpoints; workspace membership is not required. |
| Credentials | Authenticated, owner-scoped credential operations with endpoint credential permissions. Secrets are not returned. |
| Social OAuth | Start is authenticated; callback is exact-path middleware-public and validates signed state. |
| Health and legacy probes | `/api/v1/health` is middleware-public; `/api/v1/test-db` is authenticated; root `/health`, `/livez`, `/readyz`, and `/runtimez` are outside `/api/*`. |

## Compatibility and error behavior

Current error payloads are not a single stable envelope. Framework validation
typically returns `{"detail": [...]}`; `HTTPException` handlers return
`{"detail": ...}` where `detail` may be text or structured data. Some handlers
also return an `error` code (429 uses `rate_limit_exceeded`). Status codes and
detail strings vary by endpoint, and some older endpoints include localized
messages. Clients should branch on status and documented machine codes where
present, not parse prose. Do not silently replace these responses globally.

For a future stable error contract, add an opt-in/versioned envelope while
retaining `detail` and existing status codes during migration. A compatible
shape can add `code`, `message`, `request_id`, and optional field errors;
existing `detail` remains present. Give validation, auth, permission, not-found,
conflict, rate-limit, and internal errors stable codes. Never expose exception
text, credentials, provider payloads, or database details in the envelope.

Unknown paths/methods currently follow FastAPI/Starlette 404/405 behavior.
Middleware must keep the public-path allow-list exact; do not make a subtree
public by prefix matching. Keep `/api/v1` as the existing compatibility prefix.
If a new major API is needed, mount `/api/v2` alongside it and migrate clients
before retiring v1.

The credential disable handler is currently mounted at
`PATCH /api/v1/credentials/credentials/{credential_id}/disable` because both the
parent router and endpoint declare `credentials`. The apparently cleaner
`/api/v1/credentials/{credential_id}/disable` is not currently registered.
Preserve the registered path until a usage/owner decision is made; if correcting
it, temporarily expose both paths and document a deprecation window.

Notification handlers retain hidden `/notifications/...` aliases in addition
to the canonical shorter paths. Preserve both until client usage is checked;
new endpoints should not create further aliases without an explicit
compatibility need. The hidden authenticated `/api/v1/test-db` and `/test-auth`
routes are legacy contracts and should be removed only through an explicit
compatibility decision. `/api/v1/health` is distinct from the root `/health`.

## Bounds and request admission

Existing bounded responses include notifications (max 100), dashboard sources
and analytics (max 100), recent notifications (max 50), topic chains (max 200),
aggregate top topics (max 50), task-result summaries (max 10), and users (max
100). The P11-T08 implementation adds optional `limit` (default 50, max 100) and
`offset` (default 0) to source, task, credential, and scenario lists, retaining
their list response shape. LLM provider/model catalog lists already return an
object with `total`; they now accept the same bounds and keep `total` as the
filtered count before pagination. Clients that previously received every row
must page through results to retrieve the full set.

Expensive or externally observable operations include monitoring collection,
task creation/run/plan execution, digest send, credential diagnostics and
interactive credential login, OAuth start, and LLM provider/model testing or
calls. The P11-T08 implementation adds a per-actor active-launch cap of 3 to
typed one-off task-plan execution. It checks a same-key receipt before applying
the cap and serializes the count/admission inside the existing workspace
PostgreSQL admission lock. A cap rejection is HTTP 429 with `Retry-After: 30`.
This is a database-backed active-work budget, not a general request-rate limit;
there is no new shared Redis dependency.

`RequestContextMiddleware` now echoes a valid `X-Request-ID` containing 1–128
ASCII letters, digits, underscore, dot, colon, or hyphen, and generates a UUID
for absent or invalid values. It wraps API authentication so denied responses
also carry the correlation header. Correlation IDs are for tracing only and
must never serve as idempotency keys.

Task plan launch has a request key/receipt contract; the admission lock checks
same-key replay before the active-work budget, and different-payload reuse
remains a conflict. Do not assume the separate legacy one-off `POST /tasks/run`
or other side-effecting routes have the same typed validation or replay
guarantees. Do not infer success when an external effect is uncertain.

The API path boundary now matches `/api` itself or `/api/…`, rather than any
path that merely starts with those three characters. Operator user-route
classification likewise uses the `/users` segment boundary, so similarly
prefixed unrelated paths do not inherit the operator exception.

## Source references

The route/auth baseline was inventoried at
`5fd91abd2ed9a2f8dc0a845b459caa8e7fd8d1de`; implementation details above
reflect subsequent P11-T08 edits under review. Relevant sources:
`app/main.py`, `app/api/v1/entry.py`,
`app/core/api_scope.py`, `app/api/v1/endpoints/{auth,user,roles,monitoring,
scenarios,notifications,dashboard,llm_providers,llm_models,social,sources,
tasks,credentials}.py`, and `app/services/task_contracts.py`.
