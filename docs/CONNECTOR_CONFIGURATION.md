# Provider-specific workspace connections

Mail.ru Mail and Yandex Mail can be configured at
`/app/settings?tab=connections`, in the existing single Email card. This delivers
**configuration, encrypted storage and an explicit IMAP login check**, not a mail
client, synchronization job, outbox or SMTP sender. Saving never calls a provider.
VK OAuth/manual user tokens and Telegram sessions retain their existing personal
flows. Other business connectors remain unavailable: a descriptor is not an adapter.

## Storage boundaries

| Layer | Owner and implementation |
| --- | --- |
| Shared platform configuration | Global `Platform.params`: existing platform API endpoints, versions and OAuth URLs, managed by the operator; no new credentials. |
| Provider/auth/field contract | Code-owned, versioned `app/integrations/catalog/`; immutable provider defaults, human labels, instructions, secret classification and shared server validation. Mail presets are fixed allowlisted provider destinations, not arbitrary runtime URL overrides. |
| Connection instance | `workspace_connections` (`WorkspaceConnection`): explicit tenant and personal owner, platform/provider/method, schema version, label, normalized account identity, non-secret config and check state. |
| Connection secret | Separate one-to-one `workspace_connection_secrets`, encrypted with Fernet under `CREDENTIALS_KEY`; FK to the connection, never a caller-selected credential reference. Deployment OAuth client secrets stay in deployment configuration. |

Only entries explicitly included in `WORKSPACE_PROVIDERS` have a settings flow.
Other catalog descriptors (VK, Telegram, disks) do not enable new calls. A new
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
