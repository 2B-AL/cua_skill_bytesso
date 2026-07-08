import sys
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import cua  # noqa: E402


class CuaCliTests(unittest.TestCase):
    def test_desktops_allocate_calls_gateway_tool(self):
        with (
            mock.patch.object(cua, "resolve_urls", return_value=("http://hub", "http://gateway")),
            mock.patch.object(cua.cua_auth, "ensure_bearer_key", return_value="token"),
            mock.patch.object(cua, "gateway_tool_call", return_value={"desktop": {"desktop_id": "vm-2"}}) as call,
        ):
            result = cua.cmd_desktops_allocate(
                Namespace(spec_code="s80", label="qa-run"),
                state=object(),
                session=object(),
            )

        self.assertEqual(result["data"]["desktop"]["desktop_id"], "vm-2")
        call.assert_called_once_with(
            "http://gateway",
            "token",
            "cua_allocate_desktop",
            {"spec_code": "s80", "label": "qa-run"},
            timeout=60,
        )

    def test_delegate_passes_desktop_id(self):
        class Session:
            def set_last_invocation_id(self, invocation_id):
                self.last = invocation_id

        with (
            mock.patch.object(cua, "resolve_urls", return_value=("http://hub", "http://gateway")),
            mock.patch.object(
                cua.cua_auth,
                "authorized_tool_call",
                return_value={"task_id": "task-1", "status": "running", "upstream": {}},
            ) as call,
        ):
            result = cua.cmd_delegate(
                Namespace(objective="do work", desktop_id="vm-2", wait_ms=None),
                state=object(),
                session=Session(),
            )

        self.assertEqual(result["data"]["invocation_id"], "task-1")
        call.assert_called_once()
        self.assertEqual(call.call_args.args[3], "cua_run_task")
        self.assertEqual(call.call_args.args[4], {"input": "do work", "desktop_id": "vm-2"})

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
