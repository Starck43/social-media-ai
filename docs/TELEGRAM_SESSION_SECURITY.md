# Telegram personal-session isolation

A Telegram MTProto StringSession is a full personal credential. It is separate
from the shared Bot API transport and must not be printed, committed or shared
between workspaces as an implicit substitute for another person.

A source with a recorded or explicitly selected personal owner resolves that
owner against current workspace membership. Its MTProto `api_id`, `api_hash` and
`session` must all be present in that same user's encrypted vault. Missing or
unreadable parts never fall back individually to deployment environment values.
A selected owner who is no longer an active member is denied; a forged or
invalid selection is not replaced by another owner. `user` mode reports that
personal authorization is required. In `auto` mode a missing personal session
falls back to the existing push-only path, not another user's MTProto account.

**Compatibility warning:** deployments which previously relied on the global
`TELEGRAM_SESSION` to serve an explicitly owned source must explicitly configure
that owner's personal session before deploying this change. Merely setting global
`TELEGRAM_API_ID`/`TELEGRAM_API_HASH` does not fill a partial personal vault.
No credentials are copied or ownership changed automatically.

The historical deployment-wide fallback remains for truly ownerless sources as
a transitional behavior and is logged without credential contents. It remains a
cross-workspace risk, not a recommended default. Inventory ownerless sources and
legacy-session usage with authorized non-secret diagnostics, agree per-source
ownership and explicit migration/revocation, and only then remove the fallback.
No live inspection, migration or session revocation is authorized by this note.
