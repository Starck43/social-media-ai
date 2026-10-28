# Public API route and compatibility inventory

This is a source inventory for P11-T08, read with the mounted route index in
[`generated/api-routes.md`](generated/api-routes.md). The generated index is
metadata only; it does not describe authorization or hidden legacy routes.

## Mounted surface and authentication

`app.main.create_application()` mounts `app.api.v1.entry.router` at `/api/v1`.
Its router groups are `/auth`, `/users`, `/users/roles`, `/monitoring`, `/ai`,
`/notifications`, `/dashboard`, `/llm`, `/social`, `/sources`, `/tasks`, and
`/credentials`. `/health` is mounted separately at `/api/v1/health`. Root
infrastructure probes are separate paths (`/livez`, `/readyz`, `/health`,
`/runtimez`).

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
| Health and legacy probes | `/api/v1/health` is middleware-public; `/api/v1/test-db` is authenticated; root probes are outside `/api/*`. |

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

Some collection responses have explicit bounds: notifications cap `limit` at
100; dashboard source and analytics lists cap at 100, recent notifications at
50, topic chains at 200, aggregate top topics at 50, and task-result summaries
at 10. The users list caps `limit` at 100. In contrast, sources, tasks, and
scenarios return a complete workspace list without pagination; this can grow
with tenant data and should receive backward-compatible pagination before
unbounded growth becomes a resource risk. Preserve existing response shapes
while adding optional query parameters, then document a migration to a paged
response if required.

Expensive or externally observable operations include monitoring collection,
task creation/run/plan execution, digest send, credential diagnostics and
interactive credential login, OAuth start, and LLM provider/model testing or
calls. Current application-level `@limiter.limit` use is limited to the admin
change-password route; there is no general per-actor/workspace admission policy
or request-correlation middleware. A future limit should key by authenticated
actor and resolved workspace, return 429 with a stable code and `Retry-After`,
and use a store with consistent multi-process behavior. Redis must not be enabled
by default while it is frozen.

Correlation IDs are for tracing only and must never serve as idempotency keys.
Task plan launch already has a request key/receipt contract; preserve its
atomic actor/workspace/payload binding and replay semantics. Do not assume the
one-off `POST /tasks/run` or other side-effecting routes have the same typed
validation or replay guarantees. Add idempotency only with explicit payload
binding, expiry, concurrent replay/conflict, in-flight and unknown-effect
semantics; do not infer success when an external effect is uncertain.

## Source references

Inventory checked at `5fd91abd2ed9a2f8dc0a845b459caa8e7fd8d1de` before the
P11-T08 task claim. Relevant sources: `app/main.py`, `app/api/v1/entry.py`,
`app/core/api_scope.py`, `app/api/v1/endpoints/{auth,user,roles,monitoring,
scenarios,notifications,dashboard,llm_providers,llm_models,social,sources,
tasks,credentials}.py`, and `app/services/task_contracts.py`.
