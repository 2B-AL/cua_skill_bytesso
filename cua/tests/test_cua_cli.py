import sys
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import cua  # noqa: E402


class CuaCliTests(unittest.TestCase):
    class Session:
        default_desktop_id = None

        def __init__(self):
            self.last = None
            self.last_desktop = None
            self.desktops = []

        def set_last_invocation_id(self, invocation_id):
            self.last = invocation_id

        @property
        def last_invocation_id(self):
            return self.last

        def set_last_task_desktop_id(self, desktop_id):
            self.last_desktop = desktop_id

        def remember_desktops(self, desktops):
            self.desktops = desktops

    def test_desktops_allocate_calls_gateway_tool(self):
        session = self.Session()
        with (
            mock.patch.object(cua, "resolve_urls", return_value=("http://hub", "http://gateway")),
            mock.patch.object(cua.cua_auth, "ensure_bearer_key", return_value="token"),
            mock.patch.object(cua, "gateway_tool_call", return_value={"desktop": {"desktop_id": "vm-2"}}) as call,
        ):
            result = cua.cmd_desktops_allocate(
                Namespace(spec_code="s80", label="qa-run"),
                state=object(),
                session=session,
            )

        self.assertEqual(result["data"]["desktop"]["desktop_id"], "vm-2")
        self.assertEqual(session.desktops, [{"desktop_id": "vm-2"}])
        call.assert_called_once_with(
            "http://gateway",
            "token",
            "cua_allocate_desktop",
            {"spec_code": "s80", "label": "qa-run"},
            timeout=60,
        )

    def test_delegate_passes_desktop_id(self):
        session = self.Session()
        with (
            mock.patch.object(cua, "resolve_urls", return_value=("http://hub", "http://gateway")),
            mock.patch.object(
                cua.cua_auth,
                "authorized_tool_call",
                return_value={"task_id": "task-1", "status": "running", "upstream": {}},
            ) as call,
        ):
            result = cua.cmd_delegate(
                Namespace(objective="do work", desktop_id="vm-2", auto=False, wait_ms=None),
                state=object(),
                session=session,
            )

        self.assertEqual(result["data"]["invocation_id"], "task-1")
        self.assertEqual(session.last_desktop, "vm-2")
        call.assert_called_once()
        self.assertEqual(call.call_args.args[3], "cua_run_task")
        self.assertEqual(call.call_args.args[4], {"input": "do work", "desktop_id": "vm-2"})

    def test_auto_delegate_selects_idle_desktop(self):
        session = self.Session()
        with (
            mock.patch.object(cua, "resolve_urls", return_value=("http://hub", "http://gateway")),
            mock.patch.object(cua.cua_auth, "ensure_bearer_key", return_value="token"),
            mock.patch.object(
                cua,
                "gateway_tool_call",
                return_value={
                    "quota": {"max_active_cuas": 5, "active_count": 2},
                    "desktops": [
                        {"desktop_id": "vm-1", "busy": True, "current_task_id": "task-busy"},
                        {"desktop_id": "vm-2", "busy": False},
                    ],
                },
            ),
            mock.patch.object(
                cua.cua_auth,
                "authorized_tool_call",
                return_value={"task_id": "task-1", "status": "running", "upstream": {}},
            ) as call,
        ):
            result = cua.cmd_delegate(
                Namespace(objective="do work", desktop_id=None, auto=True, wait_ms=None),
                state=object(),
                session=session,
            )

        self.assertEqual(result["data"]["invocation_id"], "task-1")
        self.assertEqual(call.call_args.args[4], {"input": "do work", "desktop_id": "vm-2"})

    def test_tasks_list_calls_gateway_tool(self):
        session = self.Session()
        with (
            mock.patch.object(cua, "resolve_urls", return_value=("http://hub", "http://gateway")),
            mock.patch.object(cua.cua_auth, "ensure_bearer_key", return_value="token"),
            mock.patch.object(cua, "gateway_tool_call", return_value={"tasks": [], "count": 0}) as call,
        ):
            result = cua.cmd_tasks_list(
                Namespace(status="active", limit=20),
                state=object(),
                session=session,
            )

        self.assertEqual(result["data"]["count"], 0)
        call.assert_called_once_with(
            "http://gateway",
            "token",
            "cua_list_tasks",
            {"status": "active", "limit": 20},
            timeout=30,
        )

    def test_tasks_watch_normalizes_multiple_results(self):
        session = self.Session()
        with (
            mock.patch.object(cua, "resolve_urls", return_value=("http://hub", "http://gateway")),
            mock.patch.object(cua.cua_auth, "ensure_bearer_key", return_value="token"),
            mock.patch.object(
                cua,
                "gateway_tool_call",
                return_value={
                    "tasks": [
                        {
                            "task": {"task_id": "task-1", "status": "succeeded", "mycua_run_id": "run-1"},
                            "upstream": {"status": "succeeded", "text": "done 1"},
                        },
                        {
                            "task": {"task_id": "task-2", "status": "running", "mycua_run_id": "run-2"},
                            "upstream": {"run": {"status": "running"}},
                        },
                    ],
                    "count": 2,
                    "completed_count": 1,
                    "failed_count": 0,
                    "cancelled_count": 0,
                    "needs_input_count": 0,
                    "pending_count": 1,
                    "terminal_count": 1,
                    "settled_count": 1,
                },
            ) as call,
        ):
            result = cua.cmd_tasks_watch(
                Namespace(task_id=["task-1", "task-2"], last=False, wait_ms=1000, include_upstream=False),
                state=object(),
                session=session,
            )

        self.assertEqual(result["data"]["count"], 2)
        self.assertEqual(result["data"]["completed_count"], 1)
        self.assertEqual(result["data"]["pending_count"], 1)
        self.assertEqual(result["data"]["terminal_count"], 1)
        self.assertEqual(result["data"]["settled_count"], 1)
        self.assertEqual(result["data"]["tasks"][0]["outcome"], "completed")
        self.assertEqual(result["data"]["tasks"][0]["result"]["text"], "done 1")
        self.assertEqual(result["data"]["tasks"][1]["outcome"], "in_progress")
        self.assertEqual(session.last, "task-2")
        call.assert_called_once_with(
            "http://gateway",
            "token",
            "cua_watch_tasks",
            {"task_ids": ["task-1", "task-2"], "include_upstream": False, "timeout_seconds": 1},
            timeout=31,
        )

    def test_task_envelope_reads_mycua_final_text(self):
        payload = {
            "task_id": "cua_task_1",
            "status": "succeeded",
            "upstream": {
                "runId": "run_1",
                "status": "succeeded",
                "finalText": "finished answer",
                "artifacts": [],
            },
        }

        envelope = cua._task_envelope(payload)

        self.assertEqual(envelope["outcome"], "completed")
        self.assertEqual(envelope["result"]["text"], "finished answer")

    def test_task_envelope_includes_upstream_error_diagnostics(self):
        payload = {
            "task": {"task_id": "cua_task_1", "status": "error", "mycua_run_id": "run_1"},
            "upstream": {
                "status": "error",
                "error": "my-cua run_status request failed",
                "upstream_status": 404,
            },
        }

        envelope = cua._task_envelope(payload)

        self.assertEqual(envelope["outcome"], "failed")
        self.assertIsNone(envelope["result"]["text"])
        self.assertEqual(envelope["diagnostics"]["error"], "my-cua run_status request failed")
        self.assertEqual(envelope["diagnostics"]["upstream_status"], 404)

    def test_task_envelope_normalizes_artifacts(self):
        payload = {
            "task_id": "cua_task_1",
            "status": "succeeded",
            "upstream": {
                "finalText": "done",
                "artifacts": [
                    {
                        "id": "art_image",
                        "kind": "screenshot",
                        "mimeType": "image/png",
                        "url": "/api/artifacts/art_image",
                        "width": 100,
                        "height": 80,
                        "sizeBytes": 1234,
                        "storageStatus": "ready",
                        "meta": {"title": "screen"},
                    },
                    {
                        "id": "art_text",
                        "kind": "file",
                        "mimeType": "text/markdown",
                        "filePath": "C:/Users/user/Desktop/result.md",
                        "contentText": "# Result",
                    },
                ],
                "output": {
                    "artifacts": [
                        {
                            "id": "art_file",
                            "kind": "file",
                            "mimeType": "application/pdf",
                            "name": "report.pdf",
                            "downloadUrl": "/api/artifacts/art_file",
                        }
                    ]
                },
            },
        }

        artifacts = cua._task_envelope(payload)["result"]["artifacts"]

        self.assertEqual(len(artifacts), 3)
        self.assertEqual(artifacts[0]["type"], "image")
        self.assertEqual(artifacts[0]["mime_type"], "image/png")
        self.assertEqual(artifacts[0]["size_bytes"], 1234)
        self.assertEqual(artifacts[0]["title"], "screen")
        self.assertEqual(artifacts[1]["type"], "text")
        self.assertEqual(artifacts[1]["path"], "C:/Users/user/Desktop/result.md")
        self.assertEqual(artifacts[1]["text"], "# Result")
        self.assertEqual(artifacts[2]["type"], "file")
        self.assertEqual(artifacts[2]["name"], "report.pdf")
        self.assertEqual(artifacts[2]["url"], "/api/artifacts/art_file")


if __name__ == "__main__":
    unittest.main()
