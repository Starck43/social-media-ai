# Channel Integration Reference

Messaging channels (Telegram, MAX) provide two capabilities:
1. **Outbound** — sending messages (agent replies, digest delivery)
2. **Inbound** — long-polling for user messages (agent chat) + channel post ingestion (content collection)

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  app/channels/                      │
├─────────────────────────────────────────────────────┤
│  base.py     Channel protocol + Inbound dataclass   │
│  registry.py get_channel(), enabled_channels(),     │
│              broadcast_digest()                     │
│  listener.py listen_forever() — polls all channels, │
│              routes through agent + ingest          │
│  telegram.py TelegramChannel (Bot API)              │
│  max.py      MaxChannel (MAX Bot API)               │
└─────────────────────────────────────────────────────┘
           │                    │
           ▼                    ▼
┌────────────────┐   ┌──────────────────┐
│ Telegram Bot   │   │ MAX Bot API      │
│ API            │   │ (platform-api2)  │
└────────────────┘   └──────────────────┘
```

## Channel Protocol

All channels implement the `Channel` protocol from `app/channels/base.py`:

```python
class Channel(Protocol):
    name: str  # 'telegram' | 'max'

    async def send(self, chat_id: str, text: str, parse_mode: str | None = None) -> dict[str, Any]:
        """Send text; returns {success: bool, message_id: int, ...}"""
        ...

    async def poll(self) -> AsyncIterator[Inbound]:
        """Long-poll updates; yields Inbound objects"""
        ...
```

### Inbound Dataclass

```python
@dataclass
class Inbound:
    channel: str              # 'telegram' | 'max'
    chat_id: str              # messenger chat ID
    user_id: str              # messenger user ID
    text: str                 # message text
    is_channel_post: bool     # True if posted to a channel (not DM)
    raw: dict[str, Any]       # raw provider response
```

---

## Telegram Channel

**API:** https://api.telegram.org  
**Docs:** https://core.telegram.org/bots/api

### Configuration

| Env Var | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token (required for both agent chat and digest) |
| `TELEGRAM_DIGEST_CHANNEL_ID` | Deprecated; ignored by digest delivery (use workspace bindings) |
| `TELEGRAM_OWNER_IDS` | Telegram user IDs eligible for initial owner bootstrap; persisted membership and runtime authorization remain separate |

### Inbound: Long Polling

```
GET https://api.telegram.org/bot{token}/getUpdates?offset={offset}&timeout=25&allowed_updates=["message","channel_post"]
```

- Polls every ~25 seconds (blocking)
- Offset advances after each iteration (in-memory: `channel._offset`)
- Yields `Inbound` for `message` and `channel_post` updates
- Ignores edited messages
- On 429: sleeps `retry_after` seconds and retries
- On HTTP error: logs and retries after 3 seconds

### Outbound: Send Message

```
POST https://api.telegram.org/bot{token}/sendMessage
```

**Payload:**
```json
{
  "chat_id": "string",
  "text": "string (up to 4096 chars)",
  "parse_mode": "HTML",
  "disable_web_page_preview": true
}
```

**Limits:**
- Max text length: 4096 characters
- Rate limit: ~30 messages/sec globally, 429 → `retry_after`
- Send interval between chunks: 50ms

**Long messages:** split on paragraph boundaries (`\n\n`), fallback to `\n`, hard cut at limit. Only first chunk gets `parse_mode`.

**Response:**
```json
{
  "ok": true,
  "result": {
    "message_id": 12345,
    "chat": {"id": -1001234567890, "type": "channel"},
    "date": 1735689600,
    "text": "..."
  }
}
```

---

## MAX Channel

**API:** https://platform-api2.max.ru  
**Docs:** https://dev.max.ru

### Configuration

| Env Var | Purpose |
|---|---|
| `MAX_BOT_TOKEN` | Bot access token |
| `MAX_API_URL` | API base URL (default: `https://platform-api2.max.ru`) |
| `MAX_OWNER_ID` | MAX user IDs eligible for initial owner bootstrap; persisted membership and runtime authorization remain separate |
| `MAX_CHANNEL_ID` | Deprecated; ignored by digest delivery (use workspace bindings) |

### Inbound: Long Polling

```
GET https://platform-api2.max.ru/updates?timeout=30&marker={marker}&types=message_created,message_edited
```

- Uses marker-based pagination (not offset)
- Polls every ~30 seconds (blocking)
- Yields `Inbound` for `message_created` updates only
- On 429: sleeps 1 second and retries
- On HTTP error: logs and retries after 3 seconds

**Note:** MAX requires the Russian Ministry of Digital Development root certificate in the OS trust store if not pre-installed.

### Outbound: Send Message

```
POST https://platform-api2.max.ru/messages?chat_id={chat_id}
Authorization: {token}
```

**Payload:**
```json
{
  "text": "string (up to 4000 chars)",
  "notify": true,
  "format": "html"
}
```

**Limits:**
- Max text length: 4000 characters
- Rate limit: ~2 messages/sec per chat
- Send interval between chunks: 600ms

**Long messages:** split on paragraph boundaries (`\n\n`), fallback to `\n`, hard cut at limit. Only first chunk gets `format: html`.

---

## Channel Registry

`app/channels/registry.py` manages channel lifecycle:

### `get_channel(name)`

Returns a channel instance or `None` if not configured.

```python
ch = get_channel("telegram")  # TelegramChannel or None
ch = get_channel("max")       # MaxChannel or None
```

A channel is "enabled" if its token is configured:
- Telegram: `TELEGRAM_BOT_TOKEN` is set
- MAX: `MAX_BOT_TOKEN` is set

### `enabled_channels()`

Returns all enabled channel instances. Used by `listen_forever()`.

### `broadcast_digest(text, channel_filter=None, tenant_id=None)`

Sends only to active digest-enabled bindings of one active workspace. An
explicit ID cannot override a non-operator's ambient workspace; no ambient
scope without operator bypass fails closed. Unscoped operator calls select the
bootstrap workspace. `build_and_publish` scopes the entire build without bypass.

```python
from app.channels.registry import broadcast_digest
from app.core.tenant_context import tenant_scope

with tenant_scope(workspace_id):
    results = await broadcast_digest("Daily digest text...")
# {"telegram:123": {"success": True, "message_id": 123}}
```

`channel_filter` applies to every workspace target. Deprecated env destinations
are ignored even for bootstrap; `TELEGRAM_ADMIN_CHAT_ID` is not a digest target.
Only active, digest-enabled owned bindings authorize delivery. Result keys are
consistently `transport:chat_id`, not the old `telegram` / `max` keys. Dedup uses
trimmed exact identifiers and case-insensitive `@usernames`, not a platform
lookup between numeric IDs and username aliases. Cross-call retries and
concurrent publication are not made exactly-once by this routing change.

---

## Listener

`app/channels/listener.py::listen_forever()` — the agent chat loop.

### Flow

```
listen_forever()
  ├─ enabled_channels() → [TelegramChannel, MaxChannel]
  ├─ asyncio.gather(
  │     _consume_channel(TelegramChannel),
  │     _consume_channel(MaxChannel)
  │   )
  │
  └─ _consume_channel(channel)
       └─ async for inbound in channel.poll():
            ├─ _ingest_safely(inbound)  → services/monitoring/ingest.py
            └─ _handle_safely(inbound)  → agent/runtime.py::handle_inbound()
                 └─ if reply: channel.send(inbound.chat_id, reply)
```

### Key Design Decisions

1. **Per-channel failure isolation:** A dead Telegram token does not stop MAX polling. Each channel has its own `while True` loop with independent error handling.

2. **Ingest before agent:** Channel posts are ingested into the analysis pipeline *before* being sent to the agent. This is synchronous with the poll iteration — the `getUpdates` offset only advances after the iteration completes, giving at-least-once delivery.

3. **Owner-only agent chat:** The agent ignores channel posts from non-owners (`is_channel_post` + owner allowlist). Digest channel posts are never replied to.

4. **Offset in memory:** Telegram `getUpdates` offset lives in `channel._offset`. On restart, Telegram replays unacked updates — the per-source watermark in ingest prevents double-charging the LLM.

5. **Stranger handling:** Messages from unknown users/chats are silently dropped (the bot must not reveal its existence to strangers).

---

## Message Routing

How an inbound message finds its tenant:

1. `handle_inbound(inbound)` → `resolve_inbound(inbound)` in `app/services/tenancy/resolver.py`
2. Is this chat already bound (`tenant_channels`)? → use that tenant
3. Does the message look like an invite code (`/start <code>`)? → redeem it, bind the chat
4. Is the sender a platform owner (env allowlist)? → bootstrap into `owner` workspace
5. Otherwise → silently drop

The entire turn is wrapped in `tenant_scope(tenant_id)` — tools, managers, and the job dispatcher see only one workspace's data.

---

## Ingest Pipeline

Channel posts flow into content collection:

```
channels/listener.py::poll()
  → _ingest_safely(inbound)
    → services/monitoring/ingest.py::ingest_channel_post(inbound)
      → resolve tenant (bypass, lookup by source)
      → AIAnalyzer.base_analyze_content(item)
        → within tenant_scope(source.tenant_id)
```

**Important:** The listener consumes `getUpdates`, so a second poller would fight over the offset. Instead, every inbound update is offered to `ingest_channel_post()` directly.

### Watermarks

- `Source.last_item_id` — high-water mark per source for push-based sources
- `Source.last_checked` — incremental collection start time
- A replayed update (restart, network retry) is skipped via watermark comparison

---

## Digest Delivery

Digests are sent via `broadcast_digest()` from `app/services/digest/builder.py`:

1. Resolve one active workspace; disable bypass for aggregation and run writes.
2. Build digest text (aggregation + LLM summary).
3. Call `broadcast_digest(text)` → send once per eligible owned destination.
4. Per-target results are returned; `digest_runs` stores aggregate status,
   message_id, rendered content and error, not a durable per-target results ledger.
5. Delivery failures raise `DigestDeliveryError` → job queue retries with backoff.

No eligible bound targets returns `{}`; missing transport credentials produce a
failed result for that target. Env-only recipients require explicit binding
setup. A retry can still repeat a previously successful target after partial
failure; durable destination/part progress is a separate follow-up.

---

## Quick Reference

| Feature | Telegram | MAX |
|---|---|---|
| API Base | `api.telegram.org` | `platform-api2.max.ru` |
| Auth | `/bot{token}/` in path | `Authorization: {token}` header |
| Send Endpoint | `sendMessage` | `/messages?chat_id=` |
| Poll Endpoint | `getUpdates?offset=` | `/updates?marker=` |
| Text Limit | 4096 chars | 4000 chars |
| Rate Limit | ~30 msg/sec global | ~2 msg/sec per chat |
| Inbound Types | `message`, `channel_post` | `message_created`, `message_edited` |
| Parse Mode | `HTML` | `html` / `markdown` |
| Special | Requires admin of channel for `channel_post` updates | Requires Russian root CA |

---

## Customer communications senders (P15-T06)

`app/channels/senders/` and `app/integrations/email/smtp_send.py` expose internal,
**default-off** single-attempt adapters, separate from `Channel.send`, digest
routing and employee notifications (`app/services/notifications/`, D19). They
create no API/agent-tool entrypoint, command, outbox, schedule or migration.
P15-T05 is the consumer, not part of this transport task.

| Capability | Adapter | Exact deployment prefix | Binding ID |
| --- | --- | --- | --- |
| `comms.email.send.v1` | `send_email(MailAccount, EmailRequest)` | `P15_EMAIL_SEND` | owned `WorkspaceConnection.id` |
| `comms.telegram.bot_send.v1` | `send_telegram(BotAccount, BotRecipient, text)` | `P15_TELEGRAM_BOT_SEND` | private `TenantChannel.id` |
| `comms.max.bot_send.v1` | `send_max(BotAccount, BotRecipient, text)` | `P15_MAX_BOT_SEND` | private `TenantChannel.id` |

### Release and caller contract

For each prefix, `_ENABLED=1` **and** `_BINDINGS=workspace_id:binding_id` release
only those exact pairs; `_KILL=1` stops new admissions independently. All three
are off by default. Binding lists are comma-separated positive integer pairs,
without spaces, wildcards or leading zeroes; any malformed entry refuses the
entire list. Missing enable/bindings or any kill value other than exactly `0`
fails closed. A bot token or an approved command cannot override these controls.
An email connection also requires its own `send_enabled is True`; the catalog
presets stay false. No global installation token/mail account is used as fallback.

The adapters require an explicit matching non-bypass `tenant_scope`, positive
owner/actor IDs, and an active, verified binding. The transport does not require
the actor to be the connection owner or decide `comms.outbox` permissions. Under
D14, the trusted caller must verify fresh workspace-scoped actor permissions,
connection ownership and approval before dispatch; these DTO values are not
proof of authorization. Switches are read again after egress resolution and
immediately before SMTP DATA / Bot API POST. Environment changes must reach
**every serving process**; there is no instant cross-process propagation or
recall of an already issued request.

`SendBinding`, `MailAccount`, `BotAccount` and `BotRecipient` are immutable inputs
from a **trusted service**, not authorization evidence by themselves. Never
populate their flags, credential or recipient consent directly from HTTP/agent
input. Before enabling a consumer, it must use the existing managers/approval
ledger to resolve fresh workspace/account/actor authority, hold the appropriate
revocation locks across dispatch, seal exact destination/content/credential
identity into approval, and commit a durable single-dispatch claim before I/O.
This task does not register or activate a business-command executor; P15-T05
owns that integration with the existing central capability registry and ledger.
Calling an adapter directly from an unreviewed tool/route is not supported.

For bots the trusted caller must resolve persisted **same-bot** private-chat
start/consent evidence, bound to the workspace, binding, numeric user ID and
`bot_fingerprint(capability, token)`. A numeric ID, membership or a caller-supplied
`started_bot=True` is not proof. Token rotation invalidates old fingerprinted
evidence and reviewed intent; it does not silently authorize a different bot.
No group/channel destinations, usernames, MTProto or personal-account automation.

### Transport and outcome contract

- SMTP uses the Mail.ru/Yandex catalog hostname, implicit verified TLS on 465
  and the customer's app password. Both envelope sender and `From` are forced
  to that customer's account (D12). One recipient, plain text, generated
  `Message-ID` and `Date`; optional validated `In-Reply-To`. No caller-supplied
  sender, CC/BCC, installation-domain fallback, attachments or HTML. A bounded
  1 MiB UTF-8 body and ASCII mailboxes avoid unnegotiated SMTPUTF8; text uses
  quoted-printable on the wire rather than assuming 8BITMIME support.
- Telegram uses only `api.telegram.org` Bot API `sendMessage`; MAX uses only
  `platform-api2.max.ru/messages?user_id=...`, with its token in `Authorization`.
  One plain message (conservative UTF-16 limit 4096 / 4000), no splitting, retry,
  redirect, proxy or alternate endpoint. MAX pacing / batching belongs to T05;
  a 429 is not retried here. TLS verification is never disabled; required MAX
  root-certificate provisioning remains the operator's separate responsibility.
- **Every socket destination** passes `acquire_outbound_target` (P14-T09) on each
  attempt. All DNS answers must pass that seam; sockets connect to one validated
  IP without a second hostname lookup, keeping original-host SNI/certificate
  verification. There is no address fallback. Open the existing `registry_scope`
  in the trusted caller when its operation needs an installation snapshot.
  Mailbox domains are SMTP envelope data, not separate socket destinations.
- Boxed/self-hosted SMTP on `10.x` / `192.168.x` cannot pass P14-T09's accepted
  global-address restriction, just as for self-hosted Bitrix24. This is a known
  limitation, **not a bug** or permission to bypass the seam. This task supports
  the two fixed public SMTP presets, not arbitrary SMTP hosts.
- `SendResult.outcome` is `blocked` (pre-send refusal), `rejected` (known negative),
  `sent` (provider acceptance), or **`unknown`** (timeout / uncertain effect).
  If the SMTP kill is observed before `sendmail()` starts, the adapter returns
  `blocked/account_send_disabled`; no envelope command was issued and a fresh
  attempt may be made after an operator re-enables sending. Once `sendmail()`
  starts, it can disclose the envelope before the adapter regains control.
  If a kill or revocation is observed after SMTP `MAIL FROM`/`RCPT TO` begins but
  before `DATA`, the adapter returns `unknown/smtp_envelope_disclosed`: recipient
  envelope metadata reached the server, while the message body was not sent.
  A SMTP acceptance is not proof of delivery; its locally generated `message_id`
  is not a provider receipt. Bot `receipt_id` requires a valid acknowledgement
  naming the requested private recipient. Only fixed codes / bounded receipt IDs
  escape; no provider bodies, raw exceptions, credentials or message content in
  logs/results. Authentication and TLS failures have fixed SMTP codes.
- No outcome is retried automatically. After `unknown`, the caller must retain its
  durable claim and reconcile, never dispatch blindly. Cancellation or process
  death does not prove the send stopped. Async adapters use a worker thread:
  cancelling the await does **not** prove the socket stopped. A fresh caller
  invocation is a new attempt, not
  transport idempotency; `Message-ID` alone does not deduplicate SMTP. Durable
  replay prevention and receipt persistence are deliberately T05 responsibilities.

Focused no-DB tests: `tests/channels/test_p15_t06_senders.py`. They replace
SMTP/HTTP/DNS/socket boundaries explicitly and do not certify live credentials,
bot permissions, deployment activation or the future T05 caller integration.
