# P5-T01 — duplicate generated JSON key boundary

PR: https://github.com/Starck43/social-media-ai/pull/241

## Revisions / ownership

- Requested/fetched dev base: `aa8201709bca569fda7a6ba587e371ece5c52e9e`.
- Branch: `fix/p5-t01-typed-output-boundary`.
- First small claim commit: `526bad583c4bd0adecee3f79b57a9ac037c9c062` (ROADMAP row + execution record only).
- Exact tested implementation: `72da231767a478921710c7f78040bce275cba495`, clean detached verification worktree. Final READY/card/evidence updates are documentation-only. No rebase or integration-branch push.
- Production ownership: only `app/services/ai/llm_client.py::_try_json` (12 added lines and one decode call change).
- New tests: `tests/ai/test_p5_t01_duplicate_output_keys.py`. Other writes: this note, P5-T01 task card and that ROADMAP row only.
- AST comparison verifies the entire production module is unchanged after excluding `_try_json`; no client/admission/accounting/chat/permission/receipt/web/analyzer/delivery code changed. P5-T04's lane preserved; no migrations, runtime or deployment.

## Gap verified before implementation

Generated text `{"summary":"first","summary":"second"}` is correctly rejected by the raw-string `validate_output` contract, but baseline `_try_json` used default `json.loads` last-key-wins decoding. Its returned dict `{"summary":"second"}` passed the identical DigestSummary typed contract. Duplicate-key evidence was lost before validation; current-source reproducer confirmed this at the requested baseline.

The new suite ran before the fix against source HEAD `526bad583c4bd0adecee3f79b57a9ac037c9c062` (production code identical to `aa82017`; new regression file was uncommitted):

```bash
PYTHONPATH=tests:. python3 -m unittest tests.ai.test_p5_t01_duplicate_output_keys -v
```

RED output: exit **1**, **23 run / 17 expected failures / 6 passes / 0 errors/skips**. Duplicate cases did not reject (and therefore emitted no rejection log); positive controls already passed. Failed names:

- `test_digest_rejects_duplicate_but_journals_the_failed_attempt (tests.ai.test_p5_t01_duplicate_output_keys.AnthropicDuplicateTests.test_digest_rejects_duplicate_but_journals_the_failed_attempt)`
- `test_duplicate_summary_is_rejected_before_typed_validation (tests.ai.test_p5_t01_duplicate_output_keys.AnthropicDuplicateTests.test_duplicate_summary_is_rejected_before_typed_validation)`
- `test_failed_duplicate_keeps_unknown_accounting_unknown (tests.ai.test_p5_t01_duplicate_output_keys.AnthropicDuplicateTests.test_failed_duplicate_keeps_unknown_accounting_unknown)`
- `test_nested_duplicate_is_rejected_without_optional_schema (tests.ai.test_p5_t01_duplicate_output_keys.AnthropicDuplicateTests.test_nested_duplicate_is_rejected_without_optional_schema)`
- `test_digest_rejects_duplicate_but_journals_the_failed_attempt (tests.ai.test_p5_t01_duplicate_output_keys.CustomDuplicateTests.test_digest_rejects_duplicate_but_journals_the_failed_attempt)`
- `test_duplicate_summary_is_rejected_before_typed_validation (tests.ai.test_p5_t01_duplicate_output_keys.CustomDuplicateTests.test_duplicate_summary_is_rejected_before_typed_validation)`
- `test_failed_duplicate_keeps_unknown_accounting_unknown (tests.ai.test_p5_t01_duplicate_output_keys.CustomDuplicateTests.test_failed_duplicate_keeps_unknown_accounting_unknown)`
- `test_nested_duplicate_is_rejected_without_optional_schema (tests.ai.test_p5_t01_duplicate_output_keys.CustomDuplicateTests.test_nested_duplicate_is_rejected_without_optional_schema)`
- `test_duplicate_inside_array_is_rejected (tests.ai.test_p5_t01_duplicate_output_keys.GeneratedJSONTests.test_duplicate_inside_array_is_rejected)`
- `test_duplicate_names_and_values_are_not_in_error (tests.ai.test_p5_t01_duplicate_output_keys.GeneratedJSONTests.test_duplicate_names_and_values_are_not_in_error)`
- `test_duplicate_nested_object_is_rejected (tests.ai.test_p5_t01_duplicate_output_keys.GeneratedJSONTests.test_duplicate_nested_object_is_rejected)`
- `test_duplicate_summary_cannot_bypass_raw_contract (tests.ai.test_p5_t01_duplicate_output_keys.GeneratedJSONTests.test_duplicate_summary_cannot_bypass_raw_contract)`
- `test_unicode_escape_equivalent_keys_are_duplicates (tests.ai.test_p5_t01_duplicate_output_keys.GeneratedJSONTests.test_unicode_escape_equivalent_keys_are_duplicates)`
- `test_digest_rejects_duplicate_but_journals_the_failed_attempt (tests.ai.test_p5_t01_duplicate_output_keys.OpenAIDuplicateTests.test_digest_rejects_duplicate_but_journals_the_failed_attempt)`
- `test_duplicate_summary_is_rejected_before_typed_validation (tests.ai.test_p5_t01_duplicate_output_keys.OpenAIDuplicateTests.test_duplicate_summary_is_rejected_before_typed_validation)`
- `test_failed_duplicate_keeps_unknown_accounting_unknown (tests.ai.test_p5_t01_duplicate_output_keys.OpenAIDuplicateTests.test_failed_duplicate_keeps_unknown_accounting_unknown)`
- `test_nested_duplicate_is_rejected_without_optional_schema (tests.ai.test_p5_t01_duplicate_output_keys.OpenAIDuplicateTests.test_nested_duplicate_is_rejected_without_optional_schema)`

## Minimal fix / preserved invariants

A per-object `object_pairs_hook` rejects repeated keys using content-free `ValueError("invalid_structured_output")`, before decoding destroys the ambiguity. The rejection intentionally propagates to the existing analyze failed-output boundary, not legacy plain-text fallback. The hook checks nested objects/objects in arrays and decoded Unicode-equivalent key names, while permitting the same key in separate objects.

The new suite exercises actual helper, three adapter and digest sources with MockTransport and module-local infrastructure doubles. Typed and schema-free analyze failures remain fail-closed; diagnostic envelopes/logs do not echo rejected content; model/dispatch identity and known/unknown token/cost evidence remain available to the existing journal. Digest rejects the candidate summary but still invokes its attempt journal and retains known cost. Valid JSON, Unicode values, successful summaries and plain-text/nonobject fallback remain unchanged.

**Bounded limit:** this closes duplicates in generated textual JSON parsed by `_try_json`. It does not recover duplicate keys already collapsed by outer provider HTTP JSON decoding or in native custom-provider dicts. This is not a universal hostile-input/injection defense.

## Exact final R10/test-runner checks

Python 3.13. Native executor; no child sessions. Executed from the clean implementation-SHA worktree. New/related unittest scopes use actual source, pure contracts/journal builders, fake storage seams and MockTransport. The pytest scope consists only of inspected pure parser parameter cases; `--noconftest` is not used for DB tests. Synthetic POSTGRES_URL/SECRET_KEY configure imports only; no target accessed.

| Exact command | Exit | Passed | Failed/errors | Skipped |
| --- | --- | --- | --- | --- |
| `PYTHONPATH=tests:. python3 -m unittest tests.ai.test_p5_t01_duplicate_output_keys -v` | 0 | 23 | 0 | 0 |
| `PYTHONPATH=tests:. python3 -m unittest tests.ai.test_p10_t06_provider_error_privacy tests.ai.test_llm_attempt_journal tests.ai.test_ai_output_boundaries.ContractTests -v` | 0 | 111 | 0 | 0 |
| `POSTGRES_URL=postgresql+asyncpg://test:test@localhost:5432/test_db SECRET_KEY=p5-t01-test PYTHONPATH=tests:. python3 -m pytest --noconftest -q tests/ai/test_llm_reliability.py::test_parser_always_returns_an_object` | 0 | 5 | 0 | 0 |
| `git diff --check` | 0 | n/a | 0 | n/a |
| `python3 -m black --check --line-length 120 tests/ai/test_p5_t01_duplicate_output_keys.py` | 0 | n/a | 0 | n/a |
| `python3 -m isort --check-only --line-length 120 tests/ai/test_p5_t01_duplicate_output_keys.py` | 0 | n/a | 0 | n/a |

Final output: `Ran 23 tests ... OK`; `Ran 111 tests ... OK`; `5 passed, 1 warning in 9.58s`. **139 passed, 0 final failures/errors/skips.** Final failure names: none. The warning is pre-existing `app/core/database.py:36` SQLAlchemy `MovedIn20Warning` for `declarative_base()`; not changed in this lane. RED failures above deliberately prove the fixed gap and are not unresolved acceptance failures. No full-suite or DB PASS claim.

## Documents actually consulted

- README.md and AGENTS.md: project hierarchy, safety, scope, ownership and READY/acceptance distinction.
- `.agents/skills/remote-task/SKILL.md`, `references/local-validation.md`, `.agents/skills/task/SKILL.md`: claim/PR/helper workflow.
- `.agents/skills/test-runner/SKILL.md`, `tests/AGENTS.md`, COLLABORATION Tasks/Evidence/Tests/Git and cleanup: focused R10 route, exact evidence and safe cleanup.
- P5-T01 ROADMAP row and task card; `docs/phases/p5-ai-boundaries.md` phase goal and closed-history context; `docs/tasks/p5-t04.md` scope/ownership to avoid the active receipt/web lane. No unrelated design docs preloaded.

## Unrun / local handoff

Real PostgreSQL persistence, live provider behavior, full suite and deployment are unrun, not PASS. Fake journal seams are not commit/persistence proof. The selected parser boundary is proven with isolated actual-source regressions; heavy DB checks may be left locally under the owner's previous instruction.

Optional existing downstream check, after following test-runner/Database policy to resolve an authorized isolated test DB/schema and sequential ownership, using normal root conftest:

```bash
python3 -m pytest -q tests/ai/test_ai_output_boundaries_db.py
```

Do not use a public/production schema, paid API/live sends or migrations. Report exact tested SHA or a verified docs-only descendant, command/exit/counts/failure names and synthetic-record cleanup in the PR. This optional downstream scope does not substitute for the adapter regression or establish native/outer JSON duplicate detection.

Handoff: https://github.com/Starck43/social-media-ai/pull/241#issuecomment-6404616062

## Registry / cleanup

Executor reports READY; the row is retained. Merge is explicitly owner-authorized, but is not owner acceptance/DONE. Owner-custodian handles acceptance, parent closeout and queue removal. No synthetic DB records, real provider/transport writes or child sessions created. Owned main/verification worktrees and temporary files are removed only after byte-for-byte publication and verified integration; unsupported remote branch deletion is reported separately.
