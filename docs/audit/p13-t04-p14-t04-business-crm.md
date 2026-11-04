# P13-T04, P13-T08, P14-T01 – P14-T04 — Business command ledger, OAuth framework and Bitrix24 CRM

Audited 2026-11-04 on the isolated `test_schema`. The six source branches are
linear on top of `dev` `099dd5c5`, so the tree of the stack head
(`feat/p14-t04-crm-greetings`) is what was verified. This is the audit record.

The six tasks were dispatched as stacked PRs
([#372](https://github.com/Starck43/social-media-ai/pull/372),
[#373](https://github.com/Starck43/social-media-ai/pull/373),
[#374](https://github.com/Starck43/social-media-ai/pull/374),
[#375](https://github.com/Starck43/social-media-ai/pull/375),
[#376](https://github.com/Starck43/social-media-ai/pull/376),
[#377](https://github.com/Starck43/social-media-ai/pull/377)) and, by owner choice,
land as one commit: the branches depend on each other, the migrations are one
linear chain and the stack was reviewed as a single tree.

Owner decisions consumed here: [D2](q-18-owner-decisions.md) (Bitrix24 OAuth 2.0
local application), D4, D5, D6.

## Delivered

| Task | Contract |
| --- | --- |
| P13-T04 | `business_commands` / `business_command_targets` (migration `0134`): preview → approve → execute bound to actor, tenant, payload hash and TTL; expired TTL, revoked membership, disabled capability and kill switch deny **and** audit; timeout yields `unknown` with a reconcile path and no automatic resend; cross-tenant command IDs are not found |
| P13-T08 | `app/integrations/oauth/`: authorization-code exchange with PKCE, state binding, refresh under a lock, egress restricted to the provider catalog |
| P14-T01 | `app/integrations/bitrix24/`: portal OAuth, domain validation, REST wrapper, error mapping, health endpoint, per-portal rate budget from [P13-T03](p13-t03.md) |
| P14-T02 | `crm_entities` (migration `0135`): read-only incremental sync keyed by watermark, personal fields encrypted under the [P13-T02](p13-t02.md) contract, two portals in two workspaces isolated |
| P14-T03 | CRM contact cards, deal summary and duplicate candidates on blind indexes |
| P14-T04 | `greeting_history` (migration `0136`): upcoming events at 7/3/1 days and AI greeting drafts |

The PII rotation registry in `cli/commands/pii.py` gains `crm_entities` and
`greeting_history` with a `None` kind. Each entry ships in the branch that creates
its table, so rotation never points at a relation that is not there.

## Verification

Run by the auditor on a detached worktree of the stack:

| Check | Result |
| --- | --- |
| `python -c "import app.models"` on every branch | OK — the earlier `app.models.crm` import failure is fixed |
| Each branch's own test file through the root conftest | 6 / 4 / 3 / 5 / 2 / 3 passed |
| Whole stack at `0136` | 23 passed |
| Migration `0133 -> 0136` | one new relation per task: `business_commands`, `business_command_targets`, `crm_entities`, `greeting_history`, plus their sequences, types and `contact_pii_marker` triggers |
| `downgrade 0136 -> 0133` | inventory identical to the pre-run baseline — clean rollback |
| Re-upgrade to `0136` | PASS |
| ORM metadata against the live schema | no model table missing, no orphan table |
| PII rotation registry against the live schema | every entry resolves to an existing column |
| Synthetic records after the run | none; `test_schema` restored to `0133` |

The earlier `READY` declarations on these branches were withdrawn in `e6f32b1d` and
re-earned by this run.

## Accepted with a deviation

One commit instead of six, chosen by the owner. The six tasks therefore share this
one record and one `P2` line item instead of six.

## Not covered

- No live provider call was made: Bitrix24, the OAuth token endpoint and the AI
  draft provider are mocked. Portal authorization, token refresh against a real
  portal and greeting quality are unverified.
- `RATE_LIMIT_KEY`, `PII_INDEX_KEY` and the OAuth client secret are not configured in
  any deployed environment.
- Q-19 remains open; it governs the connector, not these tasks.
