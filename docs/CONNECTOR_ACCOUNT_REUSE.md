# Personal accounts and explicit workspace access

## Decision and delivery status

Authorize a personal account once, then explicitly attach that authorization to
another workspace of **the same application user**, where the provider permits
reuse. Do not require another Telegram code/2FA challenge just to create a second
workspace grant for an already locally usable authorization. Do not clone a
StringSession, access/refresh token or API key per workspace.

This P12-T14 draft implements the encrypted persistence, consent/reuse API and
local disconnect/revoke operations for `telegram` / `mtproto` only. **It does not
yet implement the initial phone/code/2FA login, settings account-picker UI, source
binding/runtime secret resolution or safe shared-session execution.** The trusted
completion seam is not an executable authorization journey: no public endpoint
accepts a session or creates an authorization. Legacy vault sessions are not
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

`record_telegram_result` is an internal persistence seam for a future trusted
adapter. It does not authenticate a caller-supplied session. Its input must be
constructed only after bounded provider authentication and verified identity;
it is not wired to HTTP or CLI. A duplicate subject cannot silently replace an
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

Mutation selections require a strict positive integer revision and reject extra
fields, including owner, tenant and secrets. Errors never echo rejected input or
provider exception text. An inaccessible/foreign/stale/locally unreadable selection
has the same `404 connection_unavailable` answer. Middleware authentication and
membership failures retain their existing 401/403 policy.

Legacy `POST /api/v1/credentials/login` now returns
`409 workspace_telegram_authorization_not_available`: it must never run terminal
`input()` in an HTTP worker. API consumers need to handle this explicit unsupported
state until a real bounded authorization journey is delivered. Do not substitute
the legacy CLI session writer for a workspace-consented web login.

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

Draft revision `p12t14_accounts` follows this branch's `0123`, creating only the
two new empty tables, constraints and sequences. It deliberately imports no
legacy sessions/keys, creates no generic secret CRUD grants and skips unrelated
catalog reconciliation. It is an independent sibling of the unmerged VK `0124`
revision; reconcile the Alembic graph during reviewed integration, before applying
a combined release. Do not infer permission to deploy multiple unmerged heads.

Validate upgrade/downgrade on a dedicated synthetic schema first. Downgrade drops
new authorizations and grants and is destructive; securely back up any approved
retained data before separately authorized apply/rollback. No public migration,
key rotation, live credential inspection/import or provider revocation is part of
this slice. The new tables are not accessed by the legacy collector, so rollback
of this foundation does not change legacy session selection.

Still required for P12-T14 completion:

- Encrypted, expiring, single-use phone/code/2FA attempts with no server `input()`,
  replay/parallel-submit/timeout/cancel/restart/multi-worker handling and no secrets
  in logs, errors, URLs or redisplayed forms.
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
