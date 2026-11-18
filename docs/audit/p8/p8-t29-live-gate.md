# P8-T29 — bounded live VK collection-path evidence (2026-11-18)

Status: **PASS** for the card's live collection-path gate. This is executor
evidence on the exact candidate, not an audit acceptance. It does not re-open or
duplicate the accepted Q-5 profile/method decision.

Candidate: `0796ca7492043f186fe905994c084ddfee2b6001` (`origin/dev` at run
time), worktree `/private/tmp/social-p8-t29-audit`, branch
`type/p8-t29-exact-revision-audit`. Owner authorized one bounded live run on
2026-11-18. An initial transport failure (`httpx.ConnectError`) was recorded in
the earlier revision of this file; after the owner restored network access the
same path succeeded.

## Method

One read-only `newsfeed.search` through the real `VKCollectionAdapter` and
`VKClient` (`SingleRequestVKClient` overrides only `_fetch_page` to perform one
HTTP call, error-check the response and skip rate-limit retries), with
`owner_user_id=2` so the real credential resolver loaded the owner's personal
vault row. No token was printed, logged, persisted or echoed into a task. The
captured normalized item was written to a local scratch file so subsequent
staging steps did not repeat the network request; that scratch file is deleted at
closeout.

| Field | Value |
| --- | --- |
| Method / host | `newsfeed.search`, `https://api.vk.com/method` |
| API version | `5.199` (from `Platform.params`, not environment) |
| Bounds | one request, `limit=1`, no retry, no write to VK |
| Credential | personal VK ID user token, resolved from the owner's vault row; not printed |
| Result | 1 normalized item, `total_available=3849` |
| Item identity | `external_id=-149014153_69645` |
| Provenance | `permalink=https://vk.com/wall-149014153_69645`, `published_at=2026-11-18T09:16:57+00:00`, `content_type=post` |
| Content prefix | `Сварочные работы системы отопления …` (job post; no provider diagnostics retained) |

## Staging and analysis boundary

A synthetic tenant, user and `SEARCH` source were created in `public` only as
isolated task-scoped rows. The real `ContentCollector.collect_from_source`
staged the captured normalized item through `build_staged_rows` /
`CollectedItem.objects.store_items`; no provider response was mocked at this
step and there was no second network call. Assertions: one staged row, exact
`external_id`, exact `permalink`, non-null `published_at`, same text prefix and
the run id. `CollectedItem.as_agent_item()` was then read at the analysis
boundary and checked for the same identity and provenance. A saved-result
double carrying that item's `item_hash` retired exactly one staged row via
`_retire_staged`; zero rows remained. The paid model and external delivery were
not called.

Observed output (abridged):

```
LIVE method=newsfeed.search api_version=5.199 host=https://api.vk.com/method
LIVE items=1 total_available=3849
LIVE item external_id=-149014153_69645 permalink=https://vk.com/wall-149014153_69645
STAGED rows=1 source_id=3186 run_id=1578861528
STAGED external_id=-149014153_69645 permalink=https://vk.com/wall-149014153_69645
ANALYSIS item external_id=-149014153_69645 permalink=https://vk.com/wall-149014153_69645
ANALYSIS retired=1 remaining=0 (saved result drains only its own hash)
VERDICT PASS: live official-API item reached staging and the analysis boundary with provenance and coverage
CLEANUP synthetic tenant/source/user removed
```

## Cleanup and limits

All synthetic tenants, users, memberships, sources and staged rows were removed
through the project's managers and verified absent (`p8t29live-%` rows: tenants
0, users 0, sources 0; the live `external_id` in `collected_items`: 0). No
migration was applied to `public`; its revision was observed at
`p21t14_rate_limits` and was not changed. No digest or external send was run.
One network, one credential, one run; this is not an independent audit and does
not certify production availability from a deployment network.

## Exact-revision source and DB checks

Recorded in the [acceptance audit](p8-t29.md): 82 source-only tests and 69
DB-backed tests passed at the candidate; three unrelated suites have
pre-existing teardown/context failures at current `dev`, with all synthetic
residue removed. `test_schema` was reconciled from `p21t14_rate_limits` to dev
head `p12t15_policy_audit` under exclusive ownership with an additive-only
inventory diff.
