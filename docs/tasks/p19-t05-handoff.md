# P19-T05 — hand-off: do not wait for the full P15-T14

## Current state (2026-11-09)

- `IN_PROGRESS`, owner `notion-ai-p19-t05`.
- DOCX/PDF rendering is recovered **locally only**. It has no branch on `origin` and no PR.
  A workspace reset deletes it. Publish it before writing another line.
- Delivery is blocked by the attachment contract, which lives in [P15-T14](p15-t14.md).

## What P15-T14 already publishes

PR #451 (Draft, head `3b2f94fd`) carries `app/services/communications/attachments.py`:
`AttachmentRef`, `channel_supports_attachments`, `validate_attachment_set`,
`verify_delivered_bytes`, `attach_to_message`. That is a usable contract **today** —
build the renderer output against it instead of inventing a parallel shape.

## What is still missing

The outbox half: contract fields on the outbox item, store, model, migration, and the
scheduling-time refusal inside the outbox service. Until it lands, an item can be
built with attachments but cannot be scheduled with them.

## Order

1. Renderer: push a branch and open a Draft PR from the first green test.
2. Renderer output → `AttachmentRef` with both hashes: `(revision_id, revision_body_hash)`
   from the approval, `attachment_bytes_hash` from the exact rendered bytes.
3. Stop at the scheduling call. Do not extend `outbox.py` — the owner forbade it for this task;
   P15-T14 owns that path and is an active branch.
4. When the outbox half merges, wire scheduling and the dispatch-time recheck.

## Acceptance reminder

An edited revision is refused on the body hash; a re-rendered file is refused on the
attachment hash, each with its own recorded refusal. A channel without attachment support
(email only) is refused at scheduling time, not at dispatch. Restart mid-dispatch never
sends twice or sends a partial set.
