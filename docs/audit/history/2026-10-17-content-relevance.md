# Content relevance audit: task 1582 / scenario 8

**Evidence revision:** `b70911d6c1e0c6a71e5668a502df42f1e73fefa9`  
**Status:** audit complete; controlled comparison is synthetic and does not reproduce historical raw content.

## Finding

Task 1582 targets brands `Арт-Сервис` and `HiTechHouse`, plus hashtags `артсервис` and `hitecthouse`, for VK source 2939 (`annastrizhovadesign`). Scenario 8 is selected, but its saved custom prompt does not contain `{payload_instruction}`; its scope has no brand list and no relevance filter. Thus the configured targets are not required in the model prompt and there is no deterministic target-match gate before analysis. This explains how unrelated or broadly project-related records can enter analytics. The latest saved result does contain a `Hi-Tech House` entity, so it is not wholly unrelated: it selected `SETUS Design` as the primary brand and summarized the wider Aura Home project.

## Stored-data evidence (public, read-only)

- Task 1582 is tenant 1, inactive, type `analyze`, linked to source 2939 and scenario 8. Its payload contains the two brands, two hashtags, and a start date of 2026-07-01. Its recorded last run is `ok` at `2026-10-17T00:54:46.835712Z`.
- Source 2939 has `monitored_users` set to `@s_shabalin` and `@artservice_kirov`. This is collection configuration, separate from the task's brand filters.
- Scenario 8 has a generic custom prompt, empty `scope.brand_mentions.brand_names`, no enabled `relevance_filter`, and model 11 configured for text and image. The saved scenario update time is 19:22:39.
- Source 2939 has 23 analytics rows: 22 from scenario 6 and one from scenario 8. The scenario-8 row is analytics 1129, created 18:46:56 and updated 19:22:58. It records the 2026-07-01–2026-08-19 window, 13 posts and 22 merged hashes; `original_links` is empty. Its stored `task_payload` is `{}`.
- There are no retained raw `CollectedItem` rows for this source. No job row for task 1582 is available to tie the analytics row to that task. Therefore the exact input batch and the initiating UI action cannot be reconstructed.
- The latest successful `LLMAttempt` for this source is attempt 20 at 19:22:58, model 11, with null job/task references. Attempt 13 is a failed output with the same missing references; it cannot be conclusively attributed to task 1582. The current attempt journal path does not pass task/job IDs into the journal.

## Code path and prompt replay

The analysis handler selects staged content by source and date window (`app/jobs/handlers.py:733-738`), then passes the task payload into `AIAnalyzer` (`app/jobs/handlers.py:751-765`). It does not filter selected rows for target-brand or hashtag matches. The analyzer's optional relevance filter only rejects model output marked not meaningful or below a confidence threshold, when enabled by the scenario (`app/services/ai/analyzer.py:1425-1446`); scenario 8 does not enable it.

`PromptBuilder` substitutes task values only where the custom template references a variable and returns the generic default prompt when no custom prompt exists (`app/services/ai/prompts.py:27-97`). It constructs `payload_instruction` from target parameters (`app/services/ai/prompts.py:196-206`), but that cannot affect a template that omits the variable. The unified-summary prompt receives analysis results and scenario only, without task payload (`app/services/ai/prompts.py:99-132`). Replay with three combinations—current scenario/current task payload, saved scenario/empty payload, and saved scenario/current task payload—left both brands and both hashtags absent from the final text prompt in all cases.

The result shape flattens `brand_mentions` to scalar root fields `brand`, `context`, and `sentiment` (`app/services/ai/json_schema_builder.py:114-118`), which cannot represent distinct evidence for multiple configured targets. `AIAnalytics` is unique by source/date/period and has no task/scenario columns (`app/models/analysis/ai_analytics.py:18-63`). Scenario metadata is stored inside `summary_data` (`app/services/ai/analyzer.py:1624-1631`), but an update replaces that summary snapshot (`app/services/ai/analyzer.py:1673-1700`), so it is not a durable run-level provenance ledger. Source history loads recent analytics by `source_id` alone (`app/web/sources.py:479-481`), so the list may mix scenarios and tasks.

There is a proven taskless source-page path that could produce analytics with an empty task payload, but the available evidence does not establish that it created analytics 1129. The source “Analyze now” handler enqueues only `source_ids` and the content window (`app/web/sources.py:1040-1045`). `_task_payload(None)` returns `{}` (`app/jobs/handlers.py:91-93`); the handler can separately resolve a scenario with `get_source_scenario(source.id)` (`app/jobs/handlers.py:691-704`, `app/models/managers/agent_task_manager.py:225-259`). That lookup returns a scenario, not its owning task payload. This is consistent with an empty stored `task_payload`, but missing job/task references prevent attribution of row 1129 to this path.

The recorded date window begins on July 1, matching the task's configured start date; that window alone is not evidence of an incorrect search. Source `monitored_users` are read from a task payload in the collection handler (`app/jobs/handlers.py:257-265`), so the source-level values above are not applied on this audited task path. An `analyze` task consumes staged rows; it does not collect new content itself.

## Controlled model comparison

Exactly two calls used model 11 (`nvidia/Qwen3.8-27B-NVFP4`), temperature 0, maximum 1024 output tokens, the same system messages, JSON response mode, output contract and seven-post synthetic fixture. The fixture included positive examples for each target and an alias, the configured hashtag, and distractor posts mentioning SETUS, generic interiors and Aura Home. Call B differed only by an appended explicit target instruction. There were no retries or fallback calls.

| Call | Prompt | Result | Usage/accounting |
| --- | --- | --- | --- |
| A | Current scenario prompt (3,925 chars) | Returned `Арт-Сервис` as primary brand and the matching hashtag. It also included HiTechHouse, SETUS Design and Aura Home in entities/keywords, so the response wandered beyond the requested targets. JSON and schema valid; `finish_reason=stop`, not truncated. | LLMAttempt 21; 1,191 input + 714 output tokens; $0.00 (model currently journaled as free). |
| B | Same prompt plus target instruction (4,488 chars) | Returned both `Арт-Сервис` and `HiTechHouse` in the brand field, included the Hi-Tech House alias, and omitted the SETUS/Aura distractors from entities. The fixture contained `#артсервис` but no `#hitecthouse`, so the second configured hashtag was not tested. JSON and schema valid; `finish_reason=stop`, not truncated. | LLMAttempt 22; 1,352 input + 617 output tokens; $0.00 (model currently journaled as free). |

Both calls succeeded with normal usage accounting (`purpose=admin_probe`, model 11, `certainty=free`); protected task, source, raw-content, and analytics table snapshots matched before and after. The scenario table was not included in that snapshot. The current prompt did not fail to identify the fixture's primary brand, because the brand was explicitly present in that fixture. The comparison instead shows that the added target instruction narrowed reported entities. The `hitecthouse` hashtag appeared only in B's instruction, not in any post; the fixture therefore does not test whether either prompt detects it in content. No per-post precision/recall is claimed from these aggregate responses. This is a controlled prompt comparison, not a replay of the historical input batch.

## Prioritized follow-up

1. Carry task targets through every analysis entry path, including source-page and inline collection flows; keep the task context attached through the final summary.
2. Add deterministic target matching before persistence. Match either configured brand or hashtag, use explicit aliases, and retain per-post evidence; do not treat an unconstrained model summary as proof of relevance.
3. Attribute analytics to the task/scenario/run that produced them and preserve provenance when a source/date row is updated. This is a separate provenance requirement; P10-T09 covers operational counts/activity and does not resolve it.
4. Make source history identify which scenario/task produced each result, and distinguish historical rows that cannot be attributed.
5. Reconcile source-level collection settings with task-level collection conditions and show the effective collection scope. Keep monitored accounts distinct from filters on post authors; task 1582 is `analyze`, so its own run does not collect content or apply collection settings.

## Limits

The exact historical prompt, model response envelope, and raw batch are not fully recoverable: raw content is absent, the stored task payload is empty, and attempt/job/task references are missing. The findings distinguish observed database facts from code-path explanation; no historical row is claimed to be caused by task 1582 with certainty. The synthetic comparison has only seven aggregate posts and does not establish production precision or recall. No business records were modified during this audit.
