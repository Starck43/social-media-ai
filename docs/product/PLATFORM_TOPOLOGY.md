# Platform topology and access clients (future direction)

Direction recorded by P20-T01 ([../audit/p20-t01.md](../audit/p20/p20-t01.md)) at the owner's request
(2026-11-05). **Not scheduled and not shipped.** The supported target remains the
single-VPS monolith in [DEPLOYMENT](../platform/DEPLOYMENT.md#supported-single-vps-topology-prepared-not-provisioned).
Complete the monolith first; the guardrails below keep a later split possible
without a rewrite. No infrastructure, licensing or provider choice is made here.

## Deployment modes

| Mode | Code runs | Personal and customer data lives | Status |
| --- | --- | --- | --- |
| Managed service | Owner's VPS, all workspaces | VPS database | Current target |
| Self-hosted | Customer server, full monolith | Customer database | Future |
| Hybrid | Minimal core on the owner's VPS + data node on the customer server | Data node only | Future |

## Hybrid responsibilities

- **Core (owner's VPS):** installation registry, licensing/entitlements and
  billing for the installation, release/update channel, optional cloud-model
  gateway that receives only cloud-boundary requests the data node chooses to
  send, optional public relay for OAuth redirects/webhooks that forwards without
  storing content, aggregate non-personal health. The core does not hold end-user
  content, contacts, messages, documents, files or connector credentials.
- **Data node (customer server):** PostgreSQL with workspace data, credentials,
  connectors (mail, messengers, CRM, 1C, calendars), the single runtime owner,
  web/API for users, company/local models and the paired local connectors.
- **Link:** the data node opens an outbound, mutually authenticated channel to
  the core — the same pattern as the local connector ([R-05](BUSINESS_MODULES.md#review-findings-corrections-to-the-tz),
  [protocol](../connectors/LOCAL_CONNECTOR_PROTOCOL.md)). The core never pulls personal data.
- **Identity:** users authenticate at the data node; the core knows the
  installation and its administrators, not workspace members.

## Model routing

Server policy selects the model from the content's data boundary
(`cloud` / `company` / `local_only`): external cloud providers, company models in
the customer network (for example an OpenAI-compatible endpoint), local models on
a device. Content never falls back across its boundary. Today company and
local-only chat routes are refused ([API](../platform/API.md#web-chat-evidence-context-p12-t11));
end-to-end routing is a future task.

## Access clients

- **Web** — current primary client.
- **Telegram/MAX bot** — current chat transport sharing the web conversation
  ([AGENT](../agent/AGENT.md)). In hybrid mode the data node can poll the bot API outbound
  instead of exposing a webhook.
- **API and CLI** — current; every client uses the same services
  ([surface parity](WORKSPACE_UX.md#surface-parity)).
- **Desktop app** — after the monolith. Reuse the paired P18 connector shell,
  pairing and update channel ([D6](BUSINESS_MODULES.md#owner-decisions),
  [P18-T07](../tasks/p18-t07.md)); do not ship a second installer.

## Guardrails for current work

- Keep platform-global rows free of customer data (the
  [tenant-isolation exception](BUSINESS_MODULES.md#shared-requirements)); they are
  the natural core candidates.
- Clients and pages call permission-checked services, never the database or each
  other's internals; outbound calls go through the egress seam
  ([P14-T09](../audit/p14/p14-t09.md)) so placement stays configuration.
- External objects are referenced through the shared source registry (D25) and
  survive a node move; no module-specific external-id schemes.
- Keys and secrets are per installation; nothing assumes two installations share
  `CREDENTIALS_KEY`, PII keys or OAuth clients.
- No new service split until a measured load or isolation requirement exists
  ([DESIGN](DESIGN.md#development-hierarchy)).

## Decisions needed before scheduling

Core/data-node licensing and billing, update signing, support access, backup
ownership, failure behavior when the core is unreachable, and legal review of
personal-data localization (152-FZ) for the core's hosting location.
