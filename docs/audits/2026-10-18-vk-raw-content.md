# VK raw collection audit: task 1582

Audit date: 2026-10-18 | Code revision: `b70911d6c1e0c6a71e5668a502df42f1e73fefa9`  
Scope: tenant 1, `public`, task 1582/source 2939; ORM read-only inspection and
manual review of bounded text excerpts. Full raw text was not copied. No LLM or
data modifications.

## Run and collection configuration

Task 1582 is currently a `collect` task linked to the VK USER source 2939,
`annastrizhovadesign`. Its targets are brands `Арт-Сервис` and `HiTechHouse`,
hashtags `артсервис` and `hitecthouse`, with `start_date=2026-04-01` and
`force_refresh=true`. The successful collection Job 1739 ran from
`2026-10-17T21:04:41.990Z` to `2026-10-17T21:04:45.164Z` (3.174 seconds): 51
items fetched and staged, 29 counted as new, zero source errors, and zero inline
analysis. The 23 existing analytics rows did not change.

The 51 raw rows belong to Job 1739, have distinct external IDs and hashes, and
are dated 2026-04-03 through 2026-10-13, inside the requested lower-bounded
window. There is no end date. Collection inserts new `(source_id, external_id)`
identities and ignores conflicts; it does not replace existing raw or analytics
rows. Because this task does not enable inline analysis, the rows remain staged
for an analyze task. The collect form maps its refresh checkbox to
`force_reanalyze` too, but that flag has no immediate analysis to affect here.

## Which VK content was requested

The task payload has no `monitored_users` list. Although source parameters have
one, the scheduled collect handler selects that list from task payload only;
this run therefore collected the selected source wall rather than those source
parameter accounts. The source has no per-source collection overrides. The VK
client resolves its screen name and uses the `wall.get` path with the configured
date window; absent overrides, its request defaults are `count=50`, `offset=0`,
`extended=1`, and `filter=all`. The collector paginates, so 51 rows are expected
with a default page size of 50. Per-request responses were not retained, so the
exact paging sequence cannot be reconstructed.

Origin is not certified. The stored row fields do not retain VK's original
response or `copy_history`, so owner IDs alone cannot distinguish a wall post
from a repost or establish its original author/context. Stored owner IDs are
19149862 (30 rows), -55674807 (20), and -51955758 (1). A bounded coordinator
probe loaded the 51 stored IDs, then made one HTTP attempt to
`utils.resolveScreenName`; it timed out after 10 seconds. It did not call
`wall.get` and obtained no VK wall response. An earlier probe hit a 30-second
overall deadline at an unknown stage, so total network-call count cannot be
stated. Task/job association and intended request parameters are observable,
but the 51 rows cannot be certified as a faithful VK response or their original
post/repost context. Source response and media references remain unverified.

## Target relevance and media coverage

Manual review found six textual matches, all to the punctuation/case alias
`Hi-Tech House`. Four are substantive showroom/home-cinema mentions; two are
lists. Under the agreed any-explicit-target rule, all six pass.

| Published | External ID | Semantic context | Result |
| --- | --- | --- | --- |
| 2026-08-11 | `-55674807_1232` | List mention | Target mention |
| 2026-08-07 | `-55674807_1226` | List mention | Target mention |
| 2026-08-03 | `-55674807_1220` | Home-cinema integration | Target mention |
| 2026-05-30 | `19149862_2227` | Showroom concept | Target mention |
| 2026-05-22 | `-55674807_1107` | Showroom | Target mention |
| 2026-05-16 | `-55674807_1095` | Showroom | Target mention |

No row text contains `Арт-Сервис`, `HiTechHouse`, or literal `#артсервис`,
`#hitecthouse`, `#hitechhouse`. `Hi-Tech House` is a brand alias, not a literal
hashtag match; hashtags are not normalized. The other 26 text rows have no
target; they mainly concern SETUS/Anna interiors, venue (`2026-09-21`,
`-55674807_1272`), Aura Home SALON (`2026-09-07`, `-55674807_1255`), restaurant
Сказки (`2026-06-19`, `-55674807_1164`), Milan trends (`2026-06-08`,
`-55674807_1133`), family graduation (`2026-07-01`, `19149862_2247`), and
Anna/SETUS interview (`2026-04-09`, `-51955758_28874`). No other explicit
target/tag was found. Nineteen empty-text video rows are unclassifiable. All
stored media URLs are null, so media cannot confirm these text-only results.

Stored attachments are JSON with `type` and `url`: 61 images, 33 videos, all
URLs null; all 51 permalinks are null, so independent media review is unavailable.

P5-T02 previously confirmed that scenario 8's custom prompt does not append
task-payload targets automatically. Since this collect Job performed no
analysis, Job 1739 itself says nothing about downstream model relevance. Any
subsequent analysis must be audited separately against its actual task payload
and prompt; no LLM comparison was run for this audit.

## Form validation and operator feedback

The edit form splits `keywords_list` and `topic_list` as comma-separated
targets. Task 1582 is currently `collect`, while its bound scenario's analysis
types include `keywords` and `topics`. The shared form check therefore emits a
missing-target warning for this collect task too. It is explicitly
non-blocking: when `run_now` is set, the update still persists and enqueues. The
warning is relevant to an analyze task or an inline-analysis collect run, not a
collect-only run; recommend gating it on actual analysis execution. A
missing required start date is a separate blocking validation error; task 1582
has a start date. The current Safari snapshot was on the completed Job page,
with no visible flash and no edit form loaded. Thus the user's earlier warning
was not observed in the snapshot; it does not establish what that browser
displayed at the time.

## Bounded follow-up

1. Reconstruct or verify source 2939 post origin and missing media references
   against an original VK API response, if one can be obtained; see Q-2 in
   `ROADMAP.md`.
2. Make task-vs-source `monitored_users` precedence explicit in the UI and
   collection contract; source settings currently do not drive this task path.
3. Correct the collect toggle label “Перетирать данные за этот период” and its
   hint, which promise to refetch and re-analyze the period. Collect-only runs
   default to staging raw rows, whose existing `(source_id, external_id)` values
   are not overwritten; a separate analyze task is needed.
4. Ensure the next analyze run carries the task targets into its prompt, as
   already recommended by the P5-T02 prompt audit.
Code references: `app/jobs/handlers.py:256-297,340-381`; `app/services/social/vk_client.py:57-96,265-285,321-365,645-655`;
`app/models/managers/collected_item_manager.py:37-76`; `app/web/tasks.py:39-73,1142-1173,1227-1239`;
`app/services/ai/param_registry.py:113-129`; `app/services/ai/prompts.py:83-96,196-206`; `app/web/templates/web/_macros.html:135-143`.

No code, migrations, tests, LLM calls, task writes, or cleanup were performed;
the bounded probe returned no wall content, so origin verification remains open.
