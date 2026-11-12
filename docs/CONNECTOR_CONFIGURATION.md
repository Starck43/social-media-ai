# Provider-specific workspace connections

Mail.ru Mail and Yandex Mail can be configured at
`/app/settings?tab=connections`, in the existing single Email card. This delivers
**configuration, encrypted storage and an explicit IMAP login check**, not a mail
client, synchronization job, outbox or SMTP sender. Saving never calls a provider.
VK OAuth/manual user tokens retain their existing personal flow. Telegram
workspace consent and same-user cross-workspace reuse are specified in
[personal account reuse](CONNECTOR_ACCOUNT_REUSE.md); that draft foundation is not
yet a completed authorization/runtime journey. Mail configuration does not gain
credential reuse implicitly. A descriptor alone is not an adapter; see the provider-specific sections below.

## Storage boundaries

| Layer | Owner and implementation |
| --- | --- |
| Shared platform configuration | Global `Platform.params`: existing platform API endpoints, versions and OAuth URLs, managed by the operator; no new credentials. |
| Provider/auth/field contract | Code-owned, versioned `app/integrations/catalog/`; immutable provider defaults, human labels, instructions, secret classification and shared server validation. Mail presets are fixed allowlisted provider destinations, not arbitrary runtime URL overrides. |
| Connection instance | `workspace_connections` (`WorkspaceConnection`): explicit tenant and personal owner, platform/provider/method, schema version, label, normalized account identity, non-secret config and check state. |
| Connection secret | Separate one-to-one `workspace_connection_secrets`, encrypted with Fernet under `CREDENTIALS_KEY`; FK to the connection, never a caller-selected credential reference. Deployment OAuth client secrets stay in deployment configuration. |

Only entries explicitly included in `WORKSPACE_PROVIDERS` use the generic mail settings flow.
Provider-specific flows have their own explicit managers and policies. A new
provider needs a reviewed field contract **and** an implemented, tested adapter
and authorization/network policy. Increase the schema version deliberately when
changing the saved contract; unknown versions fail closed on verification.

## Operator journey

1. Open Settings → Connections → Email; choose **Mail.ru** or **Yandex Mail**.
   Provider selection is explicit: a domain (including `@vk.ru`) is not guessed
   or treated as proof of account ownership. Select the actual hosting service.
2. Enter a recognizable connection name, full mailbox address and a dedicated
   **application password**, never the normal account password.
   - Mail.ru: Mail → Settings → Security → Passwords for external applications;
     choose access to Mail, not Cloud.
   - Yandex: enable IMAP and application passwords/OAuth in mail-client settings;
     create an application password for Mail in Yandex ID.
3. Save. The record is **unverified**. A second mailbox can be added independently.
4. Press **Check IMAP login** to explicitly authorize one remote login check.
   It opens TLS, logs in and closes the socket. It does not select folders,
   read messages, mark them read, send mail or invoke the P12-T04 outbox.
5. Edit to replace the address/name/password; the password must be re-entered and
   any previous successful check is cleared. Changing provider/method requires
   a new connection. Disable clears the local secret and prevents checks;
   recovery requires a new password. Revoke the app password at the provider
   too if remote access must be revoked.

Presets use `imap.mail.ru:993` and `imap.yandex.ru:993`, with certificate-verified
TLS. SMTP host/port defaults describe the provider but `send_enabled` is always
false; no SMTP transport is implemented here. For `@yandex.ru`, IMAP uses the
local part as the login per vendor instructions; other mailboxes retain the full
address. Custom servers, automatic domain aliases, folder selection, SMTP,
mail OAuth and generic WebDAV forms are not offered.

The separately implemented internal customer sender in
[CHANNELS.md](CHANNELS.md#customer-communications-senders-p15-t06) does not add
SMTP to this settings flow: it remains IMAP-only and `send_enabled` stays false.
Its mail, Telegram and MAX capabilities require their own exact binding release
and independent kill switch; this connection check neither releases a capability
nor enables an outbox, runtime sender, or live send.

Vendor references:
[Mail.ru mail clients](https://help.mail.ru/mail/login/mailer),
[Yandex mail clients](https://yandex.ru/support/yandex-360/customers/mail/ru/mail-clients/others).

## Authorization, networking and state

The public API for these records is `workspace_connection_manager`. It rechecks
an authenticated matching actor/context, active user, active workspace, active
web membership and an assigned workspace role on **every** operation. Read lists
are personal-owner + tenant filtered. Create/edit/check/disable additionally
require the fixed workspace `source.create` grant (owner/admin/manager); platform
admin status is not a bypass. No other member's connection or secret is exposed.
Unknown/mismatched fields, methods, providers, duplicate form keys, forged
workspace/endpoints and foreign connection IDs are rejected. All writes use CSRF.
Projection is an explicit non-secret allowlist; secret inputs are never populated.

Connection and secret creation/update share one transaction. A database unique
constraint on `(tenant, owner, provider, method, account_key)` prevents concurrent
duplicate accounts; distinct accounts and the same account in another workspace
are independent. Disabled records retain the identity: edit/recover instead of
creating a duplicate. Auth rows are share-locked and the connection is row-locked
through check/edit/disable; concurrent mutation cannot let a late successful
check revive a disabled/changed connection. Cancellation waits for the socket
worker before releasing authorization locks. This serializes operations, not
remote provider revocation; a successful check is only a timestamped observation.

The verifier ignores stored endpoint overrides. It resolves the fixed catalog
host, rejects any non-global DNS result, pins the connection to a validated IP,
and verifies TLS against the original hostname (SNI). There are no redirects or
custom/local gateways. Socket operations have an 8-second timeout; OS DNS timing
is governed by the host resolver. Remote exception text is never surfaced or
logged; only fixed check codes/messages are stored. No paid calls or external
writes are required in tests.

States are `unverified`, `verified` (IMAP login only), `error`, `reauth`, `disabled`.
Only an actual successful login sets `verified`; save/edit clears the timestamp
and check code. An expired/revoked password produces `reauth`; connectivity/TLS
failures produce `error`. A stored secret is not proof of an active connection.

## Upgrade, legacy data and rollback

Apply **Alembic 0121 after 0120**, using the standard deployment migration
procedure and an authorized schema. Validate both directions on the isolated
`test_schema` before separately authorizing any public/production migration.
0121 creates only the two new tables, constraints and sequence; no new permission
catalog rows or implicit generic CRUD grants. The migration hook deliberately
skips unrelated catalog reconciliation for this revision.

Existing `UserCredential` rows are not copied, relabeled or rebound to tenants.
Personal VK/Telegram consumers remain unchanged. Legacy business secrets remain
in the personal vault and may be disabled by their owner in Settings; they do
not become usable workspace mail connections. New business secret-only creation
through the old settings form is rejected. Existing non-web legacy APIs are not
rewritten by this task and do not resolve the new connection secrets.

Downgrading to 0120 **destroys the new connection configuration and secrets**.
Back them up securely first; do not put plaintext in an export or Git. No
personal credential or existing source is changed. Roll back the application
and schema together: new application routes require 0121. Tests cover real
upgrade/downgrade/re-upgrade, legacy preservation, failure atomicity, ownership,
revocation, concurrent duplicate/check/disable behavior and provider mocks.

### VK: recovery after an unreadable refresh secret

A `refresh_attempt` exactly equal to `{"state": "unreadable"}` records a local
failure **before** sending a refresh request. It does not establish that the VK
session is renewable. The operator may, after separate owner authorization and
with a reviewed restorable backup, stop **all** credential writers and make sure
that the correct credential key is consistently installed on every instance.
Never inspect a real target merely to discover whether recovery is needed.

For the explicitly approved user and current credential row, run the default
probe first (IDs are identifiers, not secrets):

```bash
python -m scripts.ops.recover_vk_unreadable --user-id USER_ID --credential-id CREDENTIAL_ID --target-approved --writers-stopped
```

If the probe succeeds, the separately authorized mutation adds `--apply
--backup-confirmed`. It locks the user and newest active VK credential, checks
the expected row ID and decrypts both encrypted secrets without displaying them.
Only the exact known-unsent marker is removed; pending, failed, extended or
unknown markers are never cleared. Other metadata and failure cooldowns remain.
No VK request is made. A successful probe does not verify provider state; the
status is still subject to expiry and any other failure. Coordinate the key on
all instances before restarting writers. If the target changed, key is wrong or
outcome is uncertain, stop and investigate; do not blindly retry or clear a
claim manually. This is not a substitute for reauthorization or key rotation.

## OAuth 2.0 workspace-connection framework (P13-T08)

Provider-neutral OAuth 2.0 authorization-code + PKCE + refresh (`app/integrations/oauth/`)
extends `app/integrations/catalog/` with `OAuthMethod(authorize_url, token_url, scopes, pkce, redirect_path, client_config_keys, revoke_url, allowed_hosts)`:

- **Deployment client credentials:** `client_id` and `client_secret` are resolved from deployment configuration (`client_config_keys`), never accepted from form input or stored in `workspace_connections.config`. A global application (Bitrix24, P14-T07) leaves them empty and resolves through `app/integrations/oauth/credentials.py` instead — see below.
- **Single-use state + PKCE (`S256`):** `start_authorization` issues a short-lived single-use `state` bound to `(tenant_id, actor_user_id, provider_id, connection_id)` and PKCE `code_verifier`. Replayed, expired, cross-tenant, cross-actor, or foreign-connection callbacks fail closed.
- **Global DNS and TLS enforcement:** `validate_oauth_url` resolves hostnames and rejects loopback, RFC1918 private, link-local, non-HTTPS, credentialed URLs, untrusted hosts, and HTTP redirects (`follow_redirects=False`).
- **Encrypted secret storage & serialized refresh:** Access/refresh tokens and expiry are stored exclusively as Fernet-encrypted JSON inside `workspace_connection_secrets.secret_encrypted`. `refresh_tokens` holds a per-connection async lock and row lock (`SELECT ... FOR UPDATE`), performing at most one provider HTTP call under concurrent callers. Refresh rejection (`invalid_grant` / `400` / `401`) transitions `workspace_connections.state` to `reauth`, clears the secret, and creates an `action_required` `Notification` (`SOURCE_AUTH_REQUIRED`).
- **Disconnect:** `disconnect` revokes the token at the provider when `revoke_url` is configured, clears `secret_encrypted`, and marks the connection `disabled`.

## Bitrix24 portal connection and health (P14-T01)

Per Owner Decision D2 ([Q-18](audit/q-18-owner-decisions.md)), Bitrix24 connections (`app/integrations/bitrix24/`, `/api/v1/integrations/bitrix`) use **OAuth 2.0 local portal applications** (`https://<portal>/oauth/authorize/`, token exchange at `https://oauth.bitrix.info/oauth/token/`), with an **admin-only** incoming webhook alternative (`auth_method="webhook_secret"`, restricted to workspace `owner` and `admin` roles):

- **Portal domain validation (`normalize_portal_domain`):** Accepts cloud Bitrix24 suffixes (`*.bitrix24.ru`, `*.bitrix24.by`, `*.bitrix24.kz`, `*.bitrix24.com`, `*.bitrix24.eu`, `*.bitrix24.de`, etc.) and explicitly allowlisted on-premise domains. Rejects HTTP URLs, IP literals, ports, paths, and untrusted domains.
- **Non-secret config vs. encrypted secrets:** `WorkspaceConnection.config` stores only `portal_domain`, `member_id`, and `scopes`. OAuth tokens and webhook secrets live exclusively in `WorkspaceConnectionSecret.secret_encrypted`.
- **Rate budget (`PortalRateGate`):** Enforces `<= 2 req/s` per portal (`POLICIES["bitrix24"]`). Supports `/rest/batch.json` up to 50 commands (`MAX_BATCH_COMMANDS = 50`).
- **Health check (`check_health`):** Calls `app.info` to verify token validity and record `member_id`, setting `state` to `verified` (`bitrix_healthy`), `reauth` (`oauth_reauth_required`), or `error`.
- **Global-application credentials (P14-T07):** Bitrix24 is registered as a global application outside this product. `resolve_app_credentials` (the single resolver seam) reads an operator-supplied JSON secret file named by `BITRIX24_APP_SECRET_FILE` (entry `BITRIX24_APP_SECRET_NAME`, default `client_secret`, accepting `{"client_id", "client_secret"}`) or an injected provider (`INJECTED_APP_CREDENTIALS`). Source order: injected provider, operator file, then the rollback-only `BITRIX24_LEGACY_ENV_CREDENTIALS` settings bridge (default OFF, ignored whenever `BITRIX24_APP_SECRET_FILE` is set). Any missing, unreadable, malformed or incomplete source fails closed with `bitrix_credentials_unavailable`: no fallback, no secret in logs, errors, settings repr or API responses. Portal domain, install state and tokens remain tenant-scoped in the database.



## 1C connection and health (P17-T01)

A workspace owner or admin approves an exact DNS host in Settings → Connections → 1C before a member with `source.create` can configure a 1C:Enterprise 8.3+ connection. This approval is scoped to the workspace and is independent of the installation-level outbound-host registry. It is provider-neutral but does not itself grant access to a connector or permit private addresses. An actor sees and operates only their own connections in the active workspace; host approval/revocation is restricted to workspace owners and admins.

Use a published HTTPS base URL with an approved host and either standard OData (`/odata/standard.odata/$metadata`) or a published HTTP service exposing `/ping`. Basic login and password are Fernet-encrypted together in the existing `workspace_connection_secrets` row under `CREDENTIALS_KEY`; they are not placed in config or returned by the API. Prefer a dedicated read-only 1C account. Saving does not contact 1C. Health checks perform one bounded, TLS-verified GET and store a status and, for OData, the SHA-256 hash of the response; they do not query business records or write to 1C. Disable removes the local encrypted material; provider-side access must be revoked separately.

Every attempt resolves afresh, rejects non-public, multicast, mixed private/public results, pins the chosen IP and checks TLS for the original host. Redirects, IP literals, URL credentials, query strings and private/loopback destinations are refused, regardless of the installation registry. The timeout is 30 seconds and response body is bounded to 2 MiB. The shared provider budget requires a separately configured `RATE_LIMIT_KEY` and currently spends two tokens per attempt: burst 5, sustained 5/minute; a 10-in-60-second bound requires all consumers of this budget to use the same token cost. No 1C consumer should bypass the shared budget. The budget identity is the endpoint and login, so one 1C account backs one active connection per workspace: a second connection with the same account fails closed with `connection_unavailable` rather than receiving a separate allowance. Keeping that refusal is the recorded policy (see the [P17-T01 audit](audit/p17-t01.md)); widening it to a per-account budget shared by several connections is an explicit future change, never an implicit relaxation. An internal-only 1C base requires the separate future local connector; it is not an exception to these checks.

API endpoints under `/api/v1/integrations/1c`: `POST /connect`, `GET /connections`, `DELETE /connections/{id}`, `PATCH /connections/{id}/credentials`, `POST /connections/{id}/health-check`, and owner/admin `GET/POST /hosts`, `DELETE /hosts/{id}`. API calls require a bearer token and active matching workspace context; web forms require CSRF. The generic `WORKSPACE_PROVIDERS` mail catalog is not the 1C connection manager. This task implements health only; 1C data queries and write operations are separate tasks. A disabled connection retains its unique identity. Use its credential replacement form to set a new login and password and reactivate it; this clears the previous check state and does not contact the provider. Replacing credentials on an active connection likewise resets its health state.

The workspace host migration uses a task placeholder revision; rebase against the then-current Alembic head and assign its final revision at integration. Its downgrade refuses to delete persisted approvals. Before deployment, the isolated-schema runner must verify upgrade, rollback refusal, tenant/admin isolation, host revocation, credential persistence and health/disable/quota concurrency; mocked tests are not evidence of those DB properties.
