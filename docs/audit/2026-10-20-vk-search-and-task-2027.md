# VK search audit and task 2027: trip to Italy with Dreviz

Audit date: 2026-10-20 | Scope: VK collection path, task 2027, live read-only API
probes and one dry-run collection. No raw rows written, no LLM calls.

## Request

Task 2027 was meant to collect every mention of the interior-design trip to
Italy organised by Aleksandr Kabanov / Dreviz (Kirov), narrowed to designer
communities, from 1 October 2026. The operator asked for the same control the
vk.ru search sidebar gives: a query, a community/people cut and a date window.

## What VK actually offers

| Capability | API method | Works here |
| --- | --- | --- |
| Posts across all of VK | `newsfeed.search` (`q`, `start_time`, `end_time`, `extended`, 1000-result ceiling) | **no — error 1051** |
| Community search | `groups.search` (`q`, `type`, `city_id`, `country_id`, `sort`) | **no — error 1051** |
| People search | `users.search` (`q`, `city`, `sex`, `age`, `company`, …) | **no — error 1051** |
| City ids for filters | `database.getCities` | **no — error 1051** |
| One wall | `wall.get` / `wall.getComments` | yes |
| Community by screen name | `groups.getById` | yes |
| User by screen name | `users.get` | yes |
| Screen-name resolver | `utils.resolveScreenName` | **no — error 1051** |

`1051 "Method is not available for this profile type"` is answered for **both**
the personal VK ID token and `VK_SERVICE_KEY`. `wall.get`, `users.get` and
`groups.getById` work with the same user token. This is a VK application-type
limitation, not a client bug: the search methods need an app profile that is
allowed to call them (a standalone app / search-enabled app). Until the
deployment gets such credentials, the global-search half of the request cannot
be executed, and `groups.search` cannot discover designer communities.

## Code gaps found

1. **No search at all.** `VKClient` only knew `wall.get`, `wall.getComments`,
   `users.get`, `groups.getById`, `market.get`, `photos.get`,
   `messages.getHistory`. There was no way to express "search posts" or "find
   communities", so the source model could only be a wall.
2. **`utils.resolveScreenName` is unavailable (1051).** A community screen name
   could not be resolved; `groups.getById` resolves it and is allowed, so it is
   now tried first.
3. **`wall.get` ignores `start_time`/`end_time`.** The client sent them but VK
   returns the newest page regardless (`2026-09-27` appeared in a window that
   started `2026-10-01`). Client-side date filtering only ran for
   `force_refresh`, so an ordinary task run had no lower bound at all. Fixed:
   every full collection filters by `cli_dates.start_date` and drops a whole
   page once it is older than the window.
4. **VK items carried no author or permalink.** `collected_items.author` stayed
   empty, so `tracked_users` could never match a VK item and the UI had no
   link back. Normalisation now writes `author` from `from_id`/`owner_id`
   (with the community/profile name when `extended=1`) and a
   `https://vk.com/wall{owner}_{post}` permalink.
5. **No run ceiling.** A first run without a date bound could park a full
   archive in `collected_items`. A per-source cap now defaults to 1000 rows
   (`SOCIAL_COLLECT_LIMIT`), can be lowered via `collection.limit`, and can
   never exceed the hard ceiling.
6. **No rehearsal mode.** Every collection staged raw rows and could spend a
   model call. `--dry-on` (CLI) and a task toggle (web, admin/owner) now fetch,
   count, print a bounded preview and stop — no rows, no LLM, no cursor move.
7. **Dead configuration.** `excluded_users`, `blacklist` and `whitelist` were
   removed from the algorithms, forms, CLI and chat tools; the task row keeps
   only rate/cooldown guards and the new `dry_run` flag.

## Task 2027 before / after

| Field | Before | After |
| --- | --- | --- |
| start date | 2026-09-15 | **2026-10-01** |
| keywords | `Поездка`, `Италия` | `Древиз`, `Вревиз`, `Италия`, `поездка` |
| match | (unset → any) | any |
| brands | — | `Древиз`, `Вревиз` |
| sources | Kabanov personal wall | Kabanov, `dreviz2000` (real open company community), two designer walls |
| dry run | — | **on** |

The first source added by name, `dreviz`, turned out to be a different closed
community (error 15, wall available only to members). The real company is
`dreviz2000` / `-113075242`, open: 768 posts, October posts readable.

## Live evidence

- `collect --src 3145 --start-date 01-10-2026 --dry-on`: **0 items** — the
  organiser's own wall stopped at 2026-09-27.
- `collect --src 3160 --start-date 01-10-2026 --dry-on`: **6 items** from
  2026-10-01…16, e.g. `https://vk.com/wall-113075242_1163`; no Italy mention in
  the visible October window.
- Selection over the organiser's 300 newest posts with the new scope: 10
  matches, including `Группа компаний «Древиз»` mentions; no `Италия` match in
  that window.
- `groups.search`, `users.search`, `newsfeed.search` and `database.getCities`
  return `1051` for the user token and the service key; `wall.get` works.

## UI follow-up

Two operator-visible defects surfaced while testing task 2027:

1. **«Сохранить и выполнить» did not run.** The submit handler set `sending`
   before submission, and Alpine's `x-bind:disabled="sending"` on the submit
   button removed that button (and its `run_now=on` value) from the POST. The
   form saved the task and redirected to its page, but no job was queued. The
   buttons are no longer disabled before submit; the existing `x-on:submit`
   guard still prevents a double submit.
2. **The admin diagnostics did not answer “why no data”.** It exposed only a
   job-level allowlisted code. It now lists every source in the run with its
   outcome, item/new counts, and a bounded platform cause — for example
   `api_1051 · метод недоступен для типа приложения` for the VK search source,
   or `empty · нет записей в выбранном окне` for the organiser's wall. The raw
   provider text is still not stored or rendered.
3. **A finished run kept the «⏳ Job #… выполняется» badge** until a full page
   reload. The polled run fragment now removes the badge when the latest job is
   no longer pending/running.
4. **A dry run read as «Пропущено».** The outcome now has its own
   «Тестовый прогон» label and note, and a partial run keeps the dry-run
   prefix ("данные не сохранены, модель не вызывалась").
5. **A dry run did not say how many fetched records would be analysed.**
   It now runs the deterministic selection over the full fetched batch and
   reports `eligible`/`rejected`/reasons (plus a three-item sample), without
   staging or calling a model. The admin diagnostics renders
   «прошли фильтр N из M». Task 2027 now keeps only the topic phrases
   (`Италия`, `поездка`); the brand names moved to the analysis targets, so
   every Dreviz post is not eligible merely because the community is named
   «Древиз». The 2026-10-01…16 Dreviz batch is `0 из 6` — no Italy/trip
   mention in the visible window.

## Blockers / next action

1. **Credentials:** replace or re-configure the VK app so search methods are
   allowed. Without it `newsfeed.search` and `groups.search` stay unavailable
   and designer communities cannot be discovered automatically.
2. **Membership:** if a closed community is part of the event, the collecting
   account must be a member; `wall.get` cannot bypass VK privacy.
3. **Content:** as of 2026-10-20 neither the organiser nor the Dreviz wall
   contains an Italy post in the October window. The task is configured and
   will pick mentions up on later runs; the first live pass cannot invent them.
