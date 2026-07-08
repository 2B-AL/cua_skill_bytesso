import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import cua  # noqa: E402


class CuaCliTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
