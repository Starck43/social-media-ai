# VK: a Standalone application for the search family

Status: answered 2026-11-17 — the 2026-10-21 refusal below is historical. A
freshly authorized personal VK ID token on the same Web application profile now
calls `newsfeed.search`, `groups.search` and `users.search`; no Standalone
profile change is needed. Evidence: [Q-5 audit](../audit/q/q-5.md).

## The historical refusal (2026-10-21)

A read-only probe of the then-live credentials answered:

| method | VK profile | service key | personal token |
| --- | --- | --- | --- |
| `users.get`, `groups.getById` | any | OK | OK |
| `wall.get` | any | OK | OK |
| `account.getInfo`, `newsfeed.get` | user only | 28 | 1051 |
| `newsfeed.search`, `groups.search`, `users.search`, `database.getCities`, `utils.resolveScreenName` | user only | 1051 | 1051 |

`28` is the platform's own answer for a community token on a user method.
`1051` ("method is unavailable with current profile type") was the answer for
**both** credentials of the deployment at that time. That observation led to the
Standalone-profile hypothesis; it is superseded below.

Only three public API methods can find entities at all: `groups.search`,
`users.search` and `newsfeed.search`. The web UI's tabbed search (posts,
communities, clips, goods, video) has no public equivalent.

## What resolved it (2026-11-17)

VK granted the application extended access. A personal VK ID user token, freshly
authorized after the grant, calls `newsfeed.search`, `groups.search` and
`users.search` on the unchanged Web profile. The token carried only
`vkid.personal_info`, so no OAuth `scope` value was the gate: the grant binds at
authorization and a token issued earlier keeps the old result. Existing
connections need re-authorization, not a profile change.

The bounded live run recorded `users.search`, `groups.search` and
`newsfeed.search` as `ok`, `video.search` with provider error 15 and
`apps.getCatalog` as `ok`. Evidence and limits: [Q-5 audit](../audit/q/q-5.md).

The service key was not re-probed; the product search path uses a personal user
token for `groups.search`/`users.search` regardless.

## What changes in the product

With a verified connection the population step can search with the declared
query, city and kind, turns every found entity into a task-owned wall lane and
collects those walls in the same run. The named blocker (`api_1051`) is replaced
by the found population, and the per-lane evidence rows on the task page show
what was found and collected.

Category availability is still decided per category: `people` is backed by the
proven `users.search`; `video` and `market` stay `mocked` with their own gates.
Until a category is enabled, the population is the known sources of the workspace
plus whatever the operator imports by hand
(`python -m cli.main collection import <task_id> --community … --person …`).

Tracked as [Q-5](../audit/q/q-5.md) in [ROADMAP](../../ROADMAP.md#questions).

## P8-T29 activation evidence

The [universal task contract](../agent/AGENT_TASKS_PLAN.md#explicit-task-plans)
requires separate evidence for social API search and known-wall access.
Historical profile error 1051 is resolved for the three search methods by the
2026-11-17 live probe above; that run did not probe wall access and a successful
search is not proof of it. Use official APIs for collection; interactive OAuth
authorization is not browser collection.
