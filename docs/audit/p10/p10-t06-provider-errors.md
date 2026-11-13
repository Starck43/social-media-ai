# P10-T06 — safe provider errors / validation evidence

PR: https://github.com/Starck43/social-media-ai/pull/236

## Revisions and scope

- Base dev: `5d4e584d46df8c8469507aa478b1263210d80494`; first small claim commit: `0682f0c3c9744e36cbc6b0795e8aa60126188ea7`.
- Branch: `fix/p10-t06-safe-provider-errors`.
- Exact tested implementation SHA: `0128f3db166704eabd28559c3c119eee26182a00`, clean detached checkout. Final registry/card/evidence edits are docs-only.
- Production file: `app/services/ai/llm_client.py`, concrete OpenAI/Anthropic/custom analyze failure/validation branches plus two analyze-specific helpers. AST comparison against base verifies unchanged chat, billing/admission/accounting helpers and other functions; no migration or feature activation.
- New regression file: `tests/ai/test_p10_t06_provider_error_privacy.py`.
- Other writes: P10-T06 ROADMAP row, task card and this validation note only. No held P8-T29 documents, CLI, digest delivery/checkpoints, parent-phase or unrelated rows touched.

## Result / acceptance map

The common failure builder allowlists `timeout`, `http_error`, `transport_error`, `provider_request_failed`, `invalid_structured_output`. Exception mapping uses types only; logs contain a fixed message/code, never exception strings, traceback, provider body or provider/config values. Failure request metadata contains only model, model-row ID, provider, dispatch ID; no prompt/media/messages/custom endpoint or full request payload. All failure parsed output is empty (fail-closed).

Output failures retain normalized owned usage/cache/reported-cost evidence and existing legacy numeric projections; the authoritative journal still records missing evidence as unknown/undefined, not free. Same-envelope retries keep attempt keys; new calls have fresh dispatch IDs; reserved IDs survive. Existing personal outcome/cancellation hooks remain. Successful provider output is intentionally preserved rather than indiscriminately redacted. HTTP transport errors and invalid output remain distinguishable as failed_transport / failed_output in the journal.

Sixty task regressions (20 per adapter) use actual analyze source, real httpx MockTransport, actual strict validator, an intentionally input-bearing real Pydantic exception and unexpected exceptions, plus pure journal/pricing builders. Sentinels cover token, DSN, prompt and signed media URL. Cases cover HTTP/timeout/transport/unexpected/JSON-decode errors, parser/validator/provider-error output failures, known/zero/unknown/unresolved accounting, identity, successful content and cancellation. Constructor catalog/credential/admission/storage are explicit infrastructure doubles in these tests, not production bypasses; no auth/DB acceptance is claimed.

## Exact final checks

Python 3.13; native executor; no child sessions. Environment values are synthetic import-only settings; no DB target was accessed. Isolated source/fake sessions and MockTransport only. `--noconftest` is restricted to the inspected pure/mocked selected tests, not a way to disable guards on DB tests.

| Exact command | Exit | Passed | Failed/errors | Skipped |
| --- | --- | --- | --- | --- |
| `PYTHONPATH=tests:. python3 -m unittest tests.ai.test_p10_t06_provider_error_privacy -v` | 0 | 60 | 0 | 0 |
| `POSTGRES_URL=postgresql+asyncpg://test:test@localhost:5432/test_db SECRET_KEY=p10-t06-test PYTHONPATH=tests:tests/ai:. python3 -m unittest tests.ai.test_llm_attempt_journal tests.ai.test_analyzer_attempt_journal tests.ai.test_scoped_attempt_journal tests.ai.test_llm_outcome_events tests.billing.test_provider_accounting_price -v` | 0 | 121 | 0 | 0 |
| `POSTGRES_URL=postgresql+asyncpg://test:test@localhost:5432/test_db SECRET_KEY=p10-t06-test PYTHONPATH=tests:. python3 -m pytest --noconftest -q tests/ai/test_llm_reliability.py::test_clients_reject_invalid_structured_output tests/ai/test_structured_outputs.py` | 1 | 16 | 4 | 0 |
| `git diff --check` | 0 | n/a | 0 | n/a |
| `python3 -m black --check --line-length 120 tests/ai/test_p10_t06_provider_error_privacy.py` | 0 | n/a | 0 | n/a |
| `python3 -m isort --check-only --line-length 120 tests/ai/test_p10_t06_provider_error_privacy.py` | 0 | n/a | 0 | n/a |

Final output summaries: `Ran 60 tests ... OK`; `Ran 121 tests ... OK`; `4 failed, 16 passed, 1 warning in 5.92s`. Task/privacy and accounting: **181/181 PASS**. Across all selected final tests: **197 passed, 4 failed**. The four legacy failures are not claimed green. Formatting applied to owned methods only, avoiding a whole-file reformat.

### Baseline-reproduced failures (out of scope)

The exact third command above was also executed in a clean detached worktree at the base SHA, giving **exit 1, 16 passed, 4 failed, 0 skipped, 1 warning** (6.21s), with identical failures:

- `tests/ai/test_llm_reliability.py::test_clients_reject_invalid_structured_output[OpenAICompatibleClient-raw0]`
- `tests/ai/test_llm_reliability.py::test_clients_reject_invalid_structured_output[AnthropicClient-raw1]`
- `tests/ai/test_llm_reliability.py::test_clients_reject_invalid_structured_output[CustomClient-raw2]`
  - These three legacy fixtures lack an authorized catalog owner and stop at the unchanged constructor guard before analyze. No permission guard was disabled to make these fixtures pass. New privacy tests explicitly isolate that infrastructure and separately exercise the real analyze/validator source.
- `tests/ai/test_structured_outputs.py::TestBuildPydanticModel::test_model_name_contains_scenario_id`
  - Existing stale assertion expects `99` in the current class name `ScenarioOutput`. No schema/name assertions changed.

Initial preparation needed project dependencies and synthetic import settings; reruns above are the final evidence. The initial missing-settings/dependency errors are not product regressions. No broad/full-suite PASS is claimed.

## Documents actually consulted

- README.md, AGENTS.md: entry point, safety, lazy reading, ownership and READY boundary.
- `.agents/skills/remote-task/SKILL.md`, its `references/local-validation.md`, `.agents/skills/task/SKILL.md`: remote execution, PR/helper route and scope.
- `.agents/skills/test-runner/SKILL.md`, `tests/AGENTS.md`, `docs/COLLABORATION.md` Tasks/Evidence/Tests/Git and cleanup: tests, exact evidence, claim and safe cleanup.
- P10-T06 ROADMAP row/card and recovered PR #196 `docs/audits/2026-10-15-dev.md`, AUD-06: source/error/accounting contract and exclusive lane.

## Unrun / local handoff

No live HTTP/provider/paid API calls, real PostgreSQL persistence, full suite, deployment or runtime activation. These are unrun, not PASS. Heavy/DB checks may be completed by the local agent under the owner's earlier instruction. Optional existing downstream check, with normal root conftest after validating the authorized isolated test schema and sequential ownership:

```bash
python3 -m pytest -q tests/ai/test_ai_output_boundaries_db.py
```

This is downstream storage coverage, not a replacement for the adapter regressions or live-provider privacy proof. No public/production schemas, real messages, paid requests or migrations authorized. Report exact tested SHA, commands/exit/counts/failure names and synthetic-record cleanup in the PR.

Local handoff: https://github.com/Starck43/social-media-ai/pull/236#issuecomment-6401656265

## Cleanup / handoff boundary

No database accessed, synthetic records created, real transports/providers called or child sessions started. No secrets/generated artifacts committed. Owned main/baseline worktrees are inventoried and removed only after verified merge/equivalent patch preservation. Unsupported remote branch deletion is reported separately. ROADMAP row stays READY; acceptance, row removal and parent-phase closeout belong to owner-custodian, not executor.
