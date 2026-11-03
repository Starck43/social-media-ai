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
   per-workspace grants, reuse/detach/local-revoke API. See
   [account reuse](CONNECTOR_ACCOUNT_REUSE.md) for the delivery limits.
5. **Migration graph:** linear `0123` -> `0124` -> `p12t14_accounts`, not the former
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

## Remaining completion gates

- Real supported Telegram phone/code/2FA flow with bounded encrypted attempts,
  reauthorization/recovery, replay/concurrency/cancel/restart/multi-worker safety.
- Account/source picker web UI and CSRF/DOM coverage; fresh execution-time grant,
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
