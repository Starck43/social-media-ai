"""Current digest authorization through real source, with all storage/HTTP mocked.

Direct unittest execution is owner-authorized for this PR's no-DB mock work.
Does NOT establish ORM/middleware/ASGI/PostgreSQL commit acceptance.
"""

import asyncio
import copy
import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("_digest_current_support", ROOT / "tests/digest_permission_support.py")
SUPPORT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SUPPORT)


class DigestRuntimeBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = SUPPORT.DigestEnvironment()
        self.runtime = self.env.runtime()

    async def stage(self):
        env = self.env
        actor = await env.resolve()
        self.assertIsNotNone(actor)
        with env.tenant.tenant_scope(31):
            reply, stop = await self.runtime._dispatch_or_stage(env.session, "digest_send_now", {}, "call1", actor)
        self.assertTrue(stop)
        self.assertEqual(reply, "confirm digest")
        self.assertIn("pending_confirmation", env.session.state)
        self.assertEqual(env.session.state["unrelated"], "preserve")
        env.publisher.assert_not_awaited()
        return copy.deepcopy(env.session.state["pending_confirmation"])

    async def test_membership_view_and_update_cannot_stage_or_dispatch(self):
        env = self.env
        for right in (env.action.VIEW, env.action.UPDATE):
            env.grants = frozenset({("digestrun", right)})
            actor = await env.resolve()
            with env.tenant.tenant_scope(31):
                reply, stop = await self.runtime._dispatch_or_stage(env.session, "digest_send_now", {}, "c1", actor)
            self.assertIn("нет прав", reply)
            self.assertFalse(stop)
            self.assertNotIn("pending_confirmation", env.session.state)
            env.publisher.assert_not_awaited()

    async def test_create_stages_then_confirms_once_through_real_runtime_and_registry(self):
        await self.stage()
        result = await self.env.turn(self.runtime)
        self.assertEqual(result, "model continuation mocked")
        self.env.publisher.assert_awaited_once_with(period="day", group_by="themes", time_breakdown=False)
        self.assertNotIn("pending_confirmation", self.env.session.state)
        await self.env.turn(self.runtime)
        self.env.publisher.assert_awaited_once()
        self.assertEqual(self.env.session.state["unrelated"], "preserve")

    async def test_revoke_create_after_stage_refuses_yes(self):
        await self.stage()
        self.env.grants = frozenset({("digestrun", self.env.action.VIEW)})
        self.assertIn("отклонено", await self.env.turn(self.runtime))
        self.env.publisher.assert_not_awaited()

    async def test_revoke_after_consume_before_dispatch_refuses_send(self):
        await self.stage()
        self.env.after_consume = lambda: setattr(self.env, "grants", frozenset())
        self.assertIn("права изменились", await self.env.turn(self.runtime))
        self.env.publisher.assert_not_awaited()
        self.env.session_manager.consume_pending_confirmation.assert_awaited_once()

    async def test_inactive_user_after_stage_refuses_before_consume(self):
        pending = await self.stage()
        self.env.user.is_active = False
        await self.env.turn(self.runtime)
        self.env.publisher.assert_not_awaited()
        self.env.session_manager.consume_pending_confirmation.assert_not_awaited()
        self.assertEqual(self.env.session.state["pending_confirmation"], pending)

    async def test_inactive_membership_after_stage_refuses_before_consume(self):
        await self.stage()
        self.env.membership.is_active = False
        await self.env.turn(self.runtime)
        self.env.publisher.assert_not_awaited()
        self.env.session_manager.consume_pending_confirmation.assert_not_awaited()

    async def test_missing_membership_after_stage_refuses(self):
        await self.stage()
        self.env.members.get.return_value = None
        await self.env.turn(self.runtime)
        self.env.publisher.assert_not_awaited()

    async def test_missing_bound_user_after_stage_refuses(self):
        await self.stage()
        self.env.membership.user_id = None
        await self.env.turn(self.runtime)
        self.env.publisher.assert_not_awaited()

    async def test_inactive_workspace_after_stage_refuses(self):
        await self.stage()
        self.env.workspace.is_active = False
        await self.env.turn(self.runtime)
        self.env.publisher.assert_not_awaited()

    async def test_inactive_channel_binding_after_stage_refuses(self):
        await self.stage()
        self.env.binding.is_active = False
        await self.env.turn(self.runtime)
        self.env.publisher.assert_not_awaited()

    async def test_foreign_actor_cannot_consume_the_original_pending(self):
        pending = await self.stage()
        # Fresh second actor uses the same bound group chat/session, not a fake stale identity.
        self.env.user.id = 13
        self.env.membership.id = 42
        self.env.membership.user_id = 13
        self.env.membership.external_user_id = "8"
        self.env.resolution.user_id = "8"
        self.env.inbound.user_id = "8"
        self.assertIn("отклонено", await self.env.turn(self.runtime))
        self.env.publisher.assert_not_awaited()
        self.env.session_manager.consume_pending_confirmation.assert_not_awaited()
        self.assertEqual(self.env.session.state["pending_confirmation"], pending)

    async def test_old_update_contract_refuses_yes_even_with_current_create(self):
        self.env.spec.required_permission = "digestrun.update"
        actor = await self.env.resolve()
        pending = self.env.confirmation.make_pending_intent(actor, self.env.session, self.env.spec, {}, "old1")
        self.assertIsNotNone(pending)
        self.env.session.state["pending_confirmation"] = pending
        self.env.spec.required_permission = "digestrun.create"
        self.assertIn("отклонено", await self.env.turn(self.runtime))
        self.env.publisher.assert_not_awaited()

    async def test_expired_confirmation_refuses_yes(self):
        await self.stage()
        self.env.session.state["pending_confirmation"]["expires_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
        self.assertIn("отклонено", await self.env.turn(self.runtime))
        self.env.publisher.assert_not_awaited()

    async def test_replaced_intent_cannot_be_consumed_by_observed_old_snapshot(self):
        old = await self.stage()
        await self.stage()
        self.assertIsNone(await self.env.session_manager.consume_pending_confirmation(self.env.session.id, old))
        self.assertNotEqual(old["intent_id"], self.env.session.state["pending_confirmation"]["intent_id"])
        await self.env.turn(self.runtime)
        self.env.publisher.assert_awaited_once()

    async def test_two_yes_turns_with_mock_cas_invoke_publisher_at_most_once(self):
        await self.stage()
        await asyncio.gather(self.env.turn(self.runtime), self.env.turn(self.runtime))
        self.env.publisher.assert_awaited_once()

    async def test_unconfirmed_mock_consume_acknowledgement_never_grants_dispatch(self):
        await self.stage()
        self.env.consume_error = True
        self.assertIn("сохранение не подтверждено", await self.env.turn(self.runtime))
        self.env.publisher.assert_not_awaited()

    async def test_private_loader_leaves_no_app_modules_in_global_import_table(self):
        before = {name: value for name, value in sys.modules.items() if name == "app" or name.startswith("app.")}
        SUPPORT.DigestEnvironment().runtime()
        after = {name: value for name, value in sys.modules.items() if name == "app" or name.startswith("app.")}
        self.assertEqual(before, after)


class DigestWebBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = SUPPORT.DigestEnvironment()
        self.handler = self.env.web()

    async def send(self, request=None, token=None, **kwargs):
        request = request or self.env.web_request()
        if token is None:
            token = self.env.csrf.generate_token()
        return await self.handler.digest_send_now(
            request,
            period=kwargs.get("period", "day"),
            group_by=kwargs.get("group_by", "themes"),
            time_breakdown=kwargs.get("time_breakdown", ""),
            token=token,
            tenant_id=kwargs.get("tenant_id"),
        )

    async def test_web_view_and_update_are_denied_by_real_guard_with_valid_csrf(self):
        for right in (self.env.action.VIEW, self.env.action.UPDATE):
            request = self.env.web_request(frozenset({("digestrun", right)}))
            reply = await self.send(request)
            self.assertEqual(reply.status_code, 302)
            self.assertEqual(request.session["_flashes"][-1]["kind"], "error")
            self.env.publisher.assert_not_awaited()

    async def test_web_create_dispatches_same_arguments_without_owner_or_superuser(self):
        request = self.env.web_request()
        self.assertFalse(request.state.web_perms.is_owner)
        self.assertFalse(request.state.web_perms.is_superuser)
        await self.send(request, period="week", group_by="sources", time_breakdown="true")
        self.env.publisher.assert_awaited_once_with(period="week", force=True, group_by="sources", time_breakdown=True)

    async def test_missing_and_invalid_csrf_deny_create(self):
        for token in ("", "invalid", self.env.csrf.generate_token() + "modified"):
            await self.send(token=token)
        self.env.publisher.assert_not_awaited()

    async def test_inactive_user_with_create_and_valid_csrf_is_denied(self):
        self.env.user.is_active = False
        await self.send()
        self.env.publisher.assert_not_awaited()

    async def test_refreshed_empty_member_grants_deny_previously_allowed_request(self):
        # Represent middleware's fresh result; not a test of its DB lookup.
        request = self.env.web_request(frozenset())
        await self.send(request)
        self.env.publisher.assert_not_awaited()

    async def test_foreign_posted_tenant_cannot_widen_ordinary_caller_scope(self):
        seen = []

        async def publish(**kwargs):
            seen.append(self.env.tenant.current_tenant_id())
            return {"status": "sent"}

        self.env.publisher.side_effect = publish
        await self.send(tenant_id=999)
        self.assertEqual(seen, [31])
        self.assertIsNone(self.env.tenant.current_tenant_id())

    async def test_real_csrf_rejects_signed_token_from_different_key(self):
        other = type(self.env.csrf)("different-synthetic-test-key")
        await self.send(token=other.generate_token())
        self.env.publisher.assert_not_awaited()

    async def test_web_source_loader_does_not_publish_app_modules(self):
        before = {name: value for name, value in sys.modules.items() if name == "app" or name.startswith("app.")}
        SUPPORT.DigestEnvironment().web()
        after = {name: value for name, value in sys.modules.items() if name == "app" or name.startswith("app.")}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
