# P20-T16: visual and regression evidence

**Tested source:** `5fef978cffeacda6789e5322e483275cf07d23a5`. The subsequent evidence-only commit adds this document, self-contained comparison HTML and two browser fixture files; it does not change production code. The four HTML files include actual local Chromium captures, not new design proposals. Download each HTML file from GitHub and open it locally (GitHub does not render repository HTML as an application):

- [Today](today.html) — user-provided PDF page 3 vs 1440/390 light/dark.
- [Chat](chat.html) — PDF page 5 vs 1440/390 light/dark.
- [Inbox](inbox.html) — PDF page 11 vs 1440/390 light/dark.
- [Tasks / personal planner](planner.html) — PDF page 13 vs 1440/390 light/dark.

**Not a visual acceptance:** The supplied PDF has desktop light references only. There is no approved mobile or dark reference to compare pixel-for-pixel. The actual UI retains the existing shell and slate/cyan styling, so it does not match the PDF's green palette. The local synthetic data differs from the illustrated data; notably, inbox is metadata-only, and the chat screenshots do not reproduce all example inline cards. Layout, content density and controls also differ. Review and explicit design acceptance remain open. The PDF itself was provided in the task conversation; the pages above are embedded excerpts, not a link to the original PDF. A reviewer-accessible link to the original is still needed.

## Checks on the tested source

- Isolated local PostgreSQL 15: `127.0.0.1:55432/p20_work_demo`, application schema `p20_t16_demo`, separate `test_schema`; Alembic baseline `p12t12_s1_lifecycle`. Synthetic users, connections, and data only. No live provider calls or sends.
- `pytest -q tests/unit_web/test_p20_t16_communications_archive_filter.py tests/unit_web/test_p20_t16_inbox_pagination.py tests/communications/test_p20_t09_inbox_triage.py tests/unit_web/test_p20_t16_google_calendar_range.py tests/unit_web/test_p20_t10_task_surface.py tests/calendar/test_p16_t03_google.py tests/calendar/test_p16_t03_t04_service_db.py` — exit 0, **118 passed**, 1 SQLAlchemy deprecation warning. Real PostgreSQL service tests use isolated fixtures; the mixed Google route test uses mocked Google transport, service actor/connection and local inventory doubles, no live Google. For all-day, the range is deliberately unavailable; saved inventory is labelled and kept out of the month grid, not a complete occurrence projection.
- `node tests/browser/p20_t16_responsive.cjs` — exit 0, 36 surface/viewport/theme combinations, no horizontal overflow or page errors; not a pixel comparison.
- Browser scripts `p20_t16_live_smoke`, `today_layout`, `inbox_layout`, `mobile_nav`, `calendar_month`, `calendar_accessibility`, `chat_panel` — each exit 0 against local FastAPI and synthetic data. Cover Today→planner, task tabs, month navigation, inbox triage, chat context, mobile navigation and accessibility. Some scripts mock network responses; no claim of live-provider validation.
- `python -m tests.browser.p20_t16_archive_filter_fixture` with `P20_BASE_URL=http://127.0.0.1:18020` and synthetic login state — exit 0. Exercises actual HTTP + PostgreSQL for true/false archive filters, excluded deep links and ordinary unfiltered link. Creates two uniquely named synthetic threads and verifies both are deleted in `finally`.
- `python scripts/ci/schema_inventory.py test_schema --diff <before>` and the same for `p20_t16_demo` — both exit 0, no schema drift. The demo database and its original synthetic seed remain for the demonstration; they were not presented as cleaned up.

## Open evidence and limits

No successful real reply-draft persistence is claimed: previous HTTP 201 evidence used a mock; the real local handler with a verified synthetic contact still needs a separate test. Google OAuth/live responses, CalDAV recurrence, provider sends, and production permission flows have not been exercised. 36 viewport checks are not a visual acceptance; original PDF linking, precise review of the comparison pages, mobile/dark approval, and final acceptance remain open. This PR remains draft, without merge.
