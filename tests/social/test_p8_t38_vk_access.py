"""Standalone unit tests of the pure policy, no app/ORM/conftest imports.

Run with stdlib unittest, not pytest with a disabled project bootstrap. Loading
this one dependency-free module by path cannot initialize settings, DB or a
provider. Surface/vault integration tests are separate required follow-up work.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "app/services/social/vk_access.py"
_SPEC = importlib.util.spec_from_file_location("p8_t38_pure_vk_access", _PATH)
policy = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = policy
_SPEC.loader.exec_module(policy)

NOW = datetime(2026, 10, 27, tzinfo=timezone.utc)
METHODS = frozenset({"wall.get"})


class VKAccessPolicyTests(unittest.TestCase):
    def setUp(self):
        self.selection = policy.VKSelection("user", 7)
        self.snapshot = policy.VKAccessSnapshot(
            tenant_id=10,
            mode="user",
            credential_id=7,
            owner_user_id=3,
            expires_at=NOW + timedelta(hours=1),
            allowed_methods=METHODS,
            granted_scopes=frozenset({"wall"}),
        )

    def decide(self, snapshot=None, **kwargs):
        args = dict(tenant_id=10, active_member_ids=frozenset({3}), required_methods=METHODS, now=NOW)
        args.update(kwargs)
        return policy.decide_vk_access(self.selection, snapshot or self.snapshot, **args)

    def test_selected_user_is_allowed_without_live_claim(self):
        decision = self.decide()
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "local_policy_allowed")
        self.assertEqual(decision.live_unverified_methods, ("wall.get",))

    def test_service_selection_is_pinned(self):
        decision = policy.decide_vk_access(
            policy.VKSelection("api"),
            policy.VKAccessSnapshot(10, "api", allowed_methods=METHODS),
            tenant_id=10, active_member_ids=frozenset(), required_methods=METHODS, now=NOW,
        )
        self.assertTrue(decision.allowed)

    def test_missing_connection_never_falls_back(self):
        result = policy.decide_vk_access(
            self.selection, None, tenant_id=10, active_member_ids=frozenset({3}), required_methods=METHODS,
        )
        self.assertEqual(result.reason, "connection_missing")
        self.assertFalse(result.allowed)

    def test_different_credential_row_does_not_replace_selected(self):
        self.assertEqual(self.decide(replace(self.snapshot, credential_id=8)).reason, "connection_selection_mismatch")

    def test_user_cannot_fall_back_to_service(self):
        self.assertEqual(self.decide(policy.VKAccessSnapshot(10, "api")).reason, "connection_selection_mismatch")

    def test_foreign_workspace_denied(self):
        self.assertEqual(self.decide(replace(self.snapshot, tenant_id=11)).reason, "workspace_mismatch")

    def test_revoked_connection_denied(self):
        self.assertEqual(self.decide(replace(self.snapshot, active=False)).reason, "connection_revoked")

    def test_membership_change_is_rechecked(self):
        self.assertTrue(self.decide().allowed)
        self.assertEqual(self.decide(active_member_ids=frozenset()).reason, "connection_owner_not_member")

    def test_boolean_member_id_is_not_an_authorized_user_id(self):
        self.assertEqual(
            self.decide(replace(self.snapshot, owner_user_id=1), active_member_ids=frozenset({True})).reason,
            "connection_owner_not_member",
        )

    def test_expiry_is_rechecked_at_boundary(self):
        self.assertTrue(self.decide().allowed)
        self.assertEqual(self.decide(now=self.snapshot.expires_at).reason, "connection_expired")

    def test_naive_legacy_timestamp_is_interpreted_as_utc(self):
        self.assertEqual(
            self.decide(replace(self.snapshot, expires_at=NOW.replace(tzinfo=None))).reason, "connection_expired"
        )

    def test_unknown_method_fails_closed_without_echoing_input(self):
        result = self.decide(required_methods=frozenset({"secret=DO_NOT_ECHO"}))
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "method_unsupported")
        self.assertNotIn("DO_NOT_ECHO", json.dumps(result.to_dict()))

    def test_empty_method_request_does_not_authorize_a_connection(self):
        self.assertEqual(self.decide(required_methods=frozenset()).reason, "method_unsupported")

    def test_unknown_provider_grants_are_not_permissions(self):
        self.assertEqual(self.decide(replace(self.snapshot, allowed_methods=None)).reason, "method_grants_unknown")

    def test_missing_method_grant_denied(self):
        self.assertEqual(self.decide(replace(self.snapshot, allowed_methods=frozenset())).reason, "method_not_granted")

    def test_missing_requested_scope_denied(self):
        self.assertEqual(self.decide(required_scopes=frozenset({"groups"})).reason, "scope_missing")
        self.assertTrue(self.decide(required_scopes=frozenset({"wall"})).allowed)

    def test_service_mode_does_not_gain_personal_search_route(self):
        methods = frozenset({"users.search"})
        result = policy.decide_vk_access(
            policy.VKSelection("api"), policy.VKAccessSnapshot(10, "api", allowed_methods=methods),
            tenant_id=10, active_member_ids=frozenset(), required_methods=methods, now=NOW,
        )
        self.assertEqual(result.reason, "method_mode_unsupported")

    def test_live_evidence_is_method_specific(self):
        snap = replace(self.snapshot, live_verified_methods=frozenset({"users.get"}))
        self.assertEqual(self.decide(snap).live_unverified_methods, ("wall.get",))
        snap = replace(snap, live_verified_methods=METHODS)
        self.assertEqual(self.decide(snap).live_unverified_methods, ())

    def test_no_concrete_workspace_fails_closed(self):
        for value in (None, 0, -1, True, "10"):
            with self.subTest(value=value):
                self.assertEqual(self.decide(tenant_id=value).reason, "workspace_required")

    def test_parser_roundtrip_and_rejects_auto_or_token_payload(self):
        for raw in ({"mode": "user", "credential_id": 7}, {"mode": "api"}):
            self.assertEqual(policy.parse_vk_selection(raw).to_dict(), raw)
        for raw in (
            {"mode": "auto"}, {"mode": "user"}, {"mode": "user", "credential_id": True},
            {"mode": "api", "credential_id": 7}, {"mode": "api", "token": "DO_NOT_ECHO"},
            {"mode": "user", "credential_id": 7, "owner_user_id": 3}, None, "api", [],
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(policy.VKSelectionError) as caught:
                    policy.parse_vk_selection(raw)
                self.assertNotIn("DO_NOT_ECHO", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
