import sys
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import cua_auth  # noqa: E402
from cua_util import SkillError, _next_for_error, iso_to_epoch, now_epoch  # noqa: E402


class FakeState:
    def __init__(self):
        self.saved = {}
        self.access_token_expires_at = None

    def set_tokens(self, **kwargs):
        self.saved = kwargs
        self.access_token_expires_at = kwargs["access_token_expires_at"]


class CuaAuthLoginTests(unittest.TestCase):
    def test_login_waits_on_auth_pending_and_accepts_token_response_without_status(self):
        state = FakeState()
        responses = [
            SkillError("AUTH_PENDING", "SSO login is still pending"),
            {
                "access_token": "access-token",
                "expires_in": 3600,
                "refresh_token": "refresh-token",
                "scope": "cua:read cua:invoke",
                "desktop_bound": True,
                "user": {
                    "org_id": "bytedance",
                    "user_id": "user-1",
                    "email": "user@example.com",
                },
            },
        ]

        def fake_gateway_call(method, base_url, path, body=None, **_kwargs):
            self.assertEqual(method, "POST")
            self.assertEqual(base_url, "http://gateway")
            self.assertEqual(path, "/v1/auth/device/poll")
            self.assertEqual(body, {"session_id": "sess-1"})
            value = responses.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

        with mock.patch.object(cua_auth, "gateway_call", side_effect=fake_gateway_call), \
                mock.patch.object(cua_auth.time, "sleep") as sleep:
            result = cua_auth.login(state, "http://gateway", timeout=30, session_id="sess-1")

        self.assertEqual(result["status"], "logged_in")
        self.assertEqual(result["scopes"], ["cua:read", "cua:invoke"])
        self.assertTrue(result["desktop_bound"])
        self.assertEqual(state.saved["access_token"], "access-token")
        self.assertEqual(state.saved["refresh_token"], "refresh-token")
        sleep.assert_called_once_with(3)

    def test_save_token_set_defaults_refresh_expiry_when_gateway_omits_it(self):
        state = FakeState()

        cua_auth._save_token_set(state, "http://gateway", {
            "access_token": "access-token",
            "expires_in": 900,
            "refresh_token": "refresh-token",
            "desktop_bound": True,
            "user": {"org_id": "bytedance", "user_id": "user-1"},
        })

        refresh_expires_at = iso_to_epoch(state.saved["refresh_token_expires_at"])
        self.assertGreaterEqual(refresh_expires_at, now_epoch() + cua_auth.DEFAULT_REFRESH_EXPIRES_IN_SEC - 5)


class CuaErrorHintTests(unittest.TestCase):
    def test_active_run_conflict_stops_without_followup_command(self):
        hint = _next_for_error({
            "code": "ACTIVE_RUN_CONFLICT",
            "message": "A run is already active for this desktop.",
        })

        self.assertIsNotNone(hint)
        self.assertNotIn("command", hint)
        self.assertIn("wait until the current desktop task finishes", hint["agent_hint"])
        self.assertIn("do not retry", hint["agent_hint"])

    def test_legacy_upstream_active_message_is_treated_as_conflict(self):
        hint = _next_for_error({
            "code": "UpstreamError",
            "message": "A desktop run is already active for this session.",
        })

        self.assertIsNotNone(hint)
        self.assertNotIn("command", hint)


if __name__ == "__main__":
    unittest.main()
