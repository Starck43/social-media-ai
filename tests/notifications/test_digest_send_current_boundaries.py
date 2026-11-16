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
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
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
        self.env.spec.required_permission = "notifications.digest_run.update"
        actor = await self.env.resolve()
        pending = self.env.confirmation.make_pending_intent(actor, self.env.session, self.env.spec, {}, "old1")
        self.assertIsNotNone(pending)
        self.env.session.state["pending_confirmation"] = pending
        self.env.spec.required_permission = "notifications.digest_run.create"
        self.assertIn("отклонено", await self.env.turn(self.runtime))
        self.env.publisher.assert_not_awaited()

    async def test_ai_chat_disabled_prevents_staging_even_with_digest_create(self):
        env = self.env
        env.membership.ai_chat_enabled = False
        actor = await env.resolve()
        self.assertIsNotNone(actor)
        self.assertFalse(actor.ai_chat_enabled)
        with env.tenant.tenant_scope(31):
            reply, stop = await self.runtime._dispatch_or_stage(env.session, "digest_send_now", {}, "c1", actor)
        self.assertTrue(stop)
        self.assertIn("Доступ изменился", reply)
        self.assertNotIn("pending_confirmation", env.session.state)
        env.publisher.assert_not_awaited()

    async def test_ai_chat_revoked_before_yes_never_consumes_pending(self):
        pending = await self.stage()
        self.env.membership.ai_chat_enabled = False
        self.assertIn("AI-чату не разрешён", await self.env.turn(self.runtime))
        self.env.session_manager.consume_pending_confirmation.assert_not_awaited()
        self.env.publisher.assert_not_awaited()
        self.assertEqual(self.env.session.state["pending_confirmation"], pending)

    async def test_ai_chat_revoked_during_mock_consume_never_dispatches(self):
        await self.stage()
        self.env.after_consume = lambda: setattr(self.env.membership, "ai_chat_enabled", False)
        self.assertIn("отклонено", await self.env.turn(self.runtime))
        self.env.session_manager.consume_pending_confirmation.assert_awaited_once()
        self.env.publisher.assert_not_awaited()

    async def test_workspace_role_change_requires_new_confirmation_even_with_create(self):
        await self.stage()
        self.env.workspace_role("admin")
        self.assertIn(("digestrun", self.env.action.CREATE), self.env.grants)
        self.assertIn("отклонено", await self.env.turn(self.runtime))
        # A rightful actor's rejected intent is cleared, not executed.
        self.env.session_manager.consume_pending_confirmation.assert_awaited_once()
        self.assertNotIn("pending_confirmation", self.env.session.state)
        self.env.publisher.assert_not_awaited()

    async def test_workspace_role_change_during_mock_consume_never_dispatches(self):
        await self.stage()
        self.env.after_consume = lambda: self.env.workspace_role("admin")
        self.assertIn("отклонено", await self.env.turn(self.runtime))
        self.env.session_manager.consume_pending_confirmation.assert_awaited_once()
        self.env.publisher.assert_not_awaited()

    async def test_unresolved_and_unknown_workspace_roles_cannot_resolve_identity(self):
        for role_id, role in ((None, None), (3, SimpleNamespace(id=3, codename="legacy-superuser"))):
            with self.subTest(role=role):
                self.env.membership.role_id = role_id
                self.env.membership.role = role
                self.assertIsNone(await self.env.resolve())
                await self.env.turn(self.runtime)
                self.env.publisher.assert_not_awaited()

    async def test_platform_admin_cannot_replace_viewer_workspace_digest_rights(self):
        env = self.env
        env.user.role.codename = env.enums.PlatformRoleType.ADMIN
        env.user.has_admin_access = lambda: True
        env.user.has_perm_for = lambda *args: True
        env.workspace_role("viewer")
        actor = await env.resolve()
        self.assertFalse(actor.is_owner)
        with env.tenant.tenant_scope(31):
            reply, stop = await self.runtime._dispatch_or_stage(env.session, "digest_send_now", {}, "c1", actor)
        self.assertFalse(stop)
        self.assertIn("нет прав", reply)
        self.assertNotIn("pending_confirmation", env.session.state)
        env.publisher.assert_not_awaited()

    async def test_current_workspace_presets_control_digest_staging_and_dispatch(self):
        for role in ("owner", "admin", "manager", "analyst", "viewer"):
            with self.subTest(role=role):
                self.env = SUPPORT.DigestEnvironment()
                self.runtime = self.env.runtime()
                env = self.env
                env.workspace_role(role)
                # Owner's AI admission is implicit; other allowed roles opt in.
                env.membership.ai_chat_enabled = role != "owner"
                actor = await env.resolve()
                self.assertTrue(actor.ai_chat_enabled)
                self.assertEqual(actor.workspace_role_codename, role)
                self.assertEqual(actor.is_owner, role == "owner")
                self.assertEqual(actor.member_grants, env.catalog.workspace_grants(role))
                if role in ("owner", "admin", "analyst"):
                    await self.stage()
                    await env.turn(self.runtime)
                    env.publisher.assert_awaited_once()
                else:
                    with env.tenant.tenant_scope(31):
                        reply, stop = await self.runtime._dispatch_or_stage(
                            env.session, "digest_send_now", {}, "c1", actor
                        )
                    self.assertFalse(stop)
                    self.assertIn("нет прав", reply)
                    env.publisher.assert_not_awaited()

    async def test_pre_cutover_authorization_and_permission_names_cannot_reuse_approval(self):
        for legacy in ("version2", "bare-create"):
            with self.subTest(legacy=legacy):
                self.env = SUPPORT.DigestEnvironment()
                self.runtime = self.env.runtime()
                pending = await self.stage()
                if legacy == "version2":
                    pending["authorization"]["version"] = 2
                else:
                    actor = await self.env.resolve()
                    self.env.spec.required_permission = "digestrun.create"
                    pending = self.env.confirmation.make_pending_intent(
                        actor, self.env.session, self.env.spec, {}, "old-bare"
                    )
                    self.env.spec.required_permission = "notifications.digest_run.create"
                self.env.session.state["pending_confirmation"] = pending
                self.assertIn("отклонено", await self.env.turn(self.runtime))
                self.env.publisher.assert_not_awaited()
                self.env.session_manager.consume_pending_confirmation.assert_awaited_once()
                self.assertNotIn("pending_confirmation", self.env.session.state)

    async def test_expired_confirmation_refuses_yes(self):
        await self.stage()
        self.env.session.state["pending_confirmation"]["expires_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
        self.assertIn("отклонено", await self.env.turn(self.runtime))
        self.env.publisher.assert_not_awaited()

    async def test_repeated_identical_intent_keeps_intent_id_and_stays_answerable_once(self):
        """`82555f2`: a staged confirmation is a visible question, never a twin.

        Repeating the *same* tool call with the same canonical `arguments_digest`
        is answered by the pending intent, so no second intent is minted and the
        `intent_id` is reused. Because the repeat deliberately writes nothing,
        the previously observed intent stays exactly as it was — so the CAS does
        accept that snapshot, and the single dispatch below proves a repeated
        request cannot produce a second consent: the same question is asked once
        and consumed once.
        """
        old = await self.stage()
        env = self.env
        actor = await env.resolve()
        with env.tenant.tenant_scope(31):
            reply, stop = await self.runtime._dispatch_or_stage(env.session, "digest_send_now", {}, "call1", actor)
        self.assertTrue(stop)
        self.assertIn("уже ждёт вашего ответа", reply)
        self.assertIn("confirm digest", reply)
        replacement = env.session.state["pending_confirmation"]
        self.assertEqual(old["intent_id"], replacement["intent_id"])
        self.assertEqual(old, replacement)
        await env.turn(self.runtime)
        env.publisher.assert_awaited_once()
        self.assertNotIn("pending_confirmation", env.session.state)

    async def test_changed_arguments_mint_a_new_intent_and_retire_the_old_one(self):
        """A *changed* intent is a new question: new `intent_id`, old one dead.

        This is the other half of `82555f2`: dedup must key on the canonical
        arguments, not on the tool name, otherwise an edited request would be
        silently answered with the previous question.
        """
        env = self.env
        old = await self.stage()
        actor = await env.resolve()
        with env.tenant.tenant_scope(31):
            _, stop = await self.runtime._dispatch_or_stage(
                env.session, "digest_send_now", {"period": "week"}, "call2", actor
            )
        self.assertTrue(stop)
        replacement = env.session.state["pending_confirmation"]
        self.assertNotEqual(old["intent_id"], replacement["intent_id"])
        self.assertNotEqual(old["arguments_digest"], replacement["arguments_digest"])
        self.assertIsNone(await env.session_manager.consume_pending_confirmation(env.session.id, old))
        env.publisher.assert_not_awaited()

    async def test_identical_repeat_does_not_revive_an_expired_intent(self):
        """Dedup must never report an unanswerable intent as "already waiting".

        `82555f2` answers a repeated identical request instead of staging a twin.
        Its first implementation compared name + `arguments_digest` only, so an
        expired intent was also answered with «уже ждёт вашего ответа» — and the
        owner's following «да» was then rejected by `pending_rejection`
        (`expired_intent`), the silent dead end the same fix set out to remove.
        An expired intent is not a live one: the repeat stages a fresh question.
        """
        env = self.env
        old = await self.stage()
        env.session.state["pending_confirmation"]["expires_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
        expired = copy.deepcopy(env.session.state["pending_confirmation"])
        actor = await env.resolve()
        with env.tenant.tenant_scope(31):
            reply, stop = await self.runtime._dispatch_or_stage(env.session, "digest_send_now", {}, "call3", actor)
        self.assertTrue(stop)
        self.assertNotIn("уже ждёт", reply)
        fresh = env.session.state["pending_confirmation"]
        self.assertNotEqual(fresh["intent_id"], expired["intent_id"])
        self.assertIsNone(await env.session_manager.consume_pending_confirmation(env.session.id, expired))
        # The freshly staged intent is answerable: «да» dispatches exactly once.
        self.assertNotIn("отклонено", await env.turn(self.runtime))
        env.publisher.assert_awaited_once()

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

    async def test_platform_admin_viewer_cannot_send_or_widen_posted_workspace(self):
        env = self.env
        env.user.role.codename = env.enums.PlatformRoleType.ADMIN
        env.user.has_admin_access = lambda: True
        env.user.has_perm_for = lambda *args: True
        env.workspace_role("viewer")
        request = env.web_request()
        self.assertFalse(request.state.web_perms.is_superuser)
        reply = await self.send(request, tenant_id=999)
        self.assertEqual(reply.status_code, 302)
        self.assertEqual(request.session["_flashes"][-1]["kind"], "error")
        self.assertEqual(env.webdeps.action_tenant_id(request, 999), 31)
        env.publisher.assert_not_awaited()

    async def test_current_workspace_presets_control_web_digest_send(self):
        for role in ("owner", "admin", "manager", "analyst", "viewer"):
            with self.subTest(role=role):
                self.env = SUPPORT.DigestEnvironment()
                self.handler = self.env.web()
                self.env.workspace_role(role)
                request = self.env.web_request()
                await self.send(request)
                if role in ("owner", "admin", "analyst"):
                    self.env.publisher.assert_awaited_once()
                else:
                    self.assertEqual(request.session["_flashes"][-1]["kind"], "error")
                    self.env.publisher.assert_not_awaited()

    async def test_ai_chat_flag_is_not_a_web_digest_permission(self):
        self.env.workspace_role("analyst")
        self.env.membership.ai_chat_enabled = False
        request = self.env.web_request()
        self.assertFalse(request.state.web_perms.can_open_chat)
        await self.send(request)
        self.env.publisher.assert_awaited_once()

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
