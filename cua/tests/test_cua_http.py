import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import cua_http  # noqa: E402
from cua_util import SkillError  # noqa: E402


class CuaHttpTests(unittest.TestCase):
    def test_structured_desktop_busy_error_keeps_diagnostics(self):
        envelope = {
            "ok": False,
            "request_id": "req-1",
            "error": {
                "code": "DESKTOP_BUSY",
                "message": "The desktop already has an active run.",
                "reason": "active desktop execution",
                "upstream_code": "active_run_conflict",
                "upstream_status": 409,
                "retryable": False,
                "context": {"active_run_id": "run-1", "desktop_id": "desk-1"},
            },
        }

        with self.assertRaises(SkillError) as ctx:
            cua_http._tool_result(envelope)

        self.assertEqual(ctx.exception.code, "DESKTOP_BUSY")
        self.assertEqual(ctx.exception.extra["request_id"], "req-1")
        self.assertEqual(ctx.exception.extra["upstream_code"], "active_run_conflict")
        self.assertEqual(ctx.exception.extra["upstream_status"], 409)
        self.assertEqual(ctx.exception.extra["context"]["active_run_id"], "run-1")

    def test_unknown_http_conflict_is_not_misreported_as_unbound_desktop(self):
        with self.assertRaises(SkillError) as ctx:
            cua_http._raise_mapped_error(None, 409, "conflict")

        self.assertEqual(ctx.exception.code, "CONFLICT")

    def test_success_result_carries_gateway_request_id(self):
        result = cua_http._tool_result({"ok": True, "request_id": "req-1", "result": {"status": "running"}})

        self.assertEqual(result["request_id"], "req-1")

    def test_operation_not_owned_maps_to_forbidden(self):
        with self.assertRaises(SkillError) as ctx:
            cua_http._tool_result(
                {
                    "ok": False,
                    "error": {
                        "code": "OperationNotOwned",
                        "message": "operation is not owned by caller",
                    },
                }
            )

        self.assertEqual(ctx.exception.code, "FORBIDDEN")


if __name__ == "__main__":
    unittest.main()
