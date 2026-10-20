# Task selection: sources, tracked users and text scope

How a task decides *which collected items are analysed*. Selection is a
property of the **task**; a scenario still answers only "how to analyse" and a
source only "which wall/channel we may read".

## Why the old model is replaced

Until this refactor a task could carry `payload.monitored_users`. It did not
filter anything: for every source of the task the collector replaced the wall
with the personal walls of the listed users, and a listed user only worked when
a separate `Source` row of type `USER` already existed. That produced duplicate
sources for one person (one to serve as the wall registry, one to be tracked),
silently analysed nothing from the task's own sources, and stored the same
setting in two places (`Source.params["monitored_users"]` and the task payload)
with different effects. The setting is removed, not deprecated: there is no
legacy read path.

## The three layers of a task scope

| Layer | Where | Meaning |
| --- | --- | --- |
| **Sources** | `agent_task_sources` | Walls/channels the task may read. Empty set = every active workspace source (existing semantics). |
| **Tracked users** | `AgentTask.payload["tracked_users"]` | Authors whose items are eligible for analysis. An item is eligible when its author is in the list. |
| **Text scope** | `AgentTask.payload["keywords_list"]`, `payload["topics_list"]` | Phrases an eligible item must contain (see matching rules). |

The layers are applied in order: an item must come from a task source, carry an
eligible author, and pass the text scope. The same order is reported in run
diagnostics, so an operator sees *why* an item was left out.

### Tracked users

`tracked_users` is a **filter on the author**, never a collection target. It does
not read anyone's personal wall, and it never adds a source to the task.

Stored as a list of flat strings, `<platform>:<external_id>`:

```json
{"tracked_users": ["vk:123456", "telegram:@durov"]}
```

- `external_id` is what the platform client already resolves for a source —
  a numeric id for VK users, a username/chat id for Telegram. Accepting
  `vk:@name` or `vk:name` in the UI is a convenience that must be resolved to
  the numeric id through the platform client before it is stored; the stored
  value is the resolved one so a rename cannot silently retarget the filter.
- An empty list means "no author filter": the task analyses the source's own
  items under the scenario's `content_types`, not "nobody" (see below).
- Identity is platform-scoped; `vk:123` and `telegram:123` are different people.

An item's author is read from the collected snapshot
(`collected_items.author`: the next `Author/Timestamp` pair, i.e. whose
"next post" a wall repost carries), not from `Source.params`, so a wall that
reposts other people is filterable by the person it carries.

**Without `tracked_users` the task analyses the source's own content, as the
scenario says.** The scenario's `content_types` (`posts`, `comments`, `likes`,
…) already decides what is collected and analysed; the selection layers never
change it:

- no `tracked_users` → the items are those of the source itself
  (`content_types` of the scenario applied to the wall/channel);
- `tracked_users` given → **only** those authors' items of the same
  `content_types` are analysed. The task does not widen the content types and
  does not fall back to the source's own items when a tracked author has
  nothing.

A task whose `tracked_users` are all unknown to the collector reports an empty
selection with a named reason (`unknown_tracked_user`), never a silent "nothing
found".

### Text scope

`keywords_list` (and `topics_list` as a synonym of the same scope) are whole
phrases matched against the item text plus its author name and permalink, within
the content types the scenario already selected:

- matching is case-insensitive, word-bounded, `ё` = `е`; a phrase matches as a
  contiguous sequence of words, and Russian inflections are folded to a common
  stem (`utils/ru_stem.py`) so «Италия» matches «Италию»/«Италии». Stemming is a
  matching key, not a synonym: «дизайн» still does not match «дизайнер»;
- `payload["keywords_match"]` is `"any"` (default) or `"all"`;
- the same list is *also* offered to the model as analysis context
  (`{payload_instruction}`), but the deterministic scope is what decides whether
  an item is paid for. A prompt is not a filter.
- An empty scope means every eligible item is analysed.

### Region and city

There is no region column and no platform filter for it: VK does not expose a
post's city, and a profile city is self-declared. A city is expressed as
ordinary text-scope phrases ("Киров", "Kirov") plus the sources whose audience
is that city. The UI must label it that way rather than implying a geographic
filter.

## What is not selection

- **Reaction/action triggers** (`KEYWORD_MATCH`, `USER_MENTION`,
  `SENTIMENT_THRESHOLD`, `ACTIVITY_SPIKE`) decide whether a stored analysis
  produces an action. They never decide whether an item is analysed; a keyword
  for an alert and a keyword for a selection are different intentions and live
  in different fields.
- **Analytics filters** (`/app/analytics`) narrow the report over rows that were
  already analysed. They never reduce spend and never change a task.
- **A scenario's `scope`** holds methodology only. Concrete people, brands and
  phrases there are rejected by `param_registry` rules.

## Removal plan of `monitored_users` (single migration window, pre-production)

1. Code: no reader of `payload.monitored_users` or
   `Source.params["monitored_users"]`; `collect_monitored_users` is replaced by
   the selection path (see `docs/COLLECTION.md`) or deleted with its API route.
2. Data: an Alembic migration strips the key from `agent_tasks.payload` and
   `sources.params`, and (for tasks that carried it) writes the equivalent
   `tracked_users` only where the value can be resolved to a platform id and the
   operator confirms; unresolved values are listed in the migration log, never
   guessed.
3. Docs, CLI flags, API schema, chat tools, web forms and admin help are changed
   in the same release; no compatibility flag is kept.

## Validation focus

No-DB tests for parsing and matching boundaries (`any`/`all`, `ё`, word bounds,
platform scope, empty scope), task-payload normalisation rejects malformed
entries, and a mocked collector proves that a non-tracked author's item is
staged but not analysed. Screening accuracy against the real platform is a
separate, live acceptance step and is not claimed by unit tests.
