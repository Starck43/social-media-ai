# P12-T12 — Bounded plain-text extraction contract

Companion to [P12-T12](tasks/p12-t12.md), original plan slice 3.
Implementation branch/PR: `feat/p12-t12-file-workspace`, [#327](https://github.com/Starck43/social-media-ai/pull/327).
Owner continuation: https://app.notion.com/chat?t=40dd5248ae668030a7a000a9e8d5112b

## Delivery boundary

This checkpoint specifies the smallest next parser slice; **no extractor is
implemented or activated by this document**. Defaults below are conservative
proposals for synthetic implementation/tests, pending P12-T13 capability review.
They are not provider guarantees, accepted production budgets or new chat limits.
P8-T34 still owns real authorized reads/network/credentials. This document does
not resolve the real original-open gate or complete the wider analysis milestone.

## Proposed isolated helper

`extract_plain_text(payload: bytes, *, name: str)` accepts an already bounded,
complete byte payload and a display filename. It is internal and pure: no path
opening, URL fetch, DB/cache writes, subprocess, archive interpreter or model call.
Its name is not a file selector, authorization grant or trustworthy MIME header.

| Capability / bound | Proposed first implementation |
| --- | --- |
| Format | Only a final `.txt` suffix, ASCII case-insensitive; no automatic fallback for other extensions. |
| Filename | Nonempty string, at most 500 characters; no slash/backslash or Unicode category-C characters. |
| Input | Exact `bytes` type, at most 262144 bytes (256 KiB); check size before decoding. No str/bytearray coercion. |
| Encoding | Strict UTF-8; an optional single leading UTF-8 BOM is removed from extracted text only. Invalid bytes fail, never decode with replacement or guess an encoding. |
| Text | CRLF and lone CR become LF; otherwise preserve whitespace and Unicode without casefold/NFKC. Reject category-C characters except TAB, LF and CR; an embedded/second BOM is rejected. |
| Output | At most 32768 Unicode code points after newline/BOM handling, at most 1000 logical lines. Reject overflow rather than silently truncate. |
| Lines | Empty output has zero lines; otherwise count `text.split("\n")`, including a final empty line after a trailing LF. Anchors are 1-based inclusive line ranges over that canonical text. |
| Interpretation | Treat all content as untrusted plain text, never instructions or executable markup; do not execute URLs, HTML, scripts, formulas or embedded commands. |
| Unsupported | PDF/Office/CSV/Markdown/archives/images/OCR/encrypted or legacy-encoded documents are outside this helper; no import of their parser libraries. |

A renamed structured file that passes all plain-text rules is still only plain
text: this helper does not certify its semantic format, safety or authenticity.
Any future renderer must escape it; future model/tool consumers must preserve the
untrusted-data boundary. Do not relax the existing chat attachment parser or use
this helper as its validation fallback.

## Result, provenance and failure contract

Proposed immutable result: canonical `text`, `raw_sha256`, `text_sha256`,
`parser_version="utf8-plain-v1"`, `input_bytes`, `text_characters`, `line_count`.
Hashes are lowercase SHA-256 hex: raw hash covers the exact input including BOM
and original newlines; text hash covers canonical text encoded as UTF-8. Thus
CRLF/LF variants can share a text hash while retaining different raw hashes.
No content-derived fields appear in the default repr or automatic logs/errors.

The helper returns no source URL, provider ID/version, tenant/owner, freshness,
LLM conclusion or permission claim. Equal hashes do not merge file identities,
prove current content, create a backup or authorize cross-tenant reuse. A later
service must attach actual authorized read/version lineage; do not infer it from
catalog name/type observations. No chain-of-thought is requested or stored.

Proposed sanitized rejection codes: `unsupported_format` (including invalid
filename), `invalid_input`, `input_too_large`, `invalid_encoding`, `unsafe_text`,
`extraction_limit`. Check in that order: filename, exact bytes type, byte size,
strict decode, character safety, canonical output bounds. Messages must not echo
filename, payload, path, hashes or provider response. Empty text is a valid result
with zero lines, not a claim that the source never contained useful information.

## Authority, processing and storage remain separate

Before any real read/extraction, an owning service must independently recheck
active actor/membership/source.view, tenant/personal connection owner, LIST+READ,
current root/restrictions and permitted processing boundary; repeat before release
or persistence after asynchronous work. No workspace-wide KnowledgeSnippet
projection or implicit sharing. local_only/company inputs must not enter a cloud
read/model/cache/log path; the pure helper is not a local execution route.

This helper retains nothing in DB, files, indexes or caches, and has no TTL/GC,
queue, UI action or paid model. Process-memory lifetime is not secure erasure.
No retained-output/pin/backup promise follows from hashing. Persistence, private
insights, deduplication domain, cache quotas/retention/GC and numeric model/provider
budgets require subsequent P12-T13-reviewed contracts and separate slices.

## Acceptance matrix for the later implementation

Focused synthetic tests must cover empty/ASCII/Cyrillic/emoji text; leading vs
embedded BOM; CRLF/CR/LF normalization and raw/text hash distinction; whitespace
preservation and combining Unicode; byte/character/line exact limits and plus-one
rejections; trailing-newline line counting and multi-byte boundary cases; invalid
UTF-8, NUL/DEL/bidi/control characters, wrong payload types and unsupported names;
plain-text prompt/HTML/URL strings staying inert; immutable/repr-safe output and
sanitized errors. No provider/model/DB fixture is needed for this pure helper.
Add no public route, provider adapter or implicit parser reuse with that slice.

## Evidence and next action

Documentation only, authored against PR source `2f4ac00b` after the owner-requested
registry refresh. Sandbox command execution failed twice with infrastructure
errors before shell work; no parser code, static command, pytest or DB access was
performed for this checkpoint. Earlier 328-test results belong to `fea1135f`, not
to this document or the unimplemented helper. No tests/approval are fabricated.

Read: current dev task/ROADMAP and COLLABORATION tasks/evidence rules; original
P12-T12 slice-3 plan and current branch handoff from this session; P12-T13 card;
only the loaded header/limit preview of `chat_attachments.py`, not its full body.
The P12-T13 card's named planning branch returned 404; no unseen contract or
approval is inferred. Review the proposed limits with P12-T13, then reconcile the
retained checkout from current dev/PR heads after infrastructure recovery and
implement the pure helper plus focused tests as separate commits. Parent task
stays in progress and PR draft. No merge/deploy/public migration or live access.
