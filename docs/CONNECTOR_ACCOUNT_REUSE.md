# Personal accounts and explicit workspace access

## Decision and delivery status

Authorize a personal account once, then explicitly attach that authorization to
another workspace of **the same application user**, where the provider permits
reuse. Do not require another Telegram code/2FA challenge just to create a second
workspace grant for an already locally usable authorization. Do not clone a
StringSession, access/refresh token or API key per workspace.

This P12-T14 draft implements encrypted persistence, explicit consent/reuse and
local disconnect/revoke operations for `telegram` / `mtproto`, plus a bounded
phone/code/2FA **machine API**. It includes partial personal-account and workspace-grant web pages, but does **not**
yet implement a source picker, source binding/runtime secret resolution or safe
shared-session execution.
No public endpoint accepts a StringSession. Legacy vault sessions are not
imported. Do not deploy this slice or present it as completed Telegram onboarding.

The current collector still reads the legacy user-scoped vault; it has not been
switched to these grants. That path does not enforce workspace consent and remains
a rollout blocker. The shared Bot API transport is a different deployment identity
and does not lend a personal MTProto account to workspace members.

## Expected product journey

1. In the current workspace choose a connector. Offer only **my existing
   accounts** for a provider/auth-method whose adapter explicitly allows reuse.
2. If an eligible account exists, choose **Use this account in this workspace**.
   This is explicit target-workspace consent; it reuses the encrypted personal
   authorization and creates/reactivates only a local grant. No new provider
   authentication or secret entry is required by that operation.
3. Otherwise complete the connector's supported authentication/key-verification
   journey. A provider-verified stable subject (Telegram `get_me().id`, not typed
   phone, username or email) establishes the identity. First authorization and
   its first workspace grant must commit atomically.
4. Each source must explicitly select one authorized grant in its own workspace,
   bound to its selected owner. No "first account", login-string inference,
   workspace-owner substitution or ambient personal-vault fallback is allowed.

Creating/joining a workspace does **not** attach every existing personal account.
The system may discover eligible accounts for their owner automatically, but
attaching one is an explicit action. There is no remembered/bulk auto-attach policy
in this slice. Granting workspace access never grants other members permission to
read the owner's account catalog, credentials or manage their grants.

A matching typed login is not account proof. Two application users may separately
verify the same provider account; their authorizations stay independently owned.
A grant may never point to another application user's authorization, even if
usernames, email addresses or provider subject IDs match.

## Storage and integrity

- `personal_connector_authorizations`: one encrypted credential bundle for
  `(owner_user_id, provider_id, auth_method, subject_key)`. A revision, state and
  provider-verified subject are metadata; ciphertext is never projected.
- `workspace_authorization_grants`: `(tenant_id, authorization_id)` identifies one
  explicit consent. The composite foreign key `(authorization_id, owner_user_id)`
  prevents a grant for another person's authorization. It records the consented
  authorization revision and an independent active flag.
- The Fernet envelope binds material to purpose, format, authorization ID, owner,
  provider, method, subject and revision. Copying a ciphertext to another row
  fails integrity checks. Attach decrypts/validates locally but returns no secret.
- Existing `workspace_connections` / `workspace_connection_secrets` remain the
  mail/disk configuration contract. They are **not** silently converted to
  reusable personal authorizations. This avoids changing unrelated integrations.

The bounded login adapter constructs the internal `VerifiedTelegramResult` only
after completed authentication and Telegram `get_me()` verification. Login settlement
persists the authorization, first grant and terminal attempt state atomically;
the internal completion helper is not a caller-supplied session import. A duplicate subject cannot silently replace an
existing session or reactivate revoked grants. Explicit reauthorization, including
revision/reconsent behavior, is still a required integration gate.

## Permission and transaction contract

Workspace operations check the current actor and tenant context against fresh
active user, workspace, web membership and assigned workspace role. No
platform-admin bypass is used. Lists are owner-only; reusable account metadata
deliberately does not enumerate source workspaces. Attach/initial persistence
require workspace `source.create` plus `source.view`. A viewing member can detach
their own previous consent without needing CREATE. No client-supplied owner/tenant
is used.

Account self-service `GET /personal` and `POST /{authorization_id}/revoke` instead
require an active Bearer-JWT user, fresh locked active-user check and owner-only
account lookup, with **no tenant context or bypass**. They remain reachable after
all memberships disappear or workspaces are suspended. Tenant headers do not
select/expand a workspace on these two routes. The personal list includes own
Telegram/MTProto identities in active/reauth/revoked states for recovery/revocation,
but no credential ciphertext, grants, source workspaces or provider-health claim.
Local revoke needs no encryption key or decryption: lock the owned authorization,
check the exact revision, clear ciphertext and invalidate all its own grants in
one transaction. Transitioning to revoked advances revision; repeating with the
current revoked revision repairs ciphertext/active-grant inconsistencies without
advancing again. An old revision remains unavailable. No Telegram API/logout is
called. The existing workspace-scoped service method remains for internal callers;
the HTTP revoke now uses the independent account scope. A separate session-authenticated
personal web page now lists metadata and offers confirmed local revoke without a
workspace; same-subject reauthorization/reconsent remains undelivered.

### Personal web metadata and local revoke (partial)

`GET /app/settings/telegram-accounts` uses the active browser session, not a
Bearer-only request, and lists only owned Telegram/MTProto metadata from the new
store. It skips workspace and membership lookup for this exact path. The separate
`POST /app/settings/telegram-accounts/{id}/revoke` requires a bounded URL-encoded
form containing exactly `_csrf`, `expected_revision`, and `confirm=revoke-local`;
its CSRF token is bound to session, actor and this surface. Only exact GET/POST
paths have the no-workspace exception. Ownership and revision are rechecked by the
account service; stale, missing and foreign records produce the same opaque error.
Responses are no-store with no-referrer. An unconfirmed outcome is not presented
as a success or rollback. Local revoke clears new-store ciphertext and invalidates
its grants; it does not end a Telegram session or change the legacy vault. Status
is local metadata, not evidence of provider health. Login, recovery and source
selection are not provided here. Workspace grant controls are separate below.

### Workspace grant web controls (partial)

`GET /app/settings/telegram-accounts/workspace` lists only the current actor's
reusable Telegram accounts and local grants in the current workspace. Explicit
POST forms attach a selected account/revision or detach a selected grant; each
requires confirmation and a session/actor/workspace/surface-bound CSRF token.
Workspace selection comes only from middleware, never from form fields. The
service rechecks current membership and permissions transactionally; a viewer
can withdraw their own grant but cannot attach. Stale or foreign selections are
opaque; unknown mutation outcomes require a fresh list before any retry. The
responses are no-store/no-referrer. No provider request, remote logout or secret
copy is performed by these controls. The lists are separate reads, not an atomic
snapshot or a provider-health guarantee. Source picker, collector enforcement,
reauthorization and assembled DB/browser integration remain incomplete.

Authorization-row locks serialize attach/revoke before grant-row locks; unique
constraints protect concurrent inserts. Retrying attach with the same revision
returns the same grant ID, not another secret or consent row. If revoke races with
attach, it either follows and invalidates that grant, or attach observes the
revoked/new revision and is refused. Encryption failures roll back both new rows.
An old revision cannot authorize a new attachment after personal revocation.

| Action | Local effect | Provider effect |
| --- | --- | --- |
| Attach existing account | Create/reactivate only the selected workspace grant | No provider call, login, secret copy or new session |
| Disconnect here | Deactivate only this workspace grant | No logout; other workspaces retain their grants |
| Revoke personal authorization everywhere | Increment revision, clear the sole encrypted bundle and deactivate all its grants atomically | No remote Telegram session revocation; that is a separate explicit operation |
| Remove workspace membership | All operations in that workspace are denied on fresh authorization checks | The personal account and independently consented grants in other workspaces are not implicitly revoked |

Metadata state `active` means locally recorded authorization, **not** verified
current provider health. Corrupt/unreadable bundles, wrong/missing encryption key,
revoked/reauth state, unknown methods and stale revisions cannot be attached.
Expired/invalid provider sessions must later fail closed in the execution adapter
and request recovery; no local-only check can promise a new login is unnecessary.

## Source-grant inspection foundation (not wired to collection)

`telegram_grant_access` defines a strict internal pin for an existing source:
`params["telegram_authorization"] = {"grant_id": <positive integer>,
"authorization_revision": <positive integer>}`. The source ID, workspace,
platform, external target and selected owner are also pinned. A queued pin must
not silently follow source retargeting, owner changes, a different grant or a
new account revision. There is no first-account discovery or legacy-vault fallback
inside this checker. Numeric strings/booleans, unknown binding fields, missing
selection and push-only mode are refused. Explicit `token_owner` is validated
without falling back to the recorded creator when invalid.

The metadata-only inspector requires the exact workspace context and refuses
operator bypass. Each call issues a fresh joined SQL read for active source,
Telegram platform, workspace, user, web membership and current workspace role;
`source.view` is required for the owner, independent of the job initiator/payer's
execution rights. It checks grant tenant/owner, active consent, matching grant
and authorization revisions, active Telegram/MTProto account and nonempty stored
ciphertext. It re-parses the current persisted source and compares the complete
pin. All selection denials use `connection_unavailable`; no subject or secret is
returned. The checker selects no authorization ciphertext and never decrypts or
contacts a provider.

**This is a snapshot, not an execution permit, secret resolver or lease.** It
cannot establish envelope integrity or provider health and does not prevent a
change after the read. It is not yet called by the collector or a public source
picker. The existing collector remains legacy-vault based. Complete the binding
write/CSRF UI, durable shared-session lease, in-lease decryption, request-boundary
fresh checks and output/admission fencing together before switching runtime or
claiming workspace-consented collection. The new PostgreSQL integration tests
are authored for the local test-runner; remote unit/SQL-compilation tests do not
prove their behavior. No migration or existing source binding is changed here.

## Draft machine API

All paths below require an active Bearer JWT under `/api/v1/connector-accounts`.
Except `GET /personal` and `POST /{authorization_id}/revoke`, they are workspace
routes: `X-Tenant-Id` selects an active membership and never expands access. The
two account-self-service routes do not resolve any workspace. The middleware
exemption matches only those exact method/path shapes, never a whole prefix;
attach, grant listing/detach and login still require active workspace membership.
These routes do not use cookie authentication; a future web form must also use
the existing CSRF protection and reject duplicate/forged scope fields.

| Method and suffix | Operation |
| --- | --- |
| `GET` root | Own reusable account metadata and `can_attach`; no decryption/health promise |
| `GET /personal` | Own account-level identity metadata, including unusable/revoked accounts; no workspace required |
| `GET /grants` | Own consent records in the current workspace |
| `POST /{authorization_id}/attach` | Explicit reuse, body `{"expected_revision": 1}` |
| `POST /grants/{grant_id}/detach` | Local disconnect; no credential material |
| `POST /{authorization_id}/revoke` | Account-level personal-wide local revoke; no workspace required, body `{"expected_revision": 1}` |
| `POST /telegram/login` | Begin: UUID `request_id` plus E.164 `phone` |
| `GET /telegram/login/{attempt_id}` | Own workspace-bound attempt metadata; no phone or secrets |
| `POST /telegram/login/{attempt_id}/code` | Strict positive `expected_revision` plus `code` |
| `POST /telegram/login/{attempt_id}/password` | Strict positive `expected_revision` plus 2FA `password` (spaces preserved) |
| `POST /telegram/login/{attempt_id}/cancel` | Cancel locally and wipe encrypted attempt state; no remote logout |

Account mutation selections and code/password submissions require a strict positive integer revision and reject extra
fields, including owner, tenant and secrets. Errors never echo rejected input or
provider exception text. An inaccessible/foreign/stale/locally unreadable selection
has the same `404 connection_unavailable` answer. Middleware authentication and
membership failures retain their existing 401/403 policy.

### Response contract and client migration

The mounted OpenAPI declares explicit metadata-only response models. Existing
successful JSON keys, status 200 and nullable fields are retained:

- Account: `authorization_id`, `provider_id`, `auth_method`, `subject_key`,
  `revision`, `state` (`active`, `reauth` or `revoked`).
- Grant: `grant_id`, `authorization_id`, `authorization_revision`, `is_active`.
- Root list: `accounts` (account records), `can_attach`; personal/grant lists are
  arrays, not wrappers.
- Login: `attempt_id`, `state`, `revision`, `expires_at`, `failure_code`,
  `code_attempts_remaining`, `password_attempts_remaining`, `authorization_id`,
  `grant_id`. IDs may be null until completion. `expires_at` retains the previous
  ISO datetime spelling (including UTC `+00:00`, not a forced `Z`).

Unlisted service fields are filtered at serialization, including nested account
records. Invalid internal projections fail with a fixed
`500 {"detail":"account_response_unavailable"}` rather than raw validation input.
This is an HTTP field allowlist, not proof of provider health or safe arbitrary
text inside a declared field. Service projections must still emit only trusted
metadata/fixed failure codes. Request validation stays
`422 {"detail":"invalid_account_selection"}`; auth/membership 401/403 and opaque
selection 404 policy do not change. Start/submission document 409/503 and start
also 429. Do not blindly retry conflicts, unknown provider outcomes or rate limits.

A 200 login response may be `processing`, `code`, `password`, `completed`,
`failed`, `expired` or `cancelled`. Consumers must inspect state/revision and
nullable IDs, not treat every 200 as an authorized connection. A failed provider
step remains state metadata, not an invented transport-success guarantee.

Legacy `POST /api/v1/credentials/login` now returns
`409 workspace_telegram_authorization_not_available`: it must never run terminal
`input()` in an HTTP worker. API consumers need to handle this explicit unsupported
state and deliberately switch to the new bounded API; this is not a compatible
replacement request body. Do not substitute the legacy CLI session writer for a
workspace-consented web login. The legacy operation is marked deprecated and its
OpenAPI response is 409, not the former misleading 200 credential schema. No
legacy request fields, disable aliases or authentication policy change here.
DB-exempt JWT/ASGI and full-application OpenAPI checks exercise these contracts
with fake services. They do not replace real consumer migration, database-backed
HTTP/transaction checks or full migration-history validation.

## Bounded login state and failure policy

The phone is accepted only by the initial API request, never a URL or identity
lookup. The app's `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` are server configuration,
not user-supplied credentials or a fallback personal account. `CREDENTIALS_KEY`
must be available before any provider request. DEBUG/SQL result logging is refused
for login start/submission; infrastructure/APM/body capture must also be disabled
for these private endpoints. Never put phone, code, password or session in logs.

- A DB attempt lasts **10 minutes total**; code/2FA steps do not extend expiry.
  Phone, application credentials, temporary StringSession and phone-code hash are
  one Fernet envelope bound to purpose, attempt ID, owner, tenant, revision and
  state. Codes and passwords are never persisted; responses omit all material.
- At most one pending attempt per application user across workspaces; the DB
  partial unique index and actor advisory lock work across HTTP workers. At most
  three starts in ten minutes, five code submissions and three password submissions
  per attempt. A same-user/same-workspace `request_id` replay returns the existing
  metadata, regardless of new input, without sending another code. Use a new UUID
  for an intentionally new login after termination; it does not bypass limits.
- State is `processing -> code -> processing -> password -> processing -> completed`
  (2FA is optional). Each network step is durably claimed **before** I/O. The
  revision and phase must match. Invalid code/password returns the same challenge
  phase with a new revision, within its limit. A concurrent or repeated submission
  is `409 login_conflict`; a replay never silently repeats a provider operation.
- Network work holds no DB locks. Provider work has a 20-second async deadline,
  disconnect has a separate five-second bound; the application does not retry an
  ambiguous step. Telethon client request/connection retries and flood sleeping
  are disabled. Fixed provider failure codes contain no exception/request text.
- A crash, DB failure after provider I/O or hard worker termination can leave
  `processing`. Another worker can read/cancel it but cannot resume/replay that
  step. Wait/cancel/restart explicitly. A fresh DB transaction rechecks membership,
  rights, expiry and revision before installing a verified session. Cancellation,
  expiry, revoked membership or stale claims cannot install a late result.
- Terminal, cancelled and observed-expired attempts have ciphertext cleared.
  Expiry is enforced on access/settlement and old pending attempts are cleared on
  the owner's next start. A running worker also starts independent maintenance:
  it locks at most 200 expired pending rows with `SKIP LOCKED`, changes them to
  `expired`, advances their revision and clears ciphertext in one transaction.
  It repeats immediately for full batches (one-second pause) and otherwise every
  minute; failure logs a fixed category and retries. This does not require the
  scheduler, decrypt attempts or contact Telegram. Multiple workers may overlap.
  Timing is best-effort, not a hard deletion deadline: worker downtime, locks,
  overload or database errors delay cleanup. Monitor
  `telegram_login_maintenance_failed`, restore database/worker health and verify
  pending expired-row counts without selecting ciphertext; do not replay provider
  operations. Attempt metadata and request keys remain to prevent start replay.
  Database pages, WAL and backups may retain old ciphertext; no physical erasure
  or backup deletion is claimed. PostgreSQL race/rollback and operational recovery
  validation remain rollout gates.
- Unknown outcome means local failure, not proof Telegram did nothing. Cancelling
  or timing out during a remote successful sign-in can leave an unretained provider
  authorization; review Telegram active sessions separately. No automatic logout
  is attempted, especially not against another retained/shared authorization.
- A provider-verified subject already owned by this app user returns terminal
  `account_already_exists`; it never replaces an existing bundle or revives grants.
  Choose the existing account explicitly. Invalid/unreadable existing accounts
  still require the separate revision/reconsent recovery journey (not yet built).

Successful terminal states and provider/local failures are returned as metadata
with `state`/`failure_code`; HTTP 200 does not itself mean authentication succeeded.
Clients must branch on `state`, retain `attempt_id` and latest `revision`, and never
submit another challenge while `processing`. Local request errors use fixed
422/409/429/503 details; foreign attempt IDs return 404. No secret appears in a
redirect URL or validation response. This API has authenticated ASGI coverage,
not a delivered browser form or live-provider validation.

## Provider policy: reuse is not universal

Telegram personal MTProto authorization is the sole capability allowlisted by
this foundation. Reuse for OAuth, application passwords, bot tokens, tenant-bound
enterprise installations and other keys must be approved per adapter, scopes,
audience and provider rules; matching connector/account labels alone is not enough.
Mail, VK, disks and future business connectors do not gain reuse through this PR.
A provider that requires another workspace installation/consent must run that
specific flow rather than presenting personal-session reuse as supported.

Sharing an MTProto auth key also does not authorize unrestricted parallel client
use: the runtime must serialize/leash sessions across workers, honor Telegram's
session/IP restrictions and test disconnect/revocation during jobs before rollout.
A reused refresh token likewise needs one transactional refresh owner, not a copy
in every workspace. Grants isolate consent, not all provider-side session effects.

## Migration, rollback and remaining gates

Draft revision `p12t14_accounts` follows encrypted-refresh revision `0124`, creating only the
two new empty tables, constraints and sequences. It deliberately imports no
legacy sessions/keys, creates no generic secret CRUD grants and skips unrelated
catalog reconciliation. The unified P12-T14 branch has one migration head: `0123` -> `0124` ->
`p12t14_accounts` -> `p12t14_tg_login`. The latter adds only the empty encrypted
login-attempt table and indexes. Earlier slice-only sibling graphs are historical and must not be
applied as a separate combined release. This does not authorize deployment.

Validate upgrade/downgrade on a dedicated synthetic schema first. Downgrade drops
new authorizations and grants and is destructive; securely back up any approved
retained data before separately authorized apply/rollback. No public migration,
key rotation, live credential inspection/import or provider revocation is part of
this slice. The new tables are not accessed by the legacy collector, so rollback
of this foundation does not change legacy session selection.

Still required for P12-T14 completion:

- PostgreSQL concurrency/rollback validation, metadata retention policy and operational recovery for expired attempts;
  the bounded machine API alone is not a complete web onboarding journey.
- Complete login, reauthorization/reconsent, account/grant settings and source-grant
  picker UI. The personal metadata/local-revoke page works without memberships, but
  it is not the assembled onboarding or workspace consent journey.
- Runtime grant resolution with fresh owner/membership/revision checks, shared
  session execution locking, cancellation and revoke/disconnect semantics; exercise
  queued jobs and membership changes. No fallback to legacy/global accounts.
- Explicit same-subject reauthorization/recovery with revision and grant consent
  semantics, including unreadable key and invalid provider session cases.
- Assembled authenticated web/DOM journey tests with fake provider, migration graph
  integration and independent review. Existing service/ASGI tests are not proof of
  a completed web authorization or a live Telegram connection.
