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
| `TELEGRAM_DIGEST_CHANNEL_ID` | Target channel/chat for scheduled digests |
| `TELEGRAM_OWNER_IDS` | Comma-separated Telegram user IDs allowed to chat with the agent |

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
| `MAX_API_BASE` | API base URL (default: `https://platform-api2.max.ru`) |
| `MAX_OWNER_ID` | MAX user ID allowed to chat with the agent |
| `MAX_CHANNEL_ID` | Target channel/chat for scheduled digests |

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

### `broadcast_digest(text, channel_filter=None)`

Sends digest text to configured digest targets. Returns per-channel results.

```python
results = await broadcast_digest("Daily digest text...")
# {"telegram": {"success": True, "message_id": 123}, "max": {"success": False, "error": "channel not configured"}}
```

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

1. Build digest text (aggregation + LLM summary)
2. Call `broadcast_digest(text)` → iterates over Telegram + MAX targets
3. Each target with both token and channel ID configured receives the digest
4. Results recorded in `digest_runs.results`
5. Delivery failures raise `DigestDeliveryError` → job queue retries with backoff

Only channels with both token and target set receive anything. Missing credentials return `{}` silently.

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
