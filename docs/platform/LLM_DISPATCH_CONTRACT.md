# LLM dispatch contract

Status: **normative contract, dormant implementation**. P21-T12 owns this
contract; [P21-T13](../tasks/p21-t13.md), [P21-T14](../tasks/p21-t14.md),
[P21-T15](../tasks/p21-t15.md) and [P21-T16](../tasks/p21-t16.md) extend it by
reference and never restate it. The task card is
[P21-T12](../tasks/p21-t12.md); the phase plan is
[P21](../phases/p21-ai-operations.md); the holder/policy authority is
[P2-T17](../tasks/p2-t17.md).

This document describes what a provider attempt *must* look like. It does not
authorize activation: the target dispatch stays disabled, no schema change is
part of it and no migration, personal-billing cutover or production admission
is enabled by it.

## 1. The one seam

Every actual LLM HTTP attempt enters exactly one seam entry before the request
is sent. The seam resolves, in this order and failing closed:

1. the authenticated actor and the verified workspace/execution rights;
2. the owner-configured execution model choice;
3. explicit catalog access and the plan grant for that row;
4. the credential source for the funding class;
5. the trusted payer/usage context;
6. validated parameters, capability fit, shared allowance eligibility and
   provider rate capacity;
7. the durable atomic reservation/receipt and a fresh authorization plus
   credential key-version check immediately before HTTP;
8. the terminal accounting of the attempt.

Every step fails closed: a denial stops the attempt **before** HTTP and is
never permission to try another model, another payer or another funding class.
No database lock spans the HTTP request. The reservation and its receipt commit
before the request; the evidence of the attempt is recorded after it.

## 2. Identity: four different subjects

The seam keeps these apart. They are not interchangeable and none of them may
come from a request payload, query string, job payload or tool argument.

| Field | Meaning | Authority |
| --- | --- | --- |
| `actor_user_id` | the authenticated actor of the turn | session/membership |
| `tenant_id` | the verified workspace scope of the turn | tenant context |
| `plan_holder_user_id` | the user whose plan sponsors the workspace | P2-T17 holder contract |
| `credential_owner_user_id` | the owner of the BYOK row and its provider (personal class only) | P21-T11 / P21-T13 custody |
| `payer` | the trusted payer binding of the attempt | server-side resolution, never the caller |
| `delegation_consent_version` | the version of the host-sponsored delegated-use consent | P21-T12/T13, not yet persisted (gap G7) |
| `plan_policy_version` | the version of the holder's plan policy that was checked | P2-T17 |
| `credential_version` | the monotonic freshness version of the used credential | P21-T11 |

A workspace is **sponsored** by exactly one holder. An invited member executes
inside the holder's enabled functionality with the holder's configured models,
intersected with their own workspace/object permissions; their own plan or
catalog never widens the host's rights. Delegated BYOK use needs explicit
owner consent; the seam decrypts internally and never returns a key to the
invitee. No workspace role, including a broad administrator role, grants
holder-level management of the tariff, catalog, providers, keys, models or
defaults. Accepted legacy personal bindings and legacy task payers stay
unchanged until an explicit approved rebind.

## 3. Funding classes

| Class | Catalog | Credential | Payer |
| --- | --- | --- | --- |
| Platform | the holder's plan-granted row | deployment allowlisted secret reference only | shared holder allowance plus the trusted payer binding |
| Personal | the holder's own row | encrypted BYOK of the owner or an explicitly consented delegate | accepted personal payer consent; token-policy applicability per P2-T17 |
| Workspace | future, not activated | not selectable | not activated |

Owner-sponsored dispatch admits a permission-scoped invitee or worker only
with fresh delegated-use authority. It stays personal custody: not a workspace
credential catalog and not a corporate payer account. A cross-class switch is
not ordinary automatic fallback; it needs separate informed selection/consent
and a new admission.

## 4. Route matrix

Every provider attempt of the product, the surface that owns it, and the class
it resolves to today. The attempt identity is what the journal record of that
try carries.

| Route | Surface | Provider call | Class today | Attempt identity |
| --- | --- | --- | --- | --- |
| Agent chat turn (tools, replies) | `app/agent/runtime.py` → `chat_with_fallback` | `LLMClient.chat` | personal or platform | own dispatch id per try |
| LLM tool call | `app/agent/toolset/llm.py` | `LLMClient.chat` | personal or platform | own dispatch id per try |
| Learning / reflection | `app/agent/learning.py` → `chat_with_fallback` | `LLMClient.chat` | personal or platform | own dispatch id per try |
| Prompt suggestion | `app/services/ai/scenario_prompt_suggest.py` → `chat_with_fallback` | `LLMClient.chat` | personal or platform | own dispatch id per try |
| Text analysis (with candidate fallback) | `app/services/ai/analyzer.py` | `LLMClient.analyze` | platform fleet | own dispatch id per try |
| Image analysis | `app/services/ai/analyzer.py` | `LLMClient.analyze` | platform or personal | own dispatch id per try |
| Video analysis | `app/services/ai/analyzer.py` | `LLMClient.analyze` | platform or personal | own dispatch id per try |
| Unified summary | `app/services/ai/analyzer.py` | `LLMClient.analyze` | platform fleet | own dispatch id per try |
| Digest summary | `app/services/digest/builder.py` | `LLMClient.analyze` | platform fleet | own dispatch id per try |
| Admin generation probe | `app/services/ai/model_probe.py` | `LLMClient.chat` | personal or platform | own dispatch id per try |
| Admin catalog test | `app/admin/view_modules/llm_catalog.py` | `LLMClient.analyze` | personal | own dispatch id per try |
| Admin provider listing probe | `app/services/ai/model_probe.py` | raw `GET /models` | platform or personal | excluded: no generation attempt, no completion evidence |

The excluded probe is the only provider request that is not a generation
attempt. It stays out of the seam until a dedicated assignment gives it its own
authenticity/replay contract.

## 5. Fallback

- A fallback try requires the **same credential funding class** and the **same
  trusted payer** as the try it replaces. A silent managed/BYOK switch is not
  fallback.
- Every fallback try gets its own dispatch identity and its own admission; a
  persistence retry of the same try keeps that identity.
- Authorization, budget, allowance, capacity or revocation denials never
  trigger a fallback. They end the turn.
- A fallback try keeps compatible capabilities and parameters: a route that
  needs image capability does not fall back to a text-only row.

The compatibility rule is checked before the next try: the funding class of the
next row and the payer of the attempt must equal the ones of the try it
replaces. A mismatch is a denial (`fallback_funding_class_mismatch`,
`fallback_payer_mismatch`), not a switch. Only proven bindings can differ: an
attempt without a proven payer keeps its binding, and a strict attempt without
a proven payer is already denied earlier as `payer_unresolved`.

## 6. Redacted error taxonomy

A denial carries a stable code and a generic message. No provider body, prompt,
credential, token, endpoint secret or user content may appear in a denial, its
message, its log line or its evidence.

| Code | Meaning |
| --- | --- |
| `actor_unresolved` | no authenticated actor for the attempt |
| `workspace_rights_missing` | the actor has no verified workspace/execution rights |
| `plan_holder_unresolved` | the sponsoring holder could not be resolved unambiguously |
| `plan_grant_missing` | no plan grant for the required capability of the holder's plan |
| `catalog_access_missing` | the row is not readable through an explicit catalog |
| `credential_source_unavailable` | no credential source for the funding class |
| `credential_version_stale` | the credential version changed after admission |
| `payer_unresolved` | no trusted payer binding for the attempt |
| `params_invalid` | the request parameters are not valid for the route |
| `capability_unfit` | the row cannot serve the capability the route needs |
| `allowance_unavailable` | the shared allowance policy is missing or denies |
| `capacity_unavailable` | the provider rate capacity could not be acquired |
| `admission_unavailable` | the durable reservation/receipt could not be committed |
| `fallback_funding_class_mismatch` | the next try would switch the credential funding class |
| `fallback_payer_mismatch` | the next try would switch the trusted payer |
| `funding_class_unsupported` | the funding class is not selectable (workspace class) |
| `workspace_funding_inactive` | the workspace funding class is reserved and not activated |

A missing foundation is a denial, never a permissive fallback: an unwired
grant resolver, rate acquirer, admission service or settlement service denies
the attempt before HTTP.

## 7. Extension interfaces

The seam owns the order and the typed context. The following services are
injected and owned by the listed tasks; the seam never reads their storage.

| Interface | Owner | Called by the seam |
| --- | --- | --- |
| `GrantResolver` | P21-T13 | resolves catalog access, plan grant and capability fit for the holder |
| `RateAcquirer` | P21-T14 | acquires provider capacity for one attempt and releases it afterwards |
| `AdmissionService` | P21-T15 | commits the durable reservation and its receipt for the attempt |
| `SettlementService` | P21-T16 | settles the actual spend, releases the remainder or retains unknown liability |

Each interface has one production implementation and one test double. A missing
implementation denies the attempt; it never skips the step.

## 8. Durable evidence

One journal record per actual try, including a failed try, a transport failure
and every fallback try. The record keeps the attempt's own dispatch identity,
its model row, its provider name, the token evidence and the tariff snapshot.
A persistence replay of the same record keeps that identity.

The identity and version fields of the seam map to durable evidence as
follows, and neither existing receipt family supplies all of them today:

| Seam field | Durable evidence | Owner |
| --- | --- | --- |
| attempt dispatch id | the attempt journal and the dispatch receipt | P21-T15 |
| `credential_version` | the credential freshness version of the used row | P21-T11 |
| `plan_policy_version` | the holder's plan policy version | P2-T17 |
| `delegation_consent_version` | the delegated-use consent record | P21-T12/T13, still missing |
| funding class and payer | the dispatch receipt and the payer binding | P21-T15 |

The immutable personal and workspace receipt families keep their own tables.
Overlapping snapshot columns are not a reason to merge them; a new relation is
only justified by named consumers, constraints, migration and rollback.

## 9. Activation, compatibility and rollback

The seam ships with the target dispatch **disabled**. The compatibility adapter
keeps the current call behaviour and the current public schemas unchanged: the
same response envelopes, the same journal records, the same fallback ordering
and the same accounting projections.

Rollback is a revert of the seam wiring: the compatibility adapter keeps the
call sites valid until every caller has migrated, and no table, column or
scheduler is touched by this contract.

Activation is a later, separate decision. It requires the holder contract of
P2-T17, the catalog grants of P21-T13, the rate control of P21-T14, the atomic
admission of P21-T15 and the settlement of P21-T16 to be accepted first.
