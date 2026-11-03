# P12-T14 single-PR review entry

The one implementation and audit PR is **#308**, targeting `dev`, using branch
`docs/p12-t14-config-security-plan` (the historical branch name is retained to
preserve the existing PR). Do not audit or merge old slices in sequence. All new
P12-T14 work is published to this PR; separate working branches are implementation
checkpoints, not additional audit requests. #308 stays draft until the complete
scope and checks are ready for independent review. No merge is authorized.

## Review map

1. **Credential storage and renewal:** user credential model/manager, VK OAuth
   refresh transaction fence and encrypted refresh material; migration 0124.
2. **Trust and safe diagnostics:** VK OAuth URL encoding and endpoint allowlist,
   fixed OAuth error guidance, renewal_unverified/operator vs personal action
   statuses, source preflight and personal notifications.
3. **Operations:** read-only source/owner inventories, guarded unreadable-key
   recovery and offline credential-key rotation. Apply/rotation still require
   explicit owner authorization, backup and the documented stopped-writer window.
4. **Telegram boundaries and reuse:** no deployment-wide personal session,
   selected owner checks, one encrypted personal authorization and explicit
   per-workspace grants, reuse/detach/local-revoke API and durable bounded Telegram
   phone/code/2FA machine API. See
   [account reuse](CONNECTOR_ACCOUNT_REUSE.md) for the delivery limits.
5. **Migration graph:** linear `0123` -> `0124` -> `p12t14_accounts` -> `p12t14_tg_login`, not the former
   slice-only sibling arrangement. No legacy Telegram session import.

## Preserved slice provenance

The source heads below are historical checkpoints; their individual passing
checks do not prove the consolidated tree. Local consolidation preserves these
heads in its integration ancestry. Publication uses the GitHub file API to copy
the exact verified resulting tree into the existing #308 branch, so the final
published commit need not carry every source head as a Git ancestor. The mapping
below and tree equality are the provenance contract; no old PR is marked merged.

| Old slice | Source head | Retained scope |
| --- | --- | --- |
| #314 | `2b9766d4aea2170ee75b044cc7208e59d78a3a69` | VK refresh concurrency fence |
| #317 | `856b3711aa8f13d413257c4d0258867b85b73c65` | Refresh encryption, migration, key rotation |
| #321 | `957bc2baa8833f90445302130bb911fe6ab96900` | Preserve bootstrap revocations |
| #322 | `b246e03a63f17a00f40a651e5d7b4086958ebe73` | OAuth guidance and bounded errors |
| #323 | `17bfbada39d49bdc18745c9716619eb915110c4b` | Duplicate OAuth guidance; source-only checks retained |
| #324 | `de44344659ba7737cb9cd690974db1332795052f` | OAuth query encoding |
| #325 | `94f9ffbd69e746646647ef9764a36037edaefab2` | OAuth endpoint trust policy |
| #326 | `04e00e62d20df18038190d04d4a53f808a2b42f4` | Selected Telegram owner isolation |
| #328 | `20411cb6b69638a6036a9c00cd061f3e013f3b7b` | Read-only source identity inventory |
| #330 | `50b7a9b7c2212efed89ee803e8d39253af04c490` | Read-only owner bootstrap inventory |
| #331 | `cf31e40b7022a9edbbca93dfe492705b7bec584d` | Refresh status and unreadable-key guidance |
| #332 | `075105b66a0ccb28bf7290e04a78dadb09dd414a` | Reason-aware preflight and notifications |
| #333 | `b1b95469e2c5c61ac460c397cbd703db976935e4` | Known-unsent unreadable-key recovery |
| #334 | `26527604f9809ae520a730de5e601d1911a9200a` | No global MTProto session fallback |
| #335 | `217021becea3f094fb8e48c44277c49df5f1629b` | Same-user reusable authorization grants |

Conflict resolutions retain #322's stronger bounded configuration error handling
instead of #323's exception echo; source-level checks now require redaction for
both error classes. They retain #331's `renewal_unverified` and unreadable-key
caveat, #334's no-global-session behavior and #335's explicit workspace-consent
model. Authorization schema now follows 0124; both secret-only revisions skip
unrelated permission-catalog reconciliation. Consolidation also fixes key
rotation to include personal authorization ciphertext and to refuse unsupported
or mismatched schema generations, avoiding an unreadable-store key switch.

## Source-grant inspection continuation

`telegram_grant_access` now provides strict non-secret source/owner/grant/revision
pins and a fresh joined metadata inspector. Unit tests use fake sessions and SQL
compilation; a separate PostgreSQL integration file is authored but unrun in the
remote no-database lane. This is a preparatory slice only: no collector call site,
secret resolution, source-picker binding write or session lease is delivered.
Snapshots cannot authorize I/O or establish provider/envelope health. See the
account-reuse contract for fields and freshness limits.

## Account-only local revocation continuation

The JWT-only personal metadata list and existing personal-wide local-revoke route
now work without workspace membership or a live encryption key. Only the owned
authorization and own consents can be invalidated; no provider logout is claimed.
The middleware exception is exact-method/path only. Workspace attach/detach/login
remain membership-bound. Fake-transaction/JWT-ASGI tests are distinct from the
authored-but-unrun PostgreSQL/assembled-app rollback and no-membership checks.
This API slice alone did not deliver account settings UI or reauthorization/reconsent;
the subsequent partial web slice is described below.

## Machine API contract continuation

Connector-account responses now have explicit metadata-only OpenAPI models and
HTTP output field filtering, with fixed non-echoing 500 on invalid internal
projections. Existing success fields/statuses, nullable IDs and datetime offsets
are preserved. Legacy credential login is declared deprecated with its actual
409 response; credential-disable compatibility aliases remain unchanged. The
generated route index includes personal listing. DB-exempt JWT/ASGI and mounted
application OpenAPI checks are not database or real consumer evidence. API
consumer migration and full migration-history checks remain open.

## Personal metadata and local-revoke web continuation

The browser-session page lists owned new-store Telegram metadata and provides an
explicit confirmation for personal-wide local revoke, including without any
workspace membership. The exact-path middleware exception does not extend to
login, attach, detach or workspace settings. A bounded form and session/actor/page-
bound CSRF token guard the action; the service rechecks owner and revision. Errors
are opaque and an uncertain result is not reported as success. No remote logout,
legacy vault modification or provider-health claim is made. Fake-service HTTP and
offline DOM checks do not prove real database transactions, real CDN rendering,
provider connectivity or the complete login/consent journey.

## Remaining completion gates

- Telegram reauthorization/recovery, metadata retention and operational recovery;
  bounded machine login is delivered but is not a web onboarding UI. Scheduled
  expired-ciphertext cleanup is implemented but PostgreSQL concurrency/rollback
  validation is still required. No live provider validation is claimed.
- Complete login, reauthorization/reconsent and workspace account/source picker UI;
  assembled CSRF/DOM journeys with a fake provider; fresh execution-time grant,
  owner, membership and revision checks; shared-session execution lease/locking.
- Eliminate legacy Telegram vault fallback from the runtime, not merely from
  environment. The collector is still user-vault based at this checkpoint.
- API consumer compatibility for changed status/unsupported legacy login;
  current combined-tree tests and full migration-history validation; independent
  audit. Q-5/music live-provider gates and rollout approvals remain separate.

#311 is protected coordination work, not another implementation slice to merge;
it and other tasks' PRs/branches are intentionally unchanged. Never interpret the
single-PR workflow as authority to edit another task, apply a public migration,
read/copy real sessions, contact a live provider, rotate a key or deploy.
