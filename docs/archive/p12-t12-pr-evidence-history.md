# P12-T12 — historical PR evidence

Historical checkpoints only; newer source and evidence are in PR #327. Old results do not certify later revisions.

# Pure private payload v1 checkpoint — 2026-11-03

Owner-authorized continuation in this [dialogue](https://app.notion.com/chat?t=40d7810652ab807f8d8700a9607045ac).
Existing draft [PR #327](https://github.com/Starck43/social-media-ai/pull/327),
branch `feat/p12-t12-file-workspace`. Specification `a4c7cda8`; implementation
`b11cdb70`; tests and **exact published tested source
`f555f53415f26f2086a7a6e65433ed786dd1159b`**.

Implemented `serialize_private_payload` / `private_payload_is_canonical` in
`app/integrations/file_workspace/private_payload.py`. Closed schema v1, exact
built-in types/no unknown fields, explicit owner/tenant/connection/object and
root/path keys, required bounded version/semantics assertion, optional stable
provider identity, separate hashes/parser, attempt/consent/policy/epoch fields,
fixed six-digit UTC timestamps and non-sliding capture-relative deadlines.
Preserves quote/candidate budgets and existing **1-based** citation indices.
No context/asdict dumping. All field/count bounds precede encoding; sorted keys,
compact JSON, ensure_ascii=False, strict UTF-8, <=65536 complete envelope bytes;
charge len(bytes) outside the envelope. Canonical-byte validation rejects invalid
UTF-8/JSON, duplicates, nonfinite values, unknown revisions and alternate encodings.

FORMAT ONLY: values can be forged; historical expired data can be format-valid.
No proof of source consistency, exact-version provenance, current rights/consent/
epochs, fresh time/high-water mark, truth/entailment or storage integrity. No
public DTO admission, ORM/session/migration, reservation/idempotency/fence/GC,
provider/model, route/UI or activation. Fields/limits remain proposals.
[DTO contract](https://github.com/Starck43/social-media-ai/blob/feat/p12-t12-file-workspace/docs/FILE_WORKSPACE_PRIVATE_PAYLOAD.md)
and lifecycle explicitly preserve these boundaries. Storage stays OFF.

### Exact verification and local handoff

Python **3.14.10**, dedicated sandbox venv, remote no-database exception:

```sh
PYTHONPATH=. /data/p12-t12-venv/bin/python -m pytest \
  tests/unit_services/test_p12_t12_private_payload.py \
  tests/unit_services/test_p12_t12_retention_quota.py \
  tests/unit_services/test_p12_t12_retention_time.py \
  tests/unit_services/test_p12_t12_insight_candidates.py \
  tests/unit_services/test_p12_t12_line_evidence.py \
  tests/unit_services/test_p12_t12_plain_text.py \
  tests/unit_services/test_p12_t12_plain_text_read.py \
  --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header
```

On clean exact published source above: **exit 0, 947 passed, 0 failed, 0 skipped**
(1.14s), including **287 new** DTO cases + preceding 660. First new-only run:
287 passed (0.24s); no failed run or weakened assertion. Three known unknown
asyncio config warnings in the minimal venv without pytest-asyncio. Explicit
synthetic read/quotes/candidates -> allowlist projection tested, excluding
unselected text/context/path/credential metadata. Reordered keys, Unicode/UTF-8,
field/range/reference/time/size boundaries, schema tampering, nested extras,
subclasses, malformed/deep/duplicate JSON, no logging and provenance/currentness
counterexamples covered. The envelope-size fence is tested at a lowered bound:
current field budgets bind before 64KiB; exact 64KiB arithmetic remains covered
by the preceding quota tests. No claimed maximum-size valid DTO is fabricated.

Black --check and isort --check-only (black profile, line120) on both new Python
files: exit0. AST, published 045c4837..f555f534 diff/whitespace and DTO/lifecycle
relative link targets: PASS. No root conftest, DB integration, migration, real
provider/model/network, ASGI/browser, broad suite or erasure/concurrency checks.

Old computer state did not persist; rebuilt dedicated checkout/venv and private
PostgreSQL16 cluster on loopback:55432, database/schema `p12_t12_test`. No app
DB settings were wired and no test touched it. Schema relation count 0 before/
after the pure run; no persistent synthetic fixtures created. PostgreSQL stopped
cleanly; empty schema/data directory retained for future isolated work. No public
migration or production/user data access. This is setup evidence, NOT DB-test
or full schema-inventory/migration certification.

Claim published BEFORE solution edits at dev `dc898553f21c8daa72aade8f75cd22b2f13b17f1`,
starting dev `357a2160`. Synchronized fresh dev before publication:
`979adf40223b74648d1dfabfceb8f684ac0e0db4`; only other P12-T14 card changed and
was preserved. Own ROADMAP row remains IN_PROGRESS with current dialogue.
Only owned registry/card facts go to dev; implementation/tests/contracts remain
in #327. No merge/rebase/deploy/provider activation. Draft, not READY.

Read: remote-task/test-runner skills; README, AGENTS, DESIGN; COLLABORATION
Tasks/State machine/Evidence/Tests/Database/Git and cleanup; tests/AGENTS;
DOCS_INDEX (no specific file-workspace entry); current P12-T12 card and exact
storage/read/evidence/candidate/timing/quota contracts and relevant types/call
sites; current #327/#313 state and integration comments. P8-T34's
[answer](https://github.com/Starck43/social-media-ai/pull/327#issuecomment-6602599221)
still denies required exact-byte version support. No new transport integration
or agreement was needed/claimed; P12-T13 approval is still pending.

Local OpenCode handoff: use the exact source and command above (replace only the
venv path). For this pure slice require identical counts/format checks and zero
DB/provider/model access. Future persistence needs separately scoped test-schema
manager/transaction/fence/quota concurrency and migration work, plus owner-local
real-condition acceptance/audit. Next safe work: trusted same-read projection/
admission contract before actual persistence; no storage enablement inferred.
Dedicated task checkout/venv and unmerged task branch retained for continuation;
no new branch/worktree to delete. Own scratch is removed after durable handoff;
foreign work is untouched. Parent remains incomplete.


---

## Pure retention quota checkpoint — 2026-11-03

Owner selected another short functional slice in the current
[dialogue](https://app.notion.com/chat?t=40dc1b436029807fa62200a976b5dc4d).
Helper + synthetic tests/exact tested source:
**`49fec5eccde768e1280a3e4f28644319d9f34b58`**.

Implemented `retention_quota_allows` in
app/integrations/file_workspace/retention_quota.py: ONE new unreserved envelope,
nonempty exact bytes, strict UTF-8, <=65536 bytes, object <=3 records;
connection <=100/4MiB, owner within tenant <=200/8MiB, tenant <=1000/32MiB,
including existing reservations. Exact nonnegative bounded integers (no bool),
coherent record/byte pairs and child <= parent snapshot dimensions. Expired/
invalidated retained rows count until purge. No silent eviction or truncation.
Frozen/slotted/repr-hidden usage/snapshot; malformed inputs return False.
Limits remain proposals, not approved policy or deployed entitlements.

True is CAPACITY ARITHMETIC ONLY, not a payload-schema/owner/rights/current-usage
proof or a reservation. The caller must supply the complete canonical serialized
DTO and current correctly scoped usage; arbitrary valid UTF-8 is not certified
as a storage DTO. No mutation, retry settlement, reservation/release or global
content dedup. Tests explicitly demonstrate two calls with the same stale
snapshot can both fit: future DB atomicity remains necessary. Storage stays OFF.

Python3.14.10, isolated sandbox venv, remote no-DB exception:

```sh
PYTHONPATH=. /data/p12-t12-venv/bin/python -m pytest \
  tests/unit_services/test_p12_t12_retention_quota.py \
  tests/unit_services/test_p12_t12_retention_time.py \
  tests/unit_services/test_p12_t12_insight_candidates.py \
  tests/unit_services/test_p12_t12_line_evidence.py \
  tests/unit_services/test_p12_t12_plain_text.py \
  tests/unit_services/test_p12_t12_plain_text_read.py \
  --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header
```

Exact published source: exit0, **660 passed, 0 failed, 0 skipped**, 1.04s;
**128 new** quota cases + previous 532. Three known unknown asyncio config
warnings without pytest-asyncio. New-only first and strengthened rerun: 128 PASS;
no initial failure or weakened assertion. Black/isort (black profile, line120),
AST, published diff/whitespace and added doc link targets PASS. Tested UTF-8
multibyte exact/plus-one, every aggregate count/byte cap, reserves, malformed/
subclass/negative/huge counters, hierarchy, immutability and no payload logging.
No root conftest, DB/schema/migration, provider/model/network, UI/ASGI, full suite,
operational GC/backup or durable concurrency checks. No persistent fixtures
created; no DB cleanup needed. These greens do not certify persistence.

Claim published BEFORE code at dev `3cb349c53ddddc314990804f701915d011f0eed2`.
Fresh dev `f4bce32ba07938858158488bca58a26f1f149ca8` includes another owner's
P12-T14 card update; preserved without copying/merging it. Own row/card still
IN_PROGRESS. Only task-card facts to dev, helper/tests/contract to draft #327.
Dedicated clean checkout/venv and unmerged/foreign branches retained; own scratch
removed after durable evidence publication. No merge/rebase/deploy/public
migration, real provider/model or external write. Existing divergence retained.

Remaining: versioned allowlist DTO serialization/validation, actual scoped ORM
reservations/idempotency/fences/GC, fresh authority and consent, exact-byte
provider-version support and real AI routing/usage are separate slices. P8-T34's
[answer](https://github.com/Starck43/social-media-ai/pull/327#issuecomment-6602599221)
remains explicit that the needed version assertion is absent. P12-T13 review,
real-condition/local acceptance and audit remain later; parent is not complete.
Keep IN_PROGRESS / Draft, not READY. No storage activation by this helper.

---

## Pure retention timing checkpoint — 2026-11-03

Owner requested the smallest functional slice in the current
[dialogue](https://app.notion.com/chat?t=40dc1b436029807fa62200a976b5dc4d).
Code `d575e52361c24fde8f3d9b8d4c28d6765cfe9fa4`; tests/exact tested source
**`22360e681e5edb5ed0f5018bf788f40b73724513`**.

Implemented `make_retention_deadline` / `retention_time_eligible` in
app/integrations/file_workspace/retention_time.py. Pure explicit-UTC timing:
capture age 0..5 minutes inclusive; expiry fixed at capture +24h, never save/read
+24h; exact expiry denies. Requires a supplied trusted clock high-water mark;
rollback denies. Rejects malformed/non-UTC/naive/subclass timestamps, changed
expiry and overflow. Frozen/slotted/repr-hidden deadline, sanitized creation
failure. The proposal defaults are NOT approved/deployed policy.

This is time-only functionality, NOT ownership/consent/version authorization,
idempotency, quota, GC, persistence or durable clock rollback protection. A real
caller must atomically preserve its high-water mark even on denial; a stale
supplied mark cannot be detected by a pure function. Tests demonstrate both the
synthetic mark update and this trust limitation. Fixed-format helper does not
implement shortening under a new policy revision. Storage stays OFF.

Python 3.14.10, isolated sandbox venv, remote no-DB exception:

```sh
PYTHONPATH=. /data/p12-t12-venv/bin/python -m pytest \
  tests/unit_services/test_p12_t12_retention_time.py \
  tests/unit_services/test_p12_t12_insight_candidates.py \
  tests/unit_services/test_p12_t12_line_evidence.py \
  tests/unit_services/test_p12_t12_plain_text.py \
  tests/unit_services/test_p12_t12_plain_text_read.py \
  --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header
```

Exact published source: exit 0, **532 passed, 0 failed, 0 skipped**, 1.02s;
**109 new** timing cases + preceding 423. Three known unknown asyncio config
warnings in the minimal venv without pytest-asyncio. New-only first run 109 PASS,
local combined run 532 PASS; no initial failure or weakened expectation.
Black/isort (black profile, line120), AST, published diff/whitespace and new
contract relative link targets PASS. No root conftest/DB/schema/migration,
provider/model/network, UI/ASGI, operational GC/backup or full suite; no persistent
fixtures generated/cleanup needed. These greens do not certify real storage.

Claim published BEFORE code at dev `9b9ad4fb30c33bf59744f0a4eb185c20fa393862`.
Fresh concurrent dev changes are preserved; own row/card remain IN_PROGRESS.
Only task-card facts go to dev, code/tests/contract to the same draft #327.
No merge/rebase/deploy/public migration/external write. Dedicated clean checkout,
isolated venv and unmerged/foreign branches retained; own scratch report removed
after durable publication. Existing dev/task divergence is not reconciled by merge.

The [P8-T34 reply](https://github.com/Starck43/social-media-ai/pull/327#issuecomment-6602599221)
is now received: exact-byte version and reviewed content-bound freshness are NOT
provided; do not keep asking for the same answer or infer permission to save.
Further exact-read/version support is a separate provider extension. P12-T13
storage/processing review and owner-local real-condition acceptance remain later.
Next small independent code slice: bounded pure storage payload/byte-quota
validation, still without persistence or model/provider activation. Parent stays
IN_PROGRESS / PR Draft, not READY or complete.

---

## Draft private storage lifecycle checkpoint — 2026-11-03

Owner continuation: https://app.notion.com/chat?t=40dc1b436029807fa62200a976b5dc4d

Documentation-only contract source: **`eb92fb48ed9a005449beaa91736c41f3a483070c`**.
[Draft lifecycle](https://github.com/Starck43/social-media-ai/blob/eb92fb48ed9a005449beaa91736c41f3a483070c/docs/FILE_WORKSPACE_PRIVATE_STORAGE.md).
Only the new contract and its candidate-contract handoff link changed in that
commit. No Python/test/ORM/schema/migration/runtime/configuration change.

The draft proposes personal-owner/version binding, selected quotes/unverified
candidates only, separate retention consent and fresh access checks, atomic
multidimensional quotas/idempotency, absolute TTL, invalidation/delete fences,
bounded GC and honest pin/backup/restore boundaries. Unknown/unreviewed exact-byte
provider version denies durable save; content hashes/metadata are not versions.
No private-file projection into workspace KnowledgeSnippet/DocumentRevision.
Numeric defaults and serialization/clock/fence/physical-erasure details remain
review gates, NOT accepted or deployed policy. Storage remains OFF.

P12-T13 [review handoff](https://github.com/Starck43/social-media-ai/pull/327#issuecomment-6602399402)
and P8-T34 [exact-version/freshness request](https://github.com/Starck43/social-media-ai/pull/313#issuecomment-6602427803)
were posted. Neither request constitutes agreement/approval. No accepted adapter
revision is selected; no upstream transport code is copied or activated.

Static checks on the published contract commit:

```sh
git diff --check 0d3638066f3fe5a05a4a56a4e152e19cecb244c5 eb92fb48ed9a005449beaa91736c41f3a483070c
git diff --name-only 0d3638066f3fe5a05a4a56a4e152e19cecb244c5 eb92fb48ed9a005449beaa91736c41f3a483070c
```

PASS: whitespace/diff; published paths are exactly two docs/*.md files. Local
UTF-8/fence/relative-link/heading-anchor validation checked 16 local links in the
new lifecycle and candidate contract, all PASS. Fetched the published commit and
compared the local staged tree byte-for-byte before detaching at that exact head;
working tree was clean. `git diff --no-index --check /dev/null <new-doc>` initially
returned 1 for the new-file difference without any whitespace diagnostics; the
subsequent staged and exact-commit diff checks both returned 0.

No pytest, DB/ACL/concurrency, migrations, provider/account/model, ASGI/browser,
operational GC/backup/erasure or full suite ran for this documentation-only slice.
Earlier **423 passed** applies only to mock-candidate source `16f89e55`, not this
storage proposal. The contract's acceptance matrix is FUTURE work, not executed
or green. Owner-local real-condition verification/audit remain later.

Read: existing read/quote/candidate contracts; connection owner model, private
catalog models/manager, workspace-owned knowledge models; current dev P12-T13
card, TENANCY retention/authority declarations and MODELS accounting/receipt
retention sections. Reused unchanged AGENTS/remote-task/COLLABORATION rules.
No unseen planning-branch approval is inferred.

Scope registered in dev **`33889609d535d1fbb7389e77f1427f45aac8e99f`** BEFORE
contract edits, from synchronized dev `137d5990`; current ROADMAP owner/dialogue/
IN_PROGRESS already matched, so no unrelated row edit. Only task-card status
facts go to dev; the solution contract stays in draft #327. Existing divergent
histories/task-card conflict are not merged/rebased. Dedicated checkout/venv and
unmerged local/foreign branches are retained; own slice scratch files removed
at closeout. No merge, deploy, public migration, real call or external file write.


Immediately before handoff, dev advanced concurrently to `bb1e088513180059f0178f9e022e4954812f2758`
with P8-T34 audit fixes. Its fresh row is now READY; this supersedes the older
NOT ACCEPTED status as a current queue statement, not its historical audit.
Those reported fixes/tests are not independently verified by this docs slice;
READY does not establish P12-T13 approval or exact-version/freshness agreement.
No P8 code is merged/cherry-picked into #327. Our dev card/ownership are unchanged
by that update; the scoped card publication is reconciled against this fresh base.

Next: obtain P12-T13 decisions and P8-T34 exact-version/freshness agreement before
concrete storage/adapter activation. A separate synthetic lifecycle-policy helper
can model approved/provisional rules explicitly, but cannot certify DB atomicity,
real ACL, physical erasure or backup restore. Keep **IN_PROGRESS / Draft**, not
READY or parent complete; real model/usage/native-open, approved writes, paired
local execution and owner-local audit are still unfinished.

---

## Mock insight candidates checkpoint — 2026-11-03

Owner continuation: https://app.notion.com/chat?t=40dc1b436029807fa62200a976b5dc4d

Separate slices: `8fbdcda9` inactive contracts/orchestration, `16f89e55` tests.
Exact tested published source: **`16f89e55bf279941cc197c6e40a28b9a0b0112e0`**.
[Contract](https://github.com/Starck43/social-media-ai/blob/feat/p12-t12-file-workspace/docs/FILE_WORKSPACE_INSIGHT_CANDIDATES.md).

`generate_insight_candidates` verifies same-read line evidence, freshly checks
the injected read/processing-authority assertion, sends only indexed selected
excerpts to an injected SYNTHETIC generator, validates its generation-bound typed
response and rechecks authority/context before returning. No default authority/
generator, real model, provider/client, tools, cache, durable insight, route or UI.
No concrete SQL/policy implementation or paid token/cost path is certified here.

Typed batches are fixed **unverified**. A valid citation is NOT entailment, truth
or relevance: an explicit unsupported-claim test demonstrates this limitation.
Unknown provider version stays unknown in bound evidence and is not sent to the
generator. Generator input omits full source text, path/name, actor/tenant/object/
connection IDs, hashes, credential fingerprints and provider version, but selected
text can itself be sensitive. This is minimization, NOT redaction/model consent.
Both source excerpts and generated prompts/markup/URLs remain untrusted inert data.

Provisional bounds: 0–5 candidates, 600 code points per candidate, 1800 total,
1–4 sorted unique exact integer excerpt references per candidate, fresh generation
nonce, one 5s cooperative end-to-end budget including both authority checks.
Zero candidates is unverified synthetic abstention, not certified file coverage.
Reject malformed/foreign/replayed/oversized results as a whole; no truncation,
retry or empty success fallback. Dataclasses are frozen/slotted/repr-hidden;
ordinary failures have one sanitized code and no chained generator/source details.
Cancellation/late results fail closed, without claiming preemption or malicious-
adapter sandboxing. READ/cloud processing alone does not authorize a real model
or spend; later wiring must reuse approved AI admission/usage/routing contracts.

Python **3.14.10**, minimal isolated sandbox venv, remote no-DB exception:

```sh
PYTHONPATH=. /data/p12-t12-venv/bin/python -m pytest \
  tests/unit_services/test_p12_t12_insight_candidates.py \
  tests/unit_services/test_p12_t12_line_evidence.py \
  tests/unit_services/test_p12_t12_plain_text.py \
  tests/unit_services/test_p12_t12_plain_text_read.py \
  --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header
```

Exact published source: exit 0, **423 passed, 0 failed, 0 skipped**, 0.91s;
**105 new cases** plus preceding 318. Three known unknown asyncio-config warnings
because pytest-asyncio is absent; async scenarios use asyncio.run. First local run
had 411 passed/1 failed: the immutability test expected ValueError, but Python3.14
raises TypeError when dataclasses.replace sets an init=False field. Corrected only
that test expectation; no production restriction changed. Eleven additional edge/
full-chain tests brought the local and exact published reruns to 423 passed.

Black/isort (black profile, line120), AST syntax on five affected Python files,
new contract relative links and git diff-check PASS. Covers full injected read →
quote → candidate chain, input minimization, context/revoke/malformed authority,
reference/character/count bounds, generation replay, empty response, unverified
unsupported claim, errors, timeout/cancellation/overruns and repr/immutability.
Shared doubles remain tests/shared/file_content_read.py and file_insight_candidates.py.
No app.services.ai, concrete provider, settings, DB/cache imports from new service;
actual AI package initializers were inspected, NOT imported into isolated tests.
No root conftest, DB/migration, actual provider/account/model, ASGI/browser or full
suite. Real ACL/model/quality/usage/billing and provider integration remain unrun,
not green. Prior DB/UI checks certify only their historical source revisions.

Scope registered in dev `c1d6c5fe3e5a56e27923093eb5ec2abdc883393d` BEFORE code.
Fresh dev/task refs fetched and local/published trees compared before rerun.
Only current task-card facts go to dev; code/tests/contracts stay in draft #327.
ROADMAP already has current owner/dialogue/IN_PROGRESS. No unrelated row edits,
merge/rebase, force-push, deploy or public migration. Clean dedicated checkout/venv
and unmerged local publication-equivalent branch are retained; foreign work intact.

Read additionally: current dev/card/typed read and quote contracts/services,
shared doubles, AI_PIPELINE_AND_PROMPTS Concepts/Analysis flow/Prompt assembly,
and the AI package initializer/dependency boundary. Existing approved real AI
runtime is NOT bypassed/activated by this mock-only protocol. Unchanged repo,
remote-task and test-runner rules reused. P8-T34 real-read/native-link and P12-T13
capability/processing/model/budget/retention review gates remain unresolved.

Next safe slice: draft/review the PRIVATE owner/version-bound storage lifecycle
contract (quotas, TTL/GC, read/release authorization, invalidation/pin/backup
semantics) with P12-T13 before any persistence/migration. Do not promote unverified
candidates to accepted knowledge or workspace sharing. Concrete authority/reader,
real model integration/quality checks, original-open, approved writes, paired local
execution, performance/storage corpus and owner-local real-condition audit remain
unfinished. Local reproduction: exact SHA + command above; no DB/model required.
Keep **IN_PROGRESS / draft**, not READY or parent complete. No merge/deploy/public
migration, real account/model activation or external file mutation.

---

## Source-bound line evidence checkpoint — 2026-11-03

Owner continuation: https://app.notion.com/chat?t=40dc1b436029807fa62200a976b5dc4d

Separate PR slices: `415e7447` pure helper/contract, `4142d564` synthetic tests.
Exact tested published source: **`4142d56404b358100427919ca6b3842adbabadd5`**.
[Contract](https://github.com/Starck43/social-media-ai/blob/feat/p12-t12-file-workspace/docs/FILE_WORKSPACE_LINE_EVIDENCE.md).

`build_line_evidence` selects exact 1-based inclusive canonical-LF ranges from an
internal completed ReadPlainText. Require sorted/disjoint typed ranges; preserve
whitespace/Unicode and trailing empty lines, no strip/merge/truncation. Proposed
bounds: 8 ranges, 32 selected lines, 8192 total code points including internal LF.
Empty source has zero addressable lines. All quotes remain inert untrusted text.

Evidence binds full context/request nonce, raw/text hashes, parser, canonical line
count and optional version assertion. `verify_line_evidence` rebuilds exact quotes
and checks the same read. Different read/owner/nonce, mismatched quotes/context/
hashes/version fail. Equal text hashes for CRLF/LF do not erase raw differences.
Frozen slotted outputs hide every field from default repr; sanitized build errors
have no chained source/decoder details. No logs, model, storage, URL, route or UI.

This is **internal consistency, not authentication or authorization**. No raw bytes
are available to recompute the raw hash. Trusted code can forge/replace both
source and evidence or select other valid ranges; there is no signature, approved
selection, current-permission or truth/relevance guarantee. Tests explicitly cover
that limitation. No KnowledgeSnippet projection, implicit sharing, actual insight,
provider version inference or external release mechanism is added. Future external
use/persistence still needs fresh authority and processing/retention review.

Python **3.14.10**, isolated sandbox venv, remote no-DB exception:

```sh
PYTHONPATH=. /data/p12-t12-venv/bin/python -m pytest \
  tests/unit_services/test_p12_t12_line_evidence.py \
  tests/unit_services/test_p12_t12_plain_text.py \
  tests/unit_services/test_p12_t12_plain_text_read.py \
  --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header
```

Exact published source: exit 0, **318 passed, 0 failed, 0 skipped**, 0.64s;
**97 new cases** plus the previous 221 helper/read cases. Three known minimal-venv
asyncio-configuration warnings (no pytest-asyncio); synthetic async scenarios use
asyncio.run. First local scope passed 316; two added raw-vs-canonical/last-line
cases passed 318; published-tree rerun above also passed. No failed run here.

Black/isort (black profile, line120), two new Python AST syntax checks, new
contract relative link targets and git diff-check PASS. Full synthetic read →
extract → quote → same-read verification is included; replay against another
read is rejected even with equal hashes. New helper imports only stdlib and pure
read/extraction contracts. No DB/settings/network/model/KnowledgeSnippet imports,
root conftest, provider/account/model call, DB/migration, ASGI/browser or full
suite. Real authorization/provider integration, performance and audit remain
unrun; historical DB/UI greens do not certify this source.

Scope published in dev `d4b8253a1ba99ea39700126cc9224a692ed8675c` before code.
Fresh dev/task refs fetched; local tree matched the published SHA before rerun.
Only task-card facts go to dev; code/tests/contracts stay in draft #327. Registry
row already has current owner/dialogue and IN_PROGRESS. No unrelated row edits,
merge, rebase, force-push, deploy or public migration. GitHub MCP publication;
clean dedicated checkout/venv and unmerged owned/local branches are retained.

Read this slice: current dev row/card/P12-T13 and P8-T34 handoff; extraction/read
contracts and typed source; knowledge/retrieval.py anchor/snippet API preview.
Existing generic anchors are not used to grant access or create shared knowledge.
Unchanged governing repo/remote-task/test-runner rules reused from earlier slices.
P8-T34's current dev card records NOT ACCEPTED audit at `681cf525`; that is upstream
evidence, not our rerun. Its transport remains unmerged/unactivated here. This
pure quoting slice changes no P8-T34 reader/native-link contract. P12-T13 budgets/
processing/retention review and actual provider lineage remain separate gates.

Next: typed compact insight candidates supported by these exact excerpts, with
explicit untrusted-data/unknown-version limits and mocked generation; no retained
private insight until P12-T13 storage/retention/GC contracts are reviewed. Concrete
authority/reader, real provider original-open, approved actions, paired local
execution and owner-local real-condition checks/audit remain unfinished. To
reproduce this slice locally, check out the exact source and run the command above
with Python >=3.14 and pytest; no DB required. Keep **draft / IN_PROGRESS**, not
READY or whole-parent complete. No merge, deploy or public migration.

---

## Mock bounded-read checkpoint — 2026-11-03

Owner continuation: https://app.notion.com/chat?t=40dc1b436029807fa62200a976b5dc4d

Separate slices in this PR: `c1844f62` inactive contracts/orchestration,
`42814fa3` synthetic tests, `61ab0d66` deadline checks directly before open/release.
Exact tested published source: **`61ab0d6693ac17b41b2c73df27a1b49c60e559c9`**.
[Contract](https://github.com/Starck43/social-media-ai/blob/feat/p12-t12-file-workspace/docs/FILE_WORKSPACE_CONTENT_READ.md).

Only application-injected authority/reader: catalog IDs plus fresh nonce →
authority assertion of LIST+READ and independent cloud-processing permission →
complete bounded async stream → close → UTF-8 helper → fresh equal authority
context → in-memory result. No concrete authority/provider implementation or
production caller is supplied. This tests orchestration, NOT real ACL/provider
checks. Root/policy/credential/processing/name/path/observation changes deny
release. No URL, provider fallback, cache, persistence, model, route, UI or writes.

Defensive proposals: 256 KiB decoded input, exact known full size, max 1024
chunks (including empty), one 5s cooperative budget including checks/close/parser.
Partial/range/unknown-size representations, replay, malformed chunks and overflow
fail closed. Explicit deadline/pending cancellation checks reject late results;
this is not preemption or a guarantee against dishonest/cancellation-erasing
adapters. The adapter must separately bound transport/decompression allocations
and prove exact-file/version integrity. Optional provider_version remains only
its assertion; absent stays None. Hashes/metadata never create provider versions.
Frozen slotted repr-hidden results retain no raw-byte buffer; text stays untrusted.

Python **3.14.10**, dedicated minimal sandbox venv, remote no-DB exception:

```sh
PYTHONPATH=. /data/p12-t12-venv/bin/python -m pytest \
  tests/unit_services/test_p12_t12_plain_text.py \
  tests/unit_services/test_p12_t12_plain_text_read.py \
  --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header
```

Exit 0: **221 passed, 0 failed, 0 skipped**, 0.52s; **129 new cases**, plus 92
existing pure-helper cases. Three known unknown-asyncio-config warnings because
pytest-asyncio is absent; new async scenarios use asyncio.run, not that plugin.
Initial scope passed 208 cases; cancellation/replay/malformed-authority additions
passed 219; deadline-edge additions passed 221. No failed run in this read slice.
Final published source above was fetched, tree-compared and rerun exactly.

Black/isort checks (profile black, line120), AST syntax on all four new Python
files, new contract relative link targets and git diff-check PASS. New shared
doubles live in tests/shared/file_content_read.py; service imports no concrete
DB/network/settings/model modules. Package initializers are inert. No root
conftest, DB, migration, real account/provider, model, ASGI/browser or full suite.
No DB target/fixtures existed for this slice; DB/integration acceptance remains
unrun, not green. Earlier 328-test DB/UI checks are historical only.

Registry scope published to dev `96bee78ce75091764e27c1d613443fb6bfaf1e99` before
code; fresh dev/task refs checked before publication. Only current card facts go
to origin/dev; the owner/dialogue/status row already remains IN_PROGRESS. Divergent
histories/task-card conflict are not merged/rebased; no force-push/deploy/public
migration. Same draft PR; GitHub MCP publication because sandbox git push lacks
credentials. Worktree/venv retained clean at the published continuation head;
unmerged local publication-equivalent branch and foreign branches preserved.

P8-T34 #313 discussion re-read: safe transport/native-original agreement is still
pending. P8-T34 owns real complete authorized reads/version binding; P12-T13 owns
processing/retention/capability/limits review. A protocol or comment is not their
approval. No concrete manager was invented or DB policy bypassed: future wiring
must reuse current managers and add reviewed independent processing consent.

Read additionally this slice: current dev row/P12-T12/P12-T13, plain-text contract,
original-open contracts/service, owner reader, folder policy, catalog-refresh
interface and shared-test conventions; P8-T34 discussion. Governing repo/remote-
task/test-runner rules were unchanged and reused from the preceding slice.

Next: review this seam and proposed budgets; implement reviewed real authority/
reader only after dependencies agree, with focused DB/provider integration tests
locally. Private version-aware insights, retention/GC, line evidence, approved
actions, paired local execution and full parent acceptance remain separate work.
To reproduce this slice locally, check out the exact SHA and run the command
above; no DB needed. Real-condition checks and audit remain owner-local later.
Keep **IN_PROGRESS / draft, not READY**. No merge, deploy or public migration.

---

## Pure UTF-8 helper checkpoint — 2026-11-03

Owner continuation: https://app.notion.com/chat?t=40dc1b436029807fa62200a976b5dc4d

Published in this PR as separate slices: `d74bdc4e` helper and `b8ff6104` tests.
Exact tested published source: **`b8ff61044f77b63e91721686f5906b8f21317930`**.
`app/integrations/file_workspace/plain_text.py` implements the isolated
[plain-text contract](https://github.com/Starck43/social-media-ai/blob/feat/p12-t12-file-workspace/docs/FILE_WORKSPACE_PLAIN_TEXT.md):
strict bounded `.txt` UTF-8, one leading BOM, canonical newlines, category-C
rejection, immutable repr-safe result, separate raw/text SHA-256 and sanitized
ordered failures. No caller, I/O, parser fallback, authorization, provider version,
DB/cache/logging, model, route or UI activation. Text remains untrusted data.

Python **3.14.10**, dedicated sandbox venv, remote no-DB exception:

```sh
PYTHONPATH=. /data/p12-t12-venv/bin/python -m pytest \
  tests/unit_services/test_p12_t12_plain_text.py \
  --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header
```

Exit 0: **92 passed, 0 failed, 0 skipped**, 0.16s, three pytest configuration
warnings because pytest-asyncio is absent in this deliberately minimal venv.
No async tests or root conftest/DB imports. New test module imports stdlib,
pytest and the pure helper; package initializers are inert. AST dependency test
restricts helper imports to hashlib/unicodedata/dataclasses/__future__.
Coverage includes exact/plus-one byte, character and line limits, multibyte text,
BOM/newline provenance, malformed UTF-8, controls/private/unassigned Unicode,
wrong types/names, inert prompts/HTML/URLs, immutability and sanitized errors.
At 256 KiB, valid UTF-8 necessarily exceeds the output character ceiling; tests
prove byte admission followed by output rejection, not a false successful result.
Initial local run: 91 passed/1 failed because a repr assertion matched the word
`text` in the module name. Corrected the test to check field assignments and
object repr identity; production validation was unchanged. Local exact-source
rerun passed before publication; published-tree rerun above is authoritative.

Black check, isort check (black profile, line 120), AST syntax checks on both new
files and `git diff --check`: PASS. No broad suite, DB, migration, ASGI/browser,
real provider/account, paid model or external file writes were run. Earlier
328-test DB/UI results are historical, not certification of this revision.

Fetched fresh dev `3c4f40f57876e7994f5446af2013b0f6b68559ef`; registry already
claims this owner/dialogue IN_PROGRESS. Only this row and owned docs are synced;
divergent histories and the task-card conflict are NOT merged/rebased. Git push
lacks sandbox credentials, so separate commits were published with GitHub MCP;
full local/published trees were compared equal before rerunning exact source.
Only status/card facts go to origin/dev, not helper/tests/contracts.

P8-T34 discussion #313 was re-read: no reviewed transport/native-target agreement
has arrived. The pure helper needs no provider; real reads, exact-file/version
lineage, refresh and original-open remain P8-T34-gated. P12-T13 capability/limits/
retention approval is still pending; implemented limits are synthetic defensive
proposals, not accepted production budgets. No default client or resolver enabled.

Read: README, DESIGN, AGENTS; remote-task and test-runner skills; COLLABORATION
Tasks/Evidence/Tests/Git cleanup; tests/AGENTS; DOCS_INDEX; current dev task row/
card, branch handoff, FILE_WORKSPACE_PLAIN_TEXT, current P12-T13 scope; pure package
initializers and original-target test conventions; P8-T34 PR discussion.

Next local handoff: check out the exact tested SHA and repeat the command above
with Python >=3.14 and pytest; review the helper contract/error privacy. No DB is
needed for this slice. The full parent still requires reviewed authorized bounded
read/version lineage, private insights/retention/GC, approved writes, paired local
execution, performance/storage corpus, real-condition checks and audit. Keep
**draft / IN_PROGRESS, not READY**. No merge, deploy or public migration.

Dedicated checkout `/data/social-media-ai-p12-t12` and venv are retained clean
for continuation; local publication-equivalent task commits are preserved, not
force-pushed/deleted. Only the owned task branch is used for implementation;
foreign branches/worktrees are untouched. Next owner action is local review or
continuation in the same PR, not integration.

---

## Latest continuation — original-open seam (2026-11-03)

Owner expanded the previous point-1 scope to continue the remaining task on mocks
in the [current dialogue](https://app.notion.com/chat?t=40dd5248ae668030a7a000a9e8d5112b).
This checkpoint is **internal original-open orchestration only**, not real browser
opening or whole-parent completion. Historical checkpoints below are retained.

Separate commits in this same PR:
- `116c2bd0` — provider-neutral context/native-target validation and owner-bound
  two-phase resolver orchestration; no default adapter/client, production host,
  route, UI button, persistence or migration.
- `fea1135f` — 97 new mock-only target/authority/privacy/lifetime tests.
- `2f4ac00b` — current scope, exact evidence, remaining milestone matrix and
  P8-T34 handoff; documentation only.

Catalog ID only, never client URL/path. LIST and READ are both mandatory, with
fresh actor/membership/source.view/personal-owner/root/cloud/retained-secret
checks before and after the injected resolver. Policy, account/credential and
metadata context changes deny handoff. Resolver work holds no DB locks; provider
phase has a 5s cooperative timeout, context/handoff TTL is 30s. This is not an
end-to-end DB deadline or post-delivery native-session revocation guarantee.
Targets bind the exact request/fresh provider-check assertion and an explicitly
reviewed exact HTTPS host/path namespace. No query/fragment/userinfo/ports,
traversal, ambiguous encoding or private download/share URL adaptation. The
native map and actual exact-file check are still P8-T34-owned/unimplemented here.
ResolvedOriginal means a current-original target, not a successful browser open,
known content version or historical snapshot. Catalog flags stay false.

Exact tested published source **`fea1135f5755c3f3869c44140d05b3107f766d6e`**:
**328 passed, 0 failed, 0 skipped**, exit 0, 7 existing warnings, 34.60s. Same
focused regression scope as the prior 231-test checkpoint plus the 97 new cases:
`tests/files`, traversal/native-target pure tests, three existing knowledge/file
UI test modules, P8 folder-policy/metadata tests and schema-guard tests. Exact
`pytest.main` list is in the [task card](https://github.com/Starck43/social-media-ai/blob/feat/p12-t12-file-workspace/docs/tasks/p12-t12.md#continuation--native-original-orchestration-contract-2026-11-03).
No full suite. New adapter calls are synthetic .invalid targets, never network.

Python 3.14, exclusive local PostgreSQL15 `social_manager_mock/test_schema` 0127;
canonical redirect before app imports; no API/runtime/other DB writer. Complete
test_schema and read-only public schema inventories match before/after, and all
five synthetic residue counts are zero. Black/isort (profile black, line120),
syntax on all five affected Python files, Markdown targets and diff-check PASS.
No new schema migration. Owned test cluster stopped; clean checkout/env retained.
No new UI/browser/ASGI open route was added: previous 18-PNG inspection is a
historical unchanged-UI checkpoint, not proof of original-open E2E. Previously
documented broad pure-import check failure remains unchanged/not rerun.

[P8-T34 integration request](https://github.com/Starck43/social-media-ai/pull/313#issuecomment-6600111283)
is pending; its bearer-like DownloadTarget must remain server-only, never a
browser redirect. No production resolver/refresh is enabled. Still outstanding:
reviewed native resolver + authenticated route/ASGI/browser flow; bounded analysis,
private version-aware insights and lifecycle budgets; approved conditional writes;
paired local execution; storage/performance and owner-local acceptance. Draft,
not READY. No shared-dev registry transition, merge, deploy, public migration,
real account/model/provider activation or external file mutation.

---

## Scope / handoff
P12-T12 implementation continuation of plan #307, branch `feat/p12-t12-file-workspace` created from dev (now behind current dev; reconcile before merge). Authorization: https://app.notion.com/chat?t=40c2c46c113980f09dfa00a93d1fb773

Implemented the **metadata-catalog foundation and default-off saved-metadata UI of slice 2**, not the whole parent. Detailed checkpoint: [task card](https://github.com/Starck43/social-media-ai/blob/feat/p12-t12-file-workspace/docs/tasks/p12-t12.md).

## Point-by-point commits
1. `60340ed1` — scope/coordination notes in P12-T12, P12-T13, P8-T34 and P12-T15.
2. `b96e1bc6` — additive 0124 metadata objects and immutable observations; tenant-safe links; existing 0122 evidence unchanged.
3. `07132d66` — owner-bound admission and SQL-filtered/keyset-paged search/detail. Current actor/membership/source.view/connection/root/policy rechecked under connection lock. No implicit sharing or local-only persistence. Partial pages never imply deletion; replay is idempotent; older observations cannot replace newer ones.
4. `0f6fee56` — focused mocked tests, migration round trip, bootstrap/AST compatibility. Reproduces the already-existing 0122 immutable-document guard on fresh test schemas; no applied migration rewrite.
5. `686c8055` — evidence and integration limits.

## Foundation checks (historical checkpoint)
Exact tested code SHA: `0f6fee5629cae3d866a9734cc34167b7cd5069cc`; subsequent commit is documentation-only.
Canonical test redirect before app imports, assertion of effective `test_schema`; `pytest.main` with `-q -o addopts=` and these five files:
- tests/files/test_p12_t12_catalog.py
- tests/files/test_p12_t12_migration.py
- tests/unit_services/test_p8_t34_folder_policy.py
- tests/unit_services/test_p8_t34_yandex_metadata.py
- tests/tooling/test_p11_t24_schema_guards.py

**120 passed, 0 failed, 0 skipped**, exit 0, one existing SQLAlchemy warning. Actual 0124 -> 0123 -> 0124 preserves 0122 snippet/document/revision fixtures. Black/isort profile black line120 on all 10 affected Python files and `git diff --check`: PASS.

Initial compatibility run: 77 pass / 2 static failures in existing 0122 schema-guard parsing/bootstrap; corrected and rerun. An initial local Alembic async-driver mismatch occurred before DDL; normalized URL for synchronous CLI, no stamping.

Local PostgreSQL only: `127.0.0.1:5432/social_manager/test_schema`; API stopped during DB checks. Complete test-schema inventory matches before/after tests/round-trip; public inventory matches its pre-migration state. Zero synthetic P12-T12 users/tenants and zero catalog objects/observations after cleanup. Test schema retains 0124 intentionally for continuation. Providers/accounts external writes/model calls: synthetic/mock only.

## Coordination / limits
P12-T13 owns contracts; P8-T34 owns Yandex network/credentials/list/stat/open; P12-T15 owns policy editing/sharing/approved writes. [Integration comment in #313](https://github.com/Starck43/social-media-ai/pull/313#issuecomment-6587771678), notes in all three cards.

DiskPage currently has path/name/kind only. This PR does not invent stable IDs, native versions, content freshness, content snapshots, deletes, backups or safe-open URLs. Defensive provisional limits: 100 page/query rows, 200 query chars, 2048 UTF-8 path bytes, 500 name chars, 10000 objects/connection, 32 metadata observations/object, admission ticket TTL60s; quota failures roll back the full page.

**Not implemented/accepted here:** provider activation, original-open, extraction/storage/GC, queued file tools, approved writes, paired local execution, 10000-entry performance acceptance or whole-parent E2E. No full-suite/live-provider claims; the bounded mocked browser checkpoint is below. No production fake rows, deployment, merge or public migration. Draft/IN_PROGRESS, not READY.

Direct shared-dev registry claim could not be published in this environment; only the task-branch local claim is recorded. Do not interpret this PR as a dev status transition.

## Latest continuation — saved-catalog UI

6. `8b8225a5` internal one-page owner-authorized reader → catalog bridge; required injected client, no default transport/job/route.
7. `a5cec7dc` `/app/knowledge/files` list/search/paging and file detail; `FILE_CATALOG_UI_ENABLED=false`, cloud-only entry links from knowledge/documents. Owner/membership/source.view/active secret/root/restrictive policy remain mandatory; no implicit sharing. Only saved metadata, no fake live freshness or original URLs. DB error → 503, not false empty state; privacy headers on rendered catalog.
8. `222e6f05` mocked integration, assembled auth/ASGI and strict-template states; shared synthetic fixtures; documented flag. Detail wording avoids claiming the file was never analyzed elsewhere.
9. `86f4a020` percent-query fixture correction: admissible provider paths only, literal `%` query must not become a wildcard.
10. `334feab8` evidence and coordination in P12-T12/P8-T34/P12-T13/P12-T15 cards.

### Final exact source and checks
Tested **`86f4a020db0278ab1c15668e043937979f319e79`**; the following commit is documentation-only. Canonical test redirect before app imports, effective schema asserted `test_schema`; API stopped during final checks. `pytest.main` with `-q -o addopts=`:
- `tests/files`
- `tests/unit_web/test_p12_t12_file_catalog_layout.py`
- `tests/unit_web/test_p12_t03_knowledge_documents.py`
- `tests/unit_web/test_p12_t17_evidence_ui.py`
- `tests/unit_services/test_p8_t34_folder_policy.py`
- `tests/unit_services/test_p8_t34_yandex_metadata.py`
- `tests/tooling/test_p11_t24_schema_guards.py`

**184 passed, 0 failed, 0 skipped**, exit 0, 7 existing deprecation warnings. Includes actual 0124→0123→0124 retention of 0122 evidence. Black/isort line120 on all 12 continuation Python files and diff-check passed. Complete schema inventories match; public untouched; 0 fixture users/tenants and 0 catalog objects/observations after cleanup. Synthetic credentials/accounts and HTTP MockTransport only.

Intermediate results are explicit in the card: first ASGI run had three helper/expectation failures, then 173 passed; first final run had 183 passes and one invalid percent-path fixture failure, fixed in `86f4a020`. No production validation was loosened.

Local Chromium against the actual synthetic-login app: list (3 items), matching-case Cyrillic form search, detail, 390px mobile navigation, empty search, existing dark theme. 7 captures, 0 page errors, 0 provider-host requests, no horizontal overflow in checked list/detail/search states. Harness initially used the wrong login selector and networkidle despite shell polling; corrected. Screenshot viewer failed for both PNG and JPEG: **visual/taste acceptance is not complete**.

### Explicit remaining defects / gates
- Historical browser checkpoint found a C-locale Cyrillic search defect. The later 0125 normalized-key slice below addresses it in focused synthetic tests; the earlier matching-case browser run was not a verification of this fix.
- P8-T34 #313 (`681cf525`) remains separate/unmerged; safe transport, browser/selectors and original-open belong there. No copying or production refresh route here. The internal bridge alone is not a production connector.
- Original-open, provider refresh, content extraction/conclusions, stable versions, sharing/policy authoring, local execution and approved mutations remain unimplemented here. Full suite, real providers, performance acceptance, public migration, deploy and merge were not run.

Keep **draft / IN_PROGRESS, not READY**, parent incomplete. These coordination notes do not publish a shared-dev registry transition.


## Unicode search continuation — 2026-11-03

`7813e61e` adds NFKC/casefold search keys and additive 0125 bounded backfill; `ebac1095` excludes migration from unrelated resource registration and requires online backfill; `bb4d869d` adds regressions; `874a77e8` formats them. Names and path identity stay unchanged, literal LIKE escaping and ACL-before-SQL-paging remain. Run with catalog writers stopped and deploy matching app/migration together; old writers cannot insert after migration. Review/rebuild search keys when Python Unicode data changes.

Exact tested source `874a77e8f1e78d0dcba4f3c5e14c6291d77d85f0`: **189 passed, 0 failed, 0 skipped**, 7 existing warnings, same focused suite listed above. `C/C` local PostgreSQL; synthetic users and MockTransport only. Tests include mixed-case Cyrillic, Unicode expansion/composition/compatibility, literal wildcard, ACL/paging, 503-row 0125→0124→0125 backfill, and legacy 0122 evidence through 0125→0123→0125. Schema inventory matches after tests relative to post-upgrade baseline; public unchanged. Zero synthetic fixture users/tenants or catalog rows remain. Black/isort and diff-check pass. Local API restarted against `test_schema` 0125 with flag off; readyz/login 200. No public migration, deployment, merge, live provider or real-account audit.

P8-T34 integration request: https://github.com/Starck43/social-media-ai/pull/313#issuecomment-6598494297. Direct session message could not be sent; PR comment was the requested fallback. Visual acceptance, safe transport/selector integration, original-open and the rest of P12-T12 remain incomplete. Real-condition tests/audit are deferred to local environment.


## Resumable one-folder paging — 2026-11-03

All work stays in this PR. `d0e1f8b0` adds 0126 private per-connection checkpoints; `6b90d786` adds trusted-client bounded one-page continuation; `92fa18b2` atomically admits metadata and advances the checkpoint under a current authorized lease; `dd957c6e` fixes a missing import; `5bc39184` does not expose a terminal cursor. `d8f8816e` shows honest saved-state/error messages without enabling a refresh route; tests in `63febae0`/`db923ca9`. Claims expire after 90s; stale claims cannot admit or advance. Reauthorization and policy checks apply throughout. Expected provider/quota errors preserve the offset; no deletion inference.

Exact tested code `db923ca9e84c5c46d933ad9256cddef49f2af39e`: **204 passed, 0 failed, 0 skipped**, 7 existing warnings; first run had 197 passed/4 failed due to missing `FileCatalogScan` import, then fixed. Focused mocked suite, API stopped, `test_schema` 0126 only; 0126→0125→0126 and legacy retention tested. Schema inventory matches post-upgrade baseline, public unchanged; zero synthetic users, tenants, objects, observations and scan checkpoints. Black/isort and diff-check pass. API restarted flag-off, readyz/login 200. No real provider/accounts, external writes, public migration, deploy or merge.

This is a **single-folder checkpoint**, not recursive traversal, a stable snapshot, deletion reconciliation or a production connector. Offset pages can skip/duplicate items when remote folder changes; end-of-list is not proof of full synchronization. P8-T34 integration remains gated: https://github.com/Starck43/social-media-ai/pull/313#issuecomment-6598784826. Desktop/mobile synthetic UI captures had no horizontal overflow; 390px diagnostics noted existing shell notification-dock overlap with a catalog note. Image viewer failed, so visual acceptance is not complete. Real-condition local tests and audit remain later.


## Final mocked continuation checkpoint — 2026-11-03

Exact tested source: **`45cfe77cd1ed56736e16efd05f1480cc744e981d`**. The following
handoff commit changes documentation only. This supersedes the earlier
single-folder-only and unavailable-visual-viewer limitations for this scope.
It is not parent acceptance, real-provider certification or a READY transition.

### Ordered slices in this PR

- `2f79183b`: opt-in recursive checkpoint and additive 0127; default remains one folder.
- `6636fba6`: actual footer reservation outside the scrollport; honest scan wording.
- `d5063a15`: recursive/policy/retry/concurrency/bounds/migration regressions.
- `50c98208`: corrected the concurrency fixture barrier, not the production locking contract.
- `3fc00eee`: fixed empty-state action overlap and scoped control contrast.
- `cd51e11b`: fixed mobile filename squeezing and dark-border override.
- `45cfe77c`: portable, fully intercepted browser layout regression harness.

### Focused tests and DB hygiene

The prepared environment used Python 3.14 and a private local PostgreSQL 15
cluster. No API, runtime owner or other database writer was running. The target
was **127.0.0.1:5432/social_manager_mock/test_schema**, UTF-8, C/C locale. The
working URL/schema were unused placeholders; canonical `resolve_and_redirect()`
ran before app imports, and effective `settings.DB_SCHEMA == 'test_schema'` was
asserted. Shared DB work was serialized with an exclusive `flock`.

Exact pytest invocation after redirect:

```python
pytest.main([
    "tests/files",
    "tests/unit_services/test_p12_t12_catalog_traversal.py",
    "tests/unit_web/test_p12_t12_file_catalog_layout.py",
    "tests/unit_web/test_p12_t03_knowledge_documents.py",
    "tests/unit_web/test_p12_t17_evidence_ui.py",
    "tests/unit_services/test_p8_t34_folder_policy.py",
    "tests/unit_services/test_p8_t34_yandex_metadata.py",
    "tests/tooling/test_p11_t24_schema_guards.py",
    "-q", "-o", "addopts=",
])
```

**231 passed, 0 failed, 0 skipped**, exit 0, 7 existing deprecation warnings.
Includes synthetic MockTransport reads, child retry/interruption, competing
claim/restart, current owner/tenant/policy/revoke checks, limits, default
single-folder compatibility, 0127→0126→0127 checkpoint semantics, Unicode
backfill, and legacy 0122 evidence through 0127→0123→0127. Final full schema
inventories from `scripts/ci/schema_inventory.py` match before/after; read-only
public inventory also matches. Explicit residue counts: **0** synthetic users,
tenants, catalog objects, observations and scan checkpoints. The dedicated
cluster is stopped after checks; `test_schema` retains 0127 for continuation.
No DDL/data writes to public, real accounts, providers, model calls or external
file writes occurred. An initial bootstrap attempt used SQL_ASCII and failed
before reference seeding; the successful isolated test database was freshly
created UTF-8. No production schema or migration history was repaired/stamped.

Black/isort (profile black, line120) pass for all 12 changed Python files;
changed-source syntax compilation, AST migration graph (single head 0127),
`node --check`, Prettier check on the optional browser harness, and
`git diff --check` pass. The broad offline command remains **failed**, not green:
its existing pure-import guard rejects `sqlalchemy` at
`scripts/schema_guards.py:674`, unchanged since starting source `e5b10107`.

### Reproducible synthetic browser / visual checks

With Node/Playwright, Chromium, the Python/Jinja environment, and the public
Tailwind **3.4.17**, Alpine **3.14.1**, HTMX **1.9.12** CDN bundles pre-fetched as
`tailwind.js`, `alpine.js`, `htmx.js` into an assets directory:

```sh
PYTHON=/data/p12-t12-py314/bin/python CHROMIUM=/usr/local/bin/chromium \
  node tests/shared/file_catalog_browser.cjs /data/p12-assets /data/p12-final-previews
```

Exit 0: 17 checked state/geometry/contrast records, **0 page errors, 0 unhandled
requests**; all browser HTTP responses were local fixtures/assets, no provider
network. The actual shell CSS/JS is exercised on rendered strict Jinja fixtures,
not a real-account/full-app browser E2E claim. Eighteen PNGs were opened and
individually visually inspected: list/no-connections/empty/search/detail/long-name/
scan-error at 1440×900 and 390×900; dark list at both widths; mobile navigation
and notification modal. Scrolled mobile states were inspected, not just headers.
The discovered empty-action and narrow-row defects were fixed and re-inspected.

No horizontal overflow or dock/scrollport overlap at top/middle/end. At 390px,
content column is 280px, empty-action gap 20px; main bottom 820px and dock top
836px in a 900px viewport. No-dock margin is 0px. Tested control-border contrast:
7.58:1 light / 6.96:1 dark; placeholder: 7.58:1 / 7.87:1. This is scoped visual
QA, not a full application accessibility audit or owner acceptance.

### Integration, continuation and remaining gates

P8-T34 draft #313 remains at `681cf525`, not copied/merged. Updated
[coordination request](https://github.com/Starck43/social-media-ai/pull/313#issuecomment-6599395345):
DiskPage shape unchanged; one injected-client page per step; provider offset,
not filtered-item count, drives continuation. Review/agreement is still pending,
so **no real refresh route, scheduler or default network client is enabled**.
Credentials, transport, provider browser/selectors and original-open remain
P8-T34's responsibility. Contracts/retention remain P12-T13; policy editing,
sharing and writes remain P12-T15. No content bytes, stable ID/version,
delete inference or snapshot/reconciliation guarantee was added.

Keep draft / IN_PROGRESS; owner-local real-condition checks, audit, performance
acceptance and any production integration remain deferred. No dev registry
change, merge, deploy or public migration. The clean dedicated checkout/branch
and Python environment are retained for continuation, not deleted because their
commits are unmerged. Recheck heads/migration conflicts before resuming or any
separately authorized integration.
