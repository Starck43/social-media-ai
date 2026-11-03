# VK: a Standalone application for the search family

Status: open — a historical deployment probe reported VK global search refusal
with a web application. These observations do not establish current credentials
or prove that a different application profile will resolve the restriction.

## What is refused, and why

A read-only probe of the live credentials (2026-10-21) answered:

| method | VK profile | service key | personal token |
| --- | --- | --- | --- |
| `users.get`, `groups.getById` | any | OK | OK |
| `wall.get` | any | OK | OK |
| `account.getInfo`, `newsfeed.get` | user only | 28 | 1051 |
| `newsfeed.search`, `groups.search`, `users.search`, `database.getCities`, `utils.resolveScreenName` | user only | 1051 | 1051 |

`28` is the platform's own answer for a community token on a user method.
`1051` ("method is unavailable with current profile type") is the answer for
**both** credentials of this deployment — the gate is the *application profile*,
not the token. The vk.ru web search is a different thing entirely: it is the
site's own internal search over the logged-in session, not the public API, so
it working says nothing about API access.

Only three public API methods can find entities at all: `groups.search`,
`users.search` and `newsfeed.search`. The web UI's tabbed search (posts,
communities, clips, goods, video) has no public equivalent.

## What the owner has to do

1. Review the current VK documentation and application profile, callback and
   required grants for the exact methods. A Standalone profile is a hypothesis,
   not verified evidence that this app's PKCE callback or every method will work.
2. Configure `VK_APP_ID` in the deployment secret/config store and configure
   OAuth URLs plus the exact registered callback in shared `Platform.params`.
   `VK_REDIRECT_URI` is a legacy migration input, not a runtime override. The
   current exchange does not send `VK_CLIENT_ACCESS_KEY`; retaining that legacy
   setting is not proof of provider support for any specific profile.
3. Only after explicit owner authorization, authorize an individual user and run
   bounded provider checks for each desired method. Record outcomes under Q-5;
   do not activate categories or describe grants as verified based on mocks.

## What changes in the product when it works

When the exact methods are authorized and independently verified, the population step can search with the
declared query, city and kind, turns every found entity into a task-owned
wall lane and collects those walls in the same run. The named blocker
(`api_1051`) is replaced by the found population, and the per-lane evidence
rows on the task page show what was found and collected.

Until then the population is the known sources of the workspace plus whatever
the operator imports by hand
(`python -m cli.main collection import <task_id> --community … --person …`).

Tracked as [Q-5](tasks/q-5.md) in [ROADMAP](../ROADMAP.md#questions).

## P8-T29 activation evidence

The [universal task contract](AGENT_TASKS_PLAN.md#explicit-task-plans) requires separate evidence for social API search and known-wall access. Historical profile error 1051 remains tracked in Q-5. Do not assume a Standalone profile change or token refresh resolves it without a bounded real method call. Use official APIs for collection; interactive OAuth authorization is not browser collection.
