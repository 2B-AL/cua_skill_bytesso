# API Contract

The CLI talks to the AP-style CUA Skill Gateway:

```text
GET  http://10.37.98.200/skill/manifest
POST http://10.37.98.200/skill/tools/{tool}
Authorization: Bearer cua_mcp_...
Content-Type: application/json
Accept: application/json
```

## Tools Used By This Skill

| CLI command | Gateway tool |
| --- | --- |
| `ping` | `cua_get_desktop_access` plus `GET /skill/manifest` |
| `delegate` | `cua_run_task` |
| `watch` | `cua_wait_task` |
| `answer` | `cua_resume_task` |
| `cancel` | `cua_cancel_task` |
| `observe` | `cua_get_desktop_access`, optionally `cua_take_screenshot` |

## Gateway Envelope

Success:

```json
{
  "ok": true,
  "tool": "cua_run_task",
  "result": {}
}
```

Failure:

```json
{
  "ok": false,
  "tool": "cua_run_task",
  "error": {
    "code": "Unauthorized",
    "message": "invalid bearer token",
    "retryable": false
  }
}
```

## Output Mapping

The CLI maps gateway `task_id` to the existing `invocation_id` field so agents
can keep using `watch --last` and `answer --last`.
