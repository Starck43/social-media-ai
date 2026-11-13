# LLM credential custody and freshness

Implementation: [P21-T11](../tasks/p21-t11.md). Consumers:
[P21-T12](../tasks/p21-t12.md), [P21-T13](../tasks/p21-t13.md),
[P21-T14](../tasks/p21-t14.md).

## Storage and managed references

- Personal/BYOK: `llm_providers.owner_user_id` owns `encrypted_api_key`, encrypted
  with the existing Fernet `CREDENTIALS_KEY`. Neither workspace membership nor an
  administrator workspace role grants management or key inspection. Internal
  execution scope is not a request parameter or delegated-use consent.
- `llm_providers.credential_version`: non-null positive BIGINT, initially 1,
  database-managed. No client/form may write, reset or select this value.
- Future platform catalog (P21-T13): `managed_credential_ref`, nullable opaque
  slug (`[a-z][a-z0-9_-]{0,99}`). Managed rows require this ref and forbid personal
  ciphertext/owner-store lookup; BYOK rows forbid this ref. P21-T13 implements
  those catalog-kind constraints, not a new vault in this task.
- `Settings.LLM_MANAGED_CREDENTIAL_REFS` maps opaque aliases to explicit deployment
  environment names. The internal `resolve_managed_credential` first validates
  the alias against that deployment allowlist; arbitrary environment-name reads
  are impossible. Missing/unlisted/empty references deny. The map contains names,
  not secrets; no HTTP/tenant/catalog setting writes it. Secret-store injection may
  populate the approved environment names. This task does not activate managed
  model execution or grant anyone new decrypt authority.
- Plaintext, ciphertext, ref aliases/environment names, IV/tag/key material and
  literal auth values are never model/catalog metadata. Admin forms show an empty
  password field; a masked indicator is `***` without decrypting or key fragments.
- Keep `CREDENTIALS_KEY` separate from `PII_ENCRYPTION_KEYS` (D8). No envelope,
  AES-GCM, KMS, table merge or new decrypting principal is introduced. Those are
  separate owner-gated decisions, not prerequisites for these guards.

## Exact invalidation contract

Provider-row updates increment once when any of these values actually changes:
`encrypted_api_key` (replacement, re-encryption, removal), `base_url`, `auth_header`,
`api_format`, `is_active` (revocation/reactivation), `owner_user_id` (an already
authorized ownership change). Identical writes and cosmetic `uid`/name/description
updates do not increment. Overflow fails the whole transaction.

Changing a model's `custom_endpoint_path`, upstream `model_id` or `is_active`
atomically increments its provider's version. This deliberately invalidates all
queued attempts under that provider. Provider locks serialize increments, including
bulk SQL and admin writes. Multiple changes to different rows may cause multiple
increments in one bundle; versions are freshness identities, not edit counters.
Deleting a model/provider denies by missing-row resolution.

Admission must persist the accepted version with actor, holder, credential owner
and consent identities in its existing evidence (P21-T12/T15), and re-resolve the
same live row immediately before HTTP. `require_current_credential_version` denies
missing/malformed/stale values without resolving a key. Current personal clients
snapshot the version and recheck after admission; metadata connectivity probes use
the same fresh owner/membership/configuration check. The future admission-evidence
wiring is not activated by P21-T11.

Managed replacement/revocation or changes to a managed ref must increment that
catalog row's same positive BIGINT under the platform writer transaction before
releasing writers. Rotation of deployment secrets cannot be inferred from a
mutable environment value; the operator/managed-catalog lifecycle must invalidate
the version explicitly (P21-T13). No version/ciphertext change resets provider
quota identity or usage. P21-T14 owns stable upstream pool identity.

## Egress contract

All existing OpenAI, Anthropic and custom client requests, including metadata
`GET /models`, use `provider_http_client` and validate the joined destination.
Only HTTPS without userinfo/query/fragment/control characters is accepted. Custom
paths remain relative to the same authority; absolute/protocol-relative paths,
backslashes, traversal and nested encoded authority/control escapes deny.

Every TCP attempt resolves the original host through `app.core.outbound`, checks
**all** addresses, then connects only to a validated numeric IP. HTTPcore retains
the original hostname for Host, TLS SNI and certificate verification. There is no
second name lookup, environment proxy, redirect follow, retry to another host or
reused pool across attempts. DNS failure and mixed public/denied answers deny.
Special/mapped/tunnel IPv6, link-local, multicast, reserved and cloud metadata
targets always deny.

Approved local HTTPS deployments use the existing installation trusted-host
registry's explicit `private` mode and exact approved addresses. A user/provider
payload cannot enable it. Missing/unreadable registry defaults to global public
addresses only. This preserves the accepted local mode; it does not authorize
plain HTTP, loopback or metadata access. Registry permission and consent lifecycle
remain with the installation administrator.

`auth_header` is an allowlisted name (`Authorization`, `x-api-key`, `api-key`,
`x-goog-api-key`), not a URL or literal credential. Existing `{key}` and
`Bearer {key}` templates remain accepted; literal values, other header names and
CRLF deny. BYOK material stays solely in the encrypted key field.

## Rollback

The additive migration has a placeholder revision; assign its numeric ID at
integration. Empty/unconsumed freshness metadata can downgrade. Downgrade refuses
once a provider version has advanced: freeze dispatch and obtain an approved
rollback that preserves queued evidence instead of erasing the stale-request fence.
Credential rotation follows [the maintenance procedure](CONFIGURATION.md#credential-key-rotation-reviewed-maintenance-only);
never rotate production from a test or switch keys without a verified backup and
stopped writers.
