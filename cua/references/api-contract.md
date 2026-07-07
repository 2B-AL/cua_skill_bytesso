# API Contract

The CLI talks to the CUA Skill MCP endpoint:

```text
POST http://10.37.98.200/skill/mcp
Authorization: Bearer cua_mcp_...
Content-Type: application/json
Accept: application/json, text/event-stream
```

The MCP protocol version used for initialize is `2025-06-18`.

## Tools

### `cua_ping`

Input:

```json
{}
```

Output:

```json
{
  "ok": true,
  "server": { "name": "cua-skill", "version": "0.1.0" },
  "auth": {
    "authenticated": true,
    "auth_type": "access_hub_mcp_key",
    "org_id": "...",
    "user_id": "...",
    "team_id": null,
    "desktop_bound": true
  },
  "agent_hint": "..."
}
```

### `cua_delegate`

Input:

```json
{ "objective": "user objective", "wait_ms": null }
```

### `cua_watch`

Input:

```json
{ "invocation_id": "cua_inv_...", "wait_ms": null }
```

### `cua_answer`

Input:

```json
{ "invocation_id": "cua_inv_...", "answer": "user answer", "wait_ms": null }
```

### `cua_cancel`

Input:

```json
{ "invocation_id": "cua_inv_..." }
```

### `cua_observe`

Input:

```json
{ "invocation_id": null, "include_screenshot": false }
```

## Unified Envelope

`delegate`, `watch`, and `answer` return:

```json
{
  "invocation_id": "cua_inv_...",
  "outcome": "in_progress",
  "result": { "text": null, "artifacts": [] },
  "input_request": null,
  "progress": { "summary": "...", "step_count": 1, "updated_at": "..." },
  "next_action": { "type": "watch", "agent_hint": "..." },
  "diagnostics": { "trace_id": null }
}
```

## Config Keys

`config.json` supports:

```json
{
  "access_hub_base_url": "http://10.37.98.200/cua-access",
  "skill_mcp_url": "http://10.37.98.200/skill/mcp",
  "mcp_server_name": "cua_skill_v2",
  "mcp_transport": "MCP Streamable HTTP"
}
```
