import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import cua_auth  # noqa: E402
from cua_state import AuthState  # noqa: E402
from cua_util import SkillError  # noqa: E402


class CuaAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.auth_file = Path(self.tmpdir.name) / "auth.json"
        self.env = mock.patch.dict(
            os.environ,
            {
                "CUA_SKILL_AUTH_FILE": str(self.auth_file),
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmpdir.cleanup()

    def test_auth_status_without_key_is_logged_out(self):
        state = AuthState.load()

        result = cua_auth.auth_status(state, "http://hub", "http://mcp", online=False)

        self.assertEqual(result["status"], "logged_out")
        self.assertEqual(result["login_url"], "http://hub/mcp/setup")
        self.assertIn("auth login", result["retry_command"])

    def test_login_stores_bearer_key_after_ping_validation(self):
        state = AuthState.load()
        ping = {
            "auth": {
                "auth_type": "access_hub_mcp_key",
                "org_id": "org-1",
                "user_id": "user-1",
                "team_id": None,
                "desktop_bound": True,
            }
        }

        with mock.patch.object(cua_auth, "_read_login_token", return_value="cua_mcp_test"), \
                mock.patch.object(cua_auth, "mcp_tool_call", return_value=ping) as call, \
                mock.patch.object(cua_auth.webbrowser, "open"):
            result = cua_auth.login(state, "http://hub", "http://mcp")

        self.assertEqual(result["status"], "logged_in")
        self.assertEqual(state.bearer_key, "cua_mcp_test")
        self.assertEqual(state.user["user_id"], "user-1")
        call.assert_called_once_with("http://mcp", "cua_mcp_test", "cua_ping", {}, timeout=30)

    def test_login_rejects_non_access_hub_key(self):
        state = AuthState.load()

        with mock.patch.object(cua_auth, "_read_login_token", return_value="not-a-cua-key"), \
                self.assertRaises(SkillError) as ctx:
            cua_auth.login(state, "http://hub", "http://mcp", open_browser=False)

        self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")


if __name__ == "__main__":
    unittest.main()
