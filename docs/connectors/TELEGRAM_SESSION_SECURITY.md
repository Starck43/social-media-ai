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

**Fail-closed change:** the deployment-wide `TELEGRAM_SESSION` is ignored for
all MTProto session resolution, including ownerless sources and direct credential
resolution. Ownerless `user` mode requires authorization; ownerless `auto` remains
push-only. The shared Bot API transport is separate. No credentials are copied or
ownership assigned automatically.

**Important remaining work:** this intermediate slice still stores personal
sessions by user, not by `(workspace, user)`. It does not implement the requested
per-workspace authorization journey. Do not present it as complete or deploy it
as workspace isolation; implement and test those boundaries before acceptance.
The existing interactive terminal login is not a supported web authorization flow.
No live inspection, migration or session revocation was performed.
