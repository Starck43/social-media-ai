# Audit evidence

Load a report through its task, phase or PR link, never this entire directory.
`<id>.md` owns accepted summaries or detailed blocked diagnostics. A closed summary
records verdict/date, source SHA, corrections, affected files/tests, actual checks,
limitations and dialogue; the parent phase links it and ROADMAP drops the row.
Active blocked work keeps its task card. Orphan completion leaves only its audit.
Do not duplicate PR transcripts; link exact comments/revisions instead.

## Folders

Grouping follows the naming rule below, so a new report just lands in its folder.

| Folder | Contents |
| --- | --- |
| `pN/` | Task audits for phase N (`p15/p15-t05-0141.md`, `q.md` for question rows under `q/`). A file keeps its `<id>` name, including revision or slice suffixes (`p12-t14-refresh-fence-44543165.md`). |
| `q/` | Question and owner-decision records: `q-18-owner-decisions.md`, `q-19-messaging-decisions.md`. |
| `history/` | Pre-compaction and subsystem histories: `phase-pN-history.md` (recorded instructions, READY claims and pending integration notes are historical; the current phase plan and final acceptance section determine disposition) and `<subsystem>-history.md`/dated `<YYYY-MM-DD>-<topic>.md` records that preserve source-specific rationale. Current contracts stay in the subject folders of `docs/`. |
| `shared/` | Cross-phase proposals and evidence that belong to no single phase (`personal-llm-ownership.md`, `wheel-runtime-completeness.md`). |

`docs/audit/README.md` is the only file directly in `docs/audit/`.
