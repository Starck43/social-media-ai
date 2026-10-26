# Notification delivery boundaries

## Personal inbox contract (P4-T02 / P10-T27, PR #246 candidate)

A notification belongs to a **global User**, not to their active workspace or
TenantUser membership. `notifications.tenant_id` records event origin only.
`notification_receipts(notification_id, user_id)` owns personal `read_at`,
`dismissed_at` and optional `snoozed_until`. There is no workspace-wide read action
in the user interface, API or admin. The legacy `Notification.is_read` is archive
compatibility only; historical flags are never copied into invented receipts.

- Bell and `/app/notifications/inbox` combine only the caller's addressed events
  across active own/invited memberships. Current workspace selection never limits
  ownership. Operators have no cross-user override on personal routes.
- Fresh reads and opening links recheck account, receipt owner, expiry, origin
  workspace/membership and linked-resource rights/existence. Unknown, deleted,
  inaccessible or foreign-origin targets are excluded; revocation hides access
  without reassigning receipts to another user. Platform authority is not a
  substitute for a web membership.
- `NotificationService.create(recipient_user_ids=[...])` accepts **User IDs**.
  Without that argument it derives only an active initiating actor in the event's
  origin workspace. `[]` means archive-only. Job outcomes use the trusted persisted
  `Job.initiated_by_user_id`, never a payload's claimed identity. Analyze proposals
  notify only that trusted initiator. Scheduled/unattributed collection events
  remain technical archive entries until an explicit recipient is supplied:
  there is no automatic broadcast to members, owners, invitees or other spaces.
- Events and receipts publish in one transaction. A non-null `dedup_key` is unique
  per origin workspace, across statuses; key reuse with different content or
  recipients is rejected. Producers supply a logical outcome/attempt key (or an
  explicit time-window key for recurring errors). `severity` is info/warning/
  action_required. Service-created events expire after 30 days by default.
- GET/poll creates no rows, probes no providers and changes no read receipt.
  Read/read-all are actor-owned CSRF POSTs (bearer authentication in the API).
  Read-all covers the permitted candidate window, at most 100 events; it never
  changes another receipt. Delete/cleanup affects only the caller's receipt,
  not the shared event or anyone else's inbox. Physical event retention remains
  an operator/P6 policy, not an ad-hoc user purge.
- Bell scans at most 100 receipts across memberships and displays up to 30 entries,
  plus only the user's own connection metadata. Raw workspace failed-task signals
  no longer become personal notifications. Failure is shown as unavailable, not
  as healthy empty state. Display settings and quiet hours are still per-user;
  personal persisted messages cannot be hidden by the obsolete `tasks` signal
  preference key (retained for stored-data/API compatibility).

### Opening a destination in its workspace

The bell/inbox button POSTs `/app/notifications/open/{id}` with CSRF. The server
chooses a whitelisted destination from the owned event, rechecks membership and
entity permission, sets the browser's active workspace and redirects (303).
Neither posted workspace IDs nor posted redirect URLs are trusted.

The API response's `destination` is a signed personal deep link. Its `intent`
binds the global user and notification, expires after seven days, and permits
one-click navigation/context switching only after all the same fresh checks.
Another user's signature, modified/expired intent or revoked access fails closed.
A plain unsigned GET renders a CSRF-protected open button, without switching.
GET never marks read and never approves/executes an action. Do not log full signed
links; responses are no-store and signed redirects suppress the Referrer header.
Inbox membership is not a messenger binding: automatic per-user transport routing
is not invented from a workspace's shared chat address.

Current schema/application/acceptance evidence is in PR #246; definitions and
migration validation do not mean these revisions were applied to the owner's DB.
Historical transport evidence remains in [notification history](audit/notifications-history.md).

## Historical transport scope and status

The source-pinned history for this bounded delivery change is retained in the
[notification audit record](audit/notifications-history.md).

## Three distinct paths

### Workspace-origin events in the technical archive

`NotificationService.create()` remains DB-first and returns the created row.
Writing a row does not mean it was delivered to a messenger. The personal inbox contract above supersedes the earlier shared workspace bell.

Collection failures create a notification in the source's own workspace, with
an actionable generic message instead of the raw exception. A trusted operator
can collect without ambient workspace scope, but that does not stamp its source
notification into bootstrap. Ordinary callers cannot use a foreign source's
workspace for notification creation. A source without an owner is not silently
assigned to bootstrap. The caller's context is restored after the DB write.

A failed DB notification does not prevent the independent fixed operator alert,
and a failed operator alert does not mask the original collection exception.

### Explicit workspace messenger delivery

`MessengerService.send_notification(...)` requires:

1. An explicit `recipient_id` (exact bound chat ID, with surrounding whitespace
   removed; no numeric-ID/username alias resolution).
2. One existing active workspace. A normal caller must have that workspace in
   context and cannot override it with a foreign `tenant_id`.
3. An active binding whose `tenant_id` is that workspace and whose transport
   matches the requested transport.

A trusted operator can pass `tenant_id` explicitly. An unscoped operator has no
implicit bootstrap workspace or global recipient. An env admin/digest address
never substitutes for a missing workspace recipient. `is_digest_target` is not
required for notifications; it remains a separate digest delivery flag.

```python
from app.core.tenant_context import tenant_scope
from app.services.notifications.messenger import messenger_service
from app.types import NotificationType

with tenant_scope(workspace_id):
    result = await messenger_service.send_notification(
        title="Report ready",
        message="Open the report in your workspace.",
        notification_type=NotificationType.REPORT_READY,
        messenger="telegram",
        recipient_id=owned_chat_id,
    )
```

The legacy envelope is retained: `{"telegram": result_or_none, "vk": result_or_none}`.
Rejected routes produce `success=False` before any HTTP send. Unsupported
transport names raise `ValueError`. Telegram is implemented; VK remains a failed
placeholder, and MAX notifications are not introduced here. The default `all`
can produce mixed Telegram/VK results; it is not an all-or-nothing delivery.

`send_report_ready` and `send_trend_alert` accept keyword-only `recipient_id`
and optional `tenant_id`. Calling them without a recipient now fails closed.
`NotificationService.create(..., send_to_messenger=True, recipient_id=...)`
pins delivery to the created row's workspace, not an operator default. It does
not claim delivery in logs when the transport returns failure.

Titles/messages are escaped as literal text before Telegram HTML rendering.
Messages exceeding the conservative escaped-length budget fail without HTTP
rather than implicitly splitting/retrying.
Bot API HTTP 200 alone is not success: `ok` must be true. This service's results
and logs do not echo API response bodies, exception details or token-bearing
request URLs. This does not claim that every logging integration in the process
has been audited or that Telegram stores no customer text.

### Fixed operator alerts

`send_operator_alert(event_code)` is the **only** notification path using
`TELEGRAM_ADMIN_CHAT_ID`. Its content comes from a code-owned catalog, not from
caller-provided source names, report content, URLs or exceptions.

| Code | Signal |
|---|---|
| `collection_failed` | A collection operation failed |
| `api_error` | An integration operation failed |
| `connection_error` | An integration connection failed |
| `backup_failed` | A backup operation failed |
| `backup_completed` | A backup operation completed; restore checks remain separate |

Unknown codes fail without sending. No free-form message/details/recipient
parameters exist. Missing admin chat or bot token is a failed send, not
fallback to a workspace. The collector explicitly calls `collection_failed`;
it never passes its source name or exception to this method. The other codes
are available to trusted internal callers, not automatically wired to services
that do not yet call them.

```python
await messenger_service.send_operator_alert("collection_failed")
```

`send_critical_alert(title, message, error_details)` is retained only as a legacy
shim. It discards **all three** free-form values and emits the generic `api_error`
operator template. It preserves the old Telegram/VK result envelope but not the
old free-form forwarding behavior. New callers should use catalog codes.

The configured admin chat is deployment-trusted. Configure an operator-only
chat; a client must not be able to edit that environment setting. Fixed content
prevents report/source/exception forwarding, but it does not prevent event spam:
operator-alert rate limits and durable delivery progress are future work.

## Admin action and setup impact

The SQLAdmin notification action uses the notification row's `tenant_id` and
an explicit `recipient_id` query parameter. A missing recipient, ownerless row,
foreign/inactive binding or inactive workspace cannot send. Existing SQLAdmin
session/role authorization remains in place; the sender's binding guard is
additional, not a replacement for it.

The old one-click button is hidden from list/detail until a recipient-selection
and result-feedback form is implemented. Its authorized action endpoint remains
available for explicitly addressed operator use; there is no new public client
send endpoint or automatic first-channel choice. Action-body regressions and
existing ASGI admin authorization tests are run separately.

No new secrets or env variables are required. Keep `TELEGRAM_BOT_TOKEN` as an
application secret, personal integration credentials in their encrypted vault,
workspace chat bindings in the database and optional `TELEGRAM_ADMIN_CHAT_ID`
for fixed operator templates only. No automatic binding/data migration occurs.

## Verification

Historical test scopes, counts and integration results are in the
[notification audit record](audit/notifications-history.md#verification).
They are not current checkout or release evidence.

## Deliberate remaining work

Open notification work and ownership are tracked in [ROADMAP](../ROADMAP.md).
This document records the delivered subsystem contract, not a second task queue.
