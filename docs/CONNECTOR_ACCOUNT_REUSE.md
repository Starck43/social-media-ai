# Personal accounts and explicit workspace access

## Decision and delivery status

Authorize a personal account once, then explicitly attach that authorization to
another workspace of **the same application user**, where the provider permits
reuse. Do not require another Telegram code/2FA challenge just to create a second
workspace grant for an already locally usable authorization. Do not clone a
StringSession, access/refresh token or API key per workspace.

This P12-T14 draft implements encrypted persistence, explicit consent/reuse and
local disconnect/revoke operations for `telegram` / `mtproto`, plus a bounded
phone/code/2FA **machine API**. It does **not** yet implement settings/account-picker
web UI, source binding/runtime secret resolution or safe shared-session execution.
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

Every operation checks the current actor and tenant context against fresh active
user, workspace, web membership and assigned workspace role. No platform-admin
bypass is used. Lists are owner-only; reusable account metadata deliberately does
not enumerate source workspaces. Attach/initial persistence require workspace
`source.create` plus `source.view`. A viewing member can detach/revoke their own
previous consent without needing CREATE. No client-supplied owner/tenant is used.

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

All paths below are authenticated Bearer-JWT workspace routes under
`/api/v1/connector-accounts`. `X-Tenant-Id` selects an active membership; it never
expands access. They do not use cookie authentication; a future web form must
also use the existing CSRF protection and reject duplicate/forged scope fields.

| Method and suffix | Operation |
| --- | --- |
| `GET` root | Own reusable account metadata and `can_attach`; no decryption/health promise |
| `GET /grants` | Own consent records in the current workspace |
| `POST /{authorization_id}/attach` | Explicit reuse, body `{"expected_revision": 1}` |
| `POST /grants/{grant_id}/detach` | Local disconnect; no credential material |
| `POST /{authorization_id}/revoke` | Personal-wide local revoke, body `{"expected_revision": 1}` |
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

Legacy `POST /api/v1/credentials/login` now returns
`409 workspace_telegram_authorization_not_available`: it must never run terminal
`input()` in an HTTP worker. API consumers need to handle this explicit unsupported
state and deliberately switch to the new bounded API; this is not a compatible
replacement request body. Do not substitute the legacy CLI session writer for a
workspace-consented web login.

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
- Explicit account/grant settings UI with CSRF and a source-grant picker. Account
  self-revocation when no active workspace memberships remain also needs a scoped
  account-level journey; this draft API is workspace-scoped.
- Runtime grant resolution with fresh owner/membership/revision checks, shared
  session execution locking, cancellation and revoke/disconnect semantics; exercise
  queued jobs and membership changes. No fallback to legacy/global accounts.
- Explicit same-subject reauthorization/recovery with revision and grant consent
  semantics, including unreadable key and invalid provider session cases.
- Assembled authenticated web/DOM journey tests with fake provider, migration graph
  integration and independent review. Existing service/ASGI tests are not proof of
  a completed web authorization or a live Telegram connection.
