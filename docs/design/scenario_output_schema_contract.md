# Scenario output schema contract

## Scope and status

One bounded PRD-07 implementation: scenario configuration validation and shared
structured-output validation. Implementation is ready for bounded code integration after the user explicitly
lifted execution deferral. Scoped standalone verification below is not database/
SQLAdmin/provider or full-gate acceptance. Source review alone is not acceptance.
This does not close quality/evidence, every-call accounting or the full PRD-07 gate.
No schema migration, database, provider, deployment or historical repair is included.

## Explicit supported subset

The root is an object JSON Schema. Supported JSON types are object, array,
string, integer, number, boolean and null, including declared type unions.
Supported assertions: scalar enum, properties, declared required fields, boolean
additionalProperties, homogeneous items, minimum/maximum and exclusive numeric
bounds, min/maxLength, pattern and min/maxItems. Nested objects/arrays use the
same contract. Reserved/private Python names retain their exact JSON aliases.
Omitted optional properties remain omitted; validation never invents null values.

Annotations title, description, default, examples and the supported $schema
labels are retained; default is annotation, not value insertion. The supported
labels are draft-07 and 2020-12, but neither label enables other features.
Pattern support follows the installed Pydantic regex engine, not arbitrary
JavaScript/PCRE features. Unsupported patterns fail compilation.

References, composition, format, boolean schema nodes, tuple items, typed
additionalProperties and unknown assertions/types are rejected rather than
silently mapped to Any. An explicitly unconstrained child schema remains Any.
Root type must be object; required keys must be declared properties. Schemas
are bounded to 64 KiB UTF-8, depth 16, 2048 nodes and 256 properties/enum entries
per node. Non-JSON values, nonfinite numbers, duplicate enums, inconsistent bounds
and invalid configuration/compilation fail with static error codes.

## Invocation snapshot and runtime boundaries

A scenario's custom output_schema is copied and compiled once after dedup and
scenario resolution, before model selection or provider requests. None/empty
custom schemas derive from existing analysis_types/scope without mutating the
shared ORM object. No scenario retains a cached mutable validator. The detached
schema snapshot and model are reused for text/image/video and saving, including
the audit's output_schema. Concurrent invocations receive separate contracts.
Absent scenario retains the generic analysis path.

Private modality entrypoints compile before selecting a model when no invocation
contract is supplied. Direct saving validates every nonempty semantic component
and requires at least one validated component, before tracing/pricing/writes.
Unsupported schemas never become untyped requests. Invalid returned parsed
content becomes a static failure envelope while request/usage fields survive;
valid siblings may still save under existing partial-coverage rules. No caller
parsed dictionary or stored scenario schema is changed during validation.

The relevance-filter and existing daily-row partial-A collision guard remain
before save validation/pricing/tracing; a rejected or colliding result does not
write. Sampling, media gaps, UNKNOWN attachments (#98), hash retirement,
cancellation and tenant permissions retain their existing contracts. Unified
summary has its own existing aggregation schema, not the scenario component
schema. Failed-attempt envelopes are not a complete durable paid-attempt ledger;
B usage loss on a daily-row collision remains the accounting gate's open issue.

## Configuration saving

AgentScenario flush hooks validate creation, schema/configuration changes and
reactivation, including synchronous SQLAdmin ORM flushes. Session bulk hooks
inspect literal scenario INSERT/UPDATE values; schema-changing UPDATE validates
prospective values under the same predicates/transaction with row locks. Query
projection avoids trusting stale mapped identities. Noninspectable expressions,
INSERT FROM SELECT/multi-values and schema-changing executemany updates are
rejected. No listener commits or expands a tenant predicate/permission.

Metadata-only updates and literal disabling of invalid legacy rows may proceed;
reactivation/configuration changes must validate. Nonboolean activation values
cannot bypass validation. Forms/tools that resubmit configuration must validate
that prospective configuration even if another field is their apparent change.
The REST boundary returns 422 on schema errors; explicit output_schema=null
clears the custom schema while omission preserves it. Web/tool preflights run
before default-flag changes. Cloning deep-copies the custom schema, not only its
analysis settings. Tool writable-field permissions are not expanded.

Raw engine/text SQL is outside these application ORM/session write paths; this
is not a database trigger or a guarantee against arbitrary privileged SQL.
Real ORM/SQLAdmin transaction integration remains to be verified separately;
statement/event doubles do not certify actual database concurrency or durability.

## Deferred verification

New standalone files: test_scenario_output_contract.py (33 prepared methods,
real Pydantic plus module-local analyzer/provider doubles) and
test_scenario_schema_persistence.py (13 prepared methods, real SQLAlchemy
statement construction plus event/session doubles, no engine/connection).
Existing schema-warning privacy checks (16) are aligned to fail-closed rejection,
success, cancellation, safe categories and save rejection, NOT the former
unvalidated fallback. Shared analyzer fixture imports are aligned only; prior
green evidence does not transfer to changed semantics. Preparation executed no
checks; the subsequent user-authorized verification is recorded below.

Only Python AST parsing, targeted source review, whitespace and documentation
link/anchor QA are performed during preparation. Do not run pytest/bootstrap,
old suites or full-suite checks by default. Owner-controlled execution is a later
phase; database fixture commits or schema operations require separate approval.

## Completed standalone verification

User lifted test deferral. Successor author observed on original `ebe576c3`:
22 compiler cases passed; 11 analyzer cases failed during fixture setup, before
assertions (MediaType namespace instance used in a type union). Persistence
statement/event suite13/13 and aligned privacy suite16/16 passed. Only that
NEW fixture type was corrected to a class; all33 test method/assertion ASTs
remain unchanged. Then the 11 formerly blocked analyzer cases passed on
`ebe576c3` + exact fixture patch. Total62 effective scoped passing checks, not
a single final-head/full-suite/DB run. Commands were the three documented
standalone Python entrypoints, followed only by
`python tests/test_scenario_output_contract.py AnalyzerContractTests`.

Pydantic2.13.5 and SQLAlchemy2.0.54 were used with local import doubles, no app
bootstrap, connection, provider, schema action or historical-suite discovery.
Final follow-up is one fixture plus three existing docs; eight production files
retain the original artifact. #101 source/evidence is preserved by three-way
integration with fresh dev. Real ORM/SQLAdmin transactions, production quality
and broader PRD-07 acceptance remain OPEN. No local test task or repeat needed
for this fixture/docs-only closeout; final heads/checks/source readback precede
routine merge.
