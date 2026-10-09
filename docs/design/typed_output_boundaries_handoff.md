# Typed digest and learned-memory boundaries: owner handoff

Status: IMPLEMENTATION PREPARED, NOT MERGED OR DEPLOYED. No production gate is
closed by this unit. Branch: `ai/typed-digest-memory-boundaries`, based on dev
`1d26c1bfef7f02e6de8773e9761191e13d040bc2` (2026-10-09).

The owner's `app/agent/runtime.py` prompt-injection guard is preserved unchanged.
It arrived in owner commit `1d26c1b`, not in the earlier fixture-only PR #16.
A regex guard is defense in depth, not security acceptance. The user reports
restoring the original tenant-channel constraint/migration; no database was
inspected or mutated here.

## Bounded implementation

- `DigestSummary`: one required nonempty string, at most 2000 characters, no
  unchecked `analysis` alias. Provider errors/truncation do not become narrative.
  Invalid narrative falls back to the existing aggregate-only rendering path.
- `ExtractedFact` / `LearnedFacts`: at most 8 facts, snake_case key <=100,
  nonempty value <=500, finite numeric confidence 0..1 and positive integer
  evidence ID. Reject coercion, duplicate keys and unknown fields.
- `ReflectionResult`: at most 16 discriminated delete/update operations; bounded
  values/reasons/advice, one operation per ID. Updates require value/confidence.
- New write boundaries parse only an entire JSON object or one whole JSON fence.
  Reject surrounding prose, duplicate JSON keys, nonfinite constants and text
  above 16000 characters. Existing tolerant extract_json/clamp helpers remain
  for compatibility but are not used by learn/reflect writes.
- Learning accepts evidence only from user messages actually rendered into the
  prompt, not assistant, foreign or omitted messages. Validate the entire batch
  before the first write; invalid output does not advance the watermark.
- A successful learn run advances only through rendered rows. It no longer
  consumes the unrendered tail when the transcript character budget is reached.
- Reflection validates every ID against the tenant-scoped input snapshot before
  applying any operation; one foreign/invalid operation rejects the whole batch.
  `dedup=False` remains read-only. Advice is returned, never applied to prompts.
- Existing text-framing helper now also surrounds digest briefs, transcripts
  and memory/feedback JSON as untrusted data. Shapes/evidence IDs do not prove
  truth, and framing does not grant or replace server-side authorization.
- These callers log/return content-free error codes on provider/validation
  failure. Known finite reported usage cost survives invalid output; absent or
  malformed cost remains None. An explicit reported zero is preserved.
- Known truncated/filter/tool-call endings are rejected even when the remaining
  JSON happens to be syntactically valid. No automatic extra LLM validation retry.

No migration, new provider field, embedding service, external trace exporter,
model-probe job, live publisher activation or interactive account router is
introduced. Existing economical/quality model resolution is reused. Main
analysis validation was already present and was not rebuilt.

## Actual verification

**44 tests PASSED** via `python tests/test_ai_output_boundaries.py` using
Pydantic 2.13.5. They include strict contracts and actual learning/builder source
loaded with mocked infrastructure imports (not AST reimplementations):

- type/length/extra-field/duplicate/nonfinite/JSON-fence rejection;
- foreign/assistant/unrendered evidence and operation IDs;
- no writes/watermark on invalid batches and retained known cost;
- read-only reflection and advice-only behavior;
- bounded transcript watermark, input-boundary escaping;
- redacted local errors, provider failures/truncation and budget gate.

Compilation, AST and whitespace checks also run locally. The unchanged prompt
sanitizer copy used for tests matches dev. No provider, HTTP or DB calls ran.

**5 PostgreSQL regressions PREPARED, NOT EXECUTED** in
`tests/test_ai_output_boundaries_db.py`: real persistence/watermark rejection,
cross-tenant evidence, valid owned evidence, whole-batch foreign reflection
rejection and read-only reflection. Pytest, httpx, SQLAlchemy, asyncpg and
PostgreSQL are unavailable in this sandbox. Do not claim the full suite passed.

## Owner test sequence

Run from the repository root in the normal project environment. Pytest must
use the dedicated disposable `TEST_POSTGRES_URL` / `DB_TEST_SCHEMA` configured
by `tests/conftest.py`, never the working or production database.

```bash
python tests/test_ai_output_boundaries.py
python -m pytest -q tests/test_ai_output_boundaries.py tests/test_ai_output_boundaries_db.py tests/test_learning.py
python -m pytest -q tests/test_digest_builder.py tests/test_digest_job_delivery.py tests/test_digest_snapshot_factory.py tests/test_digest_delivery_outcomes.py
python -m pytest -q
```

Keep checkpoint delivery OFF until its separate staging acceptance. Do not
change the channel uniqueness rule to make fixtures pass. Capture the exact
head, test output and migration head for handoff; redact credentials.

## Limits and remaining decisions

- This is not provider-native json_schema support. Local validation works across
  the existing protocols without guessing model capabilities. Digest clients
  have already parsed their reply, so duplicate raw JSON keys lost upstream are
  not detected at that boundary; learn/reflect textual replies do reject them.
- Well-shaped but malicious or factually incorrect memory can still be returned.
  Complete identity/permission and memory-in-prompt defense remain PRD-02/07.
- Memory writes remain individual manager transactions, not an atomic batch.
  A DB failure after some valid writes can leave partial persistence. Concurrent
  learn/reflect jobs, snapshot drift and watermark serialization need another
  reviewed transaction/lease unit. Validation failure itself performs no writes.
- Invalid-output results retain reported cost, but a persistence failure can
  still lose attempt cost. Shared clients may label unknown tariffs as zero and
  upstream logs may include raw errors. This is not a complete spend ledger or
  process-wide redaction audit; PRD-03/06/08 remain open.
- Generic dispatcher semantics for returned `status=failed` remain open under
  PRD-05; a boundary's failure dictionary is not proof of a failed Job UI state.
- Native output-protocol coverage, unsupported scenario schemas, multimodal
  metadata, acceptance quality and poisoned-memory tests remain broader PRD-07.
- Langfuse, billing, embeddings and automatic global model disabling are not
  silently bundled into this change. The historical research is not the active
  launch backlog; use proposal dispositions and production gates.

Prepared code, merged code, target migrations and activation are different
states. Commit messages include an owner handoff; merge/activation require
separate approval. No production data, secrets or live messenger were changed.
