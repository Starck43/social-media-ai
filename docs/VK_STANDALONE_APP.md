# VK: a Standalone application for the search family

Status: open — the deployment still uses a web application, so VK's global
search family is refused. Everything else in the collection path works with the
current credentials.

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

1. Create (or convert) a **Standalone** application:
   `vk.com/apps?act=manage` → «Создать приложение» → type **Standalone**.
   A Standalone app redirects to `https://oauth.vk.com/blank.html`; the current
   app redirects to `http://localhost/api/v1/social/callback` (a web app), and
   that is what the platform refuses.
2. Put the new app id and its client secret into `.env`
   (`VK_APP_ID`, `VK_CLIENT_ACCESS_KEY`) and set `VK_REDIRECT_URI` to
   `https://oauth.vk.com/blank.html`.
3. Authorize the workspace owner for the new app (the CLI command
   `python -m cli.main credentials oauth vk` starts the PKCE flow; the personal
   token lands in the vault with the app's rights).
4. Check the result with one read-only call:
   ```
   .venv/bin/python -m cli.main collection import <task_id> --tenant owner \
       --community "dreviz2000" --dry-run
   ```
   or simply re-run the task: the population step then searches instead of
   reporting `api_1051`.

## What changes in the product when it works

Nothing has to be rebuilt: the population step already searches with the
declared query, city and kind, turns every found entity into a task-owned
wall lane and collects those walls in the same run. The named blocker
(`api_1051`) is replaced by the found population, and the per-lane evidence
rows on the task page show what was found and collected.

Until then the population is the known sources of the workspace plus whatever
the operator imports by hand
(`python -m cli.main collection import <task_id> --community … --person …`).

Tracked as **Q-9** in the root `ROADMAP.md`.

## P8-T29 activation evidence

The [universal task contract](design/universal-tasks.md) requires separate evidence for social API search and known-wall access. Historical profile error 1051 remains tracked in Q-5. Do not assume a Standalone profile change or token refresh resolves it without a bounded real method call. Use official APIs for collection; interactive OAuth authorization is not browser collection.
