# API Contract

The CLI talks to the AP-style CUA Skill Gateway:

```text
GET  http://10.37.98.200/skill/manifest
POST http://10.37.98.200/skill/tools/{tool}
Authorization: Bearer cua_api_...
Content-Type: application/json
Accept: application/json
```

Legacy `cua_mcp_...` bearer keys are still accepted for compatibility.

## Tools Used By This Skill

| CLI command | Gateway tool |
| --- | --- |
| `ping` | `cua_get_desktop_access` plus `GET /skill/manifest` |
| `desktops list` | `cua_list_desktops` |
| `desktops allocate` | `cua_allocate_desktop` |
| `desktops use` | `cua_list_desktops` plus local state update |
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

When multiple desktops exist, gateway tools accept `desktop_id` where relevant.
`task_id` remains globally sufficient for `watch`, `answer`, `cancel`, and
result lookup; those commands do not need a desktop selector.

`cua_list_desktops` may include scheduling hints on each desktop:

```json
{
  "desktop_id": "desk-02",
  "instance_name": "win10-spice-desk-02",
  "busy": true,
  "current_task_id": "cua_task_...",
  "current_task_status": "running"
}
```

The CLI uses these hints for `delegate --auto`; they are advisory and the
gateway remains authoritative when creating the task.
