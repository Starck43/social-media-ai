"""`/app/chat` — the browser surface of the same agent (stage F).

The page is a transport, not a second brain: `handle_web_message` runs the
shared turn, so what matters here is the wiring, not the model. The tests
below pin the three things that would otherwise drift:

* **one conversation, one session** — the transcript rendered on `/app/chat` is
  the messenger session's, keyed `channel='web'` per user, so `/stop` from
  Telegram clears the same history.
* **no page-open side effects** — merely visiting must not create a session row
  or call an LLM (it would burn the daily budget for nothing).
* **the workspace boundary** — a member's turn runs as *their* workspace, with
  their membership role, never as another one.
"""

from __future__ import annotations

import json
import re
import secrets

import pytest
from httpx import ASGITransport, AsyncClient

from app.agent.runtime import WEB_CHANNEL
from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import User
from app.models.managers.agent_message_manager import agent_messages
from app.models.managers.agent_session_manager import agent_sessions
from app.models.managers.tenant_manager import TenantUserManager, tenants

CSRF_RE = re.compile(r'name="_csrf" value="([^"]+)"')
GREETING = "Спросите агента о ваших данных"
PASSWORD = "secret-password-1"

# The bubble carries `whitespace-pre-wrap`, so anything between its tags and the
# message is rendered: a leading newline plus the template's indentation became
# visible padding that shifted every bubble's first line and broke the
# left/right alignment. This captures the message's own text — nothing else.
# The assistant bubble has no inner text at all: Markdown is rendered
# client-side, so the message travels in the `x-html` argument instead.
BUBBLE_RE = re.compile(
    r'<div class="[^"]*whitespace-pre-wrap[^"]*"(?:\s+x-html=\'([^\']*)\')?>(.*?)</div>',
    re.S,
)
# What sits in `x-html` is the Alpine expression; the message is its argument.
XHTML_ARG_RE = re.compile(r"^renderMarkdown\((.*)\)$", re.S)


async def _client() -> AsyncClient:
    app = create_application()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=True)


async def _csrf(client: AsyncClient, path: str) -> str:
    page = await client.get(path)
    assert page.status_code == 200
    match = CSRF_RE.search(page.text)
    assert match, f"no csrf input on {path}"
    return match.group(1)


def _name(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(4)}"


async def _register(client: AsyncClient, prefix: str) -> tuple[User, int]:
    """Sign up: the new user owns a fresh workspace (membership role `owner`)."""
    username = _name(prefix)
    token = await _csrf(client, "/app/register")
    resp = await client.post(
        "/app/register",
        data={
            "username": username,
            "email": f"{username}@example.test",
            "password": PASSWORD,
            "workspace": f"{prefix} workspace",
            "_csrf": token,
        },
    )
    assert resp.status_code == 200
    user = await User.objects.get(username=username)
    memberships = await TenantUserManager().web_memberships(user.id)
    assert user is not None and memberships
    return user, memberships[0].tenant_id


async def _drop(user: User | None, tenant_id: int | None) -> None:
    if user is not None:
        await User.objects.delete_user(user.id)
    if tenant_id is not None:
        await tenants.delete_by_id(tenant_id)


async def test_opening_the_page_creates_no_session() -> None:
    """Visiting `/app/chat` must be free: no session row, no LLM call.

    Otherwise every page view would burn a turn (and daily budget) for someone
    who has not asked anything yet.
    """
    async with await _client() as client:
        user, tenant_id = await _register(client, "ChatOpen")
        try:
            page = await client.get("/app/chat")
            assert page.status_code == 200
            assert GREETING in page.text, "empty state invites the first question"

            with tenant_scope(bypass=True):
                assert await agent_sessions.get(channel=WEB_CHANNEL, chat_id=str(user.id)) is None
        finally:
            await _drop(user, tenant_id)


async def test_the_page_ships_the_markdown_renderer() -> None:
    """`marked` has to reach the browser, or every reply renders as plain text.

    The chat template pulls it in through `{% block extra_head %}`, which
    `base.html` must declare — an undeclared block is dropped silently by Jinja.
    That is exactly what happened: `marked` stayed undefined, `renderMarkdown()`
    fell through to its `<br>` fallback, and the reply was visible but the
    Markdown was not converted.
    """
    async with await _client() as client:
        user, tenant_id = await _register(client, "ChatMd")
        try:
            page = await client.get("/app/chat")
            assert page.status_code == 200
            assert "marked.min.js" in page.text, "marked.js never reaches the browser"
            assert "marked.setOptions" in page.text, "marked loads but is never configured"
            assert ".msg-prose" in page.text, "the bubble styles ride in the same block"
        finally:
            await _drop(user, tenant_id)


async def test_a_sent_message_becomes_the_transcript(monkeypatch: pytest.MonkeyPatch) -> None:
    """One POST → the turn is recorded and the answer is on the same page."""
    replies = iter(["Первый ответ", "Второй ответ"])
    seen: list[tuple[str, int, str]] = []

    async def _fake_turn(
        text: str,
        *,
        tenant_id: int,
        user_id: int,
        role_id: int | None = None,
        role_codename: str | None = None,
    ) -> str:
        seen.append((text, tenant_id, role_codename))
        # Stand in for the real turn: it opens the session and persists both
        # sides, which is exactly what makes the transcript the shared one.
        with tenant_scope(tenant_id):
            session = await agent_sessions.get(channel=WEB_CHANNEL, chat_id=str(user_id))
            if session is None:
                session = await agent_sessions.create(
                    tenant_id=tenant_id,
                    channel=WEB_CHANNEL,
                    chat_id=str(user_id),
                )
            await agent_messages.append(session_id=session.id, role="user", content=text)
            reply = next(replies)
            await agent_messages.append(session_id=session.id, role="assistant", content=reply)
            return reply

    # The route imports `handle_web_message` from the runtime inside the handler,
    # so the patch has to sit on the source module.
    monkeypatch.setattr("app.agent.runtime.handle_web_message", _fake_turn, raising=True)

    async with await _client() as client:
        user, tenant_id = await _register(client, "ChatSend")
        session_id = None
        try:
            token = await _csrf(client, "/app/chat")
            resp = await client.post("/app/chat", data={"text": "Покажи дашборд", "_csrf": token})
            assert resp.status_code == 200

            # The turn ran as this workspace, with this user's platform role.
            assert seen == [("Покажи дашборд", tenant_id, "SUPERUSER")]

            # The page answers inline — no redirect, no polling a job.
            assert "Первый ответ" in resp.text
            assert resp.url.path == "/app/chat"

            with tenant_scope(bypass=True):
                session = await agent_sessions.get(channel=WEB_CHANNEL, chat_id=str(user.id))
                assert session is not None
                session_id = session.id
        finally:
            if session_id is not None:
                with tenant_scope(bypass=True):
                    await agent_sessions.delete_by_id(session_id)
            await _drop(user, tenant_id)


async def test_the_transcript_is_the_same_one_the_messenger_sees() -> None:
    """The page must read `agent_messages`, not a web-only copy of history.

    That is the whole promise of this page: `/stop` from Telegram clears the
    context the next browser question is answered with. A web-specific store
    would silently fork the conversation.
    """
    async with await _client() as client:
        user, tenant_id = await _register(client, "ChatHist")
        session_id = None
        try:
            with tenant_scope(tenant_id):
                session = await agent_sessions.create(
                    tenant_id=tenant_id,
                    channel=WEB_CHANNEL,
                    chat_id=str(user.id),
                )
                session_id = session.id
                await agent_messages.append(
                    session_id=session.id,
                    role="user",
                    content="Вопрос из мессенджера",
                )
                await agent_messages.append(
                    session_id=session.id,
                    role="assistant",
                    content="Ответ из мессенджера",
                )

            page = await client.get("/app/chat")
            assert page.status_code == 200
            assert "Вопрос из мессенджера" in page.text
            assert "Ответ из мессенджера" in page.text
        finally:
            if session_id is not None:
                with tenant_scope(bypass=True):
                    await agent_sessions.delete_by_id(session_id)
            await _drop(user, tenant_id)


async def test_a_bubble_renders_the_message_without_template_whitespace() -> None:
    """The message must reach the bubble with nothing added around it.

    The bubbles are laid out with `justify-end` / `justify-start`, so the visual
    anchor is the bubble's own padding. `whitespace-pre-wrap` also preserved the
    newline and the indentation that sat between the template's tags and
    `{{ message.content }}`, which pushed the first line of every message to the
    right and made the two sides look misaligned.

    Indentation *inside* a message is content the agent wrote (code blocks,
    nested lists) and must survive; the template's is not.
    """
    async with await _client() as client:
        user, tenant_id = await _register(client, "ChatBubbl")
        session_id = None
        try:
            with tenant_scope(tenant_id):
                session = await agent_sessions.create(
                    tenant_id=tenant_id,
                    channel=WEB_CHANNEL,
                    chat_id=str(user.id),
                )
                session_id = session.id
                await agent_messages.append(session_id=session.id, role="user", content="Покажи источники")
                await agent_messages.append(
                    session_id=session.id,
                    role="assistant",
                    content="Ответ:\n    отступ внутри сообщения",
                )

            page = await client.get("/app/chat")
            assert page.status_code == 200

            bubbles = BUBBLE_RE.findall(page.text)
            assert len(bubbles) == 2, f"expected one bubble per message, got {bubbles!r}"

            # Flush against the tags, and no leftover blank edges.
            user_html, user_text = bubbles[0]
            assert not user_html and user_text == "Покажи источники"
            # The assistant side is Markdown rendered client-side: what sits in
            # the bubble is the Alpine expression, the message is its argument.
            assistant_html, assistant_text = bubbles[1]
            assert assistant_text == ""
            arg = XHTML_ARG_RE.match((assistant_html or "").strip())
            assert arg, f"expected a renderMarkdown(...) bubble, got {assistant_html!r}"
            # Internal newlines and indentation are the message's own.
            assert json.loads(arg.group(1)) == "Ответ:\n    отступ внутри сообщения"
        finally:
            if session_id is not None:
                with tenant_scope(bypass=True):
                    await agent_sessions.delete_by_id(session_id)
            await _drop(user, tenant_id)


async def test_a_member_does_not_inherit_the_owner_conversation() -> None:
    """Session is keyed by user id: one member's chat is another's private."""
    from app.models.managers.tenant_manager import TenantUserManager

    owner: User | None = None
    member: User | None = None
    tenant_id = None
    session_id = None
    try:
        async with await _client() as c:
            owner, tenant_id = await _register(c, "ChatOwn")

            username = _name("ChatMembr")
            token = await _csrf(c, "/app/register")
            resp = await c.post(
                "/app/register",
                data={
                    "username": username,
                    "email": f"{username}@example.test",
                    "password": PASSWORD,
                    "workspace": "member workspace",
                    "_csrf": token,
                },
            )
            assert resp.status_code == 200
            member = await User.objects.get(username=username)
            # Put the member into the owner's workspace as a plain member.
            await TenantUserManager().add_web_member(tenant_id=tenant_id, user_id=member.id, role="member")

        with tenant_scope(tenant_id):
            session = await agent_sessions.create(
                tenant_id=tenant_id,
                channel=WEB_CHANNEL,
                chat_id=str(owner.id),
            )
            session_id = session.id
            await agent_messages.append(session_id=session.id, role="user", content="Секрет владельца")

        async with await _client() as m:
            token = await _csrf(m, "/app/login")
            await m.post("/app/login", data={"username": member.username, "password": PASSWORD, "_csrf": token})
            page = await m.get("/app/chat")
            assert page.status_code == 200
            assert "Секрет владельца" not in page.text, "a member must not read another user's transcript"
    finally:
        if session_id is not None:
            with tenant_scope(bypass=True):
                await agent_sessions.delete_by_id(session_id)
        await _drop(member, tenant_id)
        await _drop(owner, tenant_id)


async def test_an_empty_question_is_a_no_op() -> None:
    """Whitespace must not open a session or reach the agent."""
    seen: list[str] = []

    async def _fake_turn(
        text: str,
        *,
        tenant_id: int,
        user_id: int,
        role_id: int | None = None,
        role_codename: str | None = None,
    ) -> str:
        seen.append(text)
        return "не должно случиться"

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.agent.runtime.handle_web_message", _fake_turn, raising=True)

        async with await _client() as client:
            user, tenant_id = await _register(client, "ChatEmpty")
            try:
                token = await _csrf(client, "/app/chat")
                resp = await client.post("/app/chat", data={"text": "   ", "_csrf": token})
                assert resp.status_code == 200
                assert resp.url.path == "/app/chat"
                assert seen == [], "a blank message must not reach the agent"

                with tenant_scope(bypass=True):
                    assert await agent_sessions.get(channel=WEB_CHANNEL, chat_id=str(user.id)) is None
            finally:
                await _drop(user, tenant_id)


async def test_a_stale_csrf_token_is_refused(client_free: None = None) -> None:
    """The POST is CSRF-guarded like every other mutation in `/app`."""
    async with await _client() as client:
        user, tenant_id = await _register(client, "ChatCsrf")
        try:
            resp = await client.post("/app/chat", data={"text": "взлом", "_csrf": "подделка"})
            assert "Сессия истекла" in resp.text
        finally:
            await _drop(user, tenant_id)
            await _drop(user, tenant_id)
