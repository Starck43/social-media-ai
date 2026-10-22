# Personal LLM ownership implementation evidence

Base: `dev` `ccce2801a640efd7a90704f37874d0bae1ec388a`.
Branch: `feat/personal-llm-ownership`; P3-T93/P3-T94/P3-T95.
Status: IN_PROGRESS, no acceptance or migration application claimed.

## Coordination

Provider/model web UI: `ui/llm-provider-settings`, separate session.
Keep social Platform independent from personal LLM providers; preserve PR #224.
PR #217 overlaps task validation but has older manager code; retain current dev
queue invalidation/admission logic, do not copy its manager wholesale.

## Safety boundary

Do not infer an owner for existing providers or copy secret keys across users.
Migration requires an explicit reviewed provider-to-user mapping.
No production or shared-test schema mutation, deployment, external model request,
or merge is authorized or performed by this implementation lane.

## Validation

Pending implementation, isolated checks and owner-local PostgreSQL/ASGI acceptance.
