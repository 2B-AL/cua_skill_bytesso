# Gateway REST API contract

The skill talks to the CUA Skill Gateway over HTTPS JSON. You never call this
directly — `scripts/cua.py` does. This is reference only.

Base URL comes from `config.json`, `CUA_SKILL_API_BASE_URL`, or `--api-base-url`.
All paths are under `/v1`.

## Unified envelope

Success:

```json
{ "ok": true, "request_id": "req_...", "data": { }, "error": null }
```

Error:

```json
{ "ok": false, "request_id": "req_...", "data": null,
  "error": { "code": "TOKEN_EXPIRED", "message": "Access token expired.", "retryable": true } }
```

`ACTIVE_RUN_CONFLICT` is a 409 admission result, not a started task. The gateway
may include diagnostic fields such as `conflict_scope`, `active_run`,
`active_task`, `active_task_id`, or `allowed_actions`; these fields are
sanitized context for explaining the conflict. Do not treat them as a command to
probe automatically. Tell the user to wait unless they explicitly ask to inspect
or cancel the existing task.

## Endpoints

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/v1/manifest` | none | capability declaration |
| `POST` | `/v1/auth/device/start` | none | begin browser login → `session_id`, `user_code`, `login_url`, `expires_at`, `interval` |
| `POST` | `/v1/auth/device/poll` | none | poll → `{status: pending}` or the token set |
| `POST` | `/v1/auth/refresh` | refresh token (body) | rotate + return a new token set |
| `GET` | `/v1/auth/me` | access token | identity + scopes |
| `POST` | `/v1/auth/logout` | refresh token (body) | revoke session |
| `GET` | `/v1/ping` | access token | auth + desktop-binding check |
| `GET` | `/v1/model-config` | access token | read bound desktop default model config |
| `POST` | `/v1/model-config` | access token | set bound desktop default model config (`{main_model, reasoning_effort}`) |
| `POST` | `/v1/invocations` | access token | delegate (`{objective, wait_ms}`) |
| `GET` | `/v1/invocations/{id}` | access token | current invocation state |
| `POST` | `/v1/invocations/{id}/watch` | access token | wait for next state (`{wait_ms}`) |
| `POST` | `/v1/invocations/{id}/answer` | access token | submit answer (`{answer, wait_ms}`) |
| `POST` | `/v1/invocations/{id}/cancel` | access token | request cancellation |
| `GET` | `/v1/desktop/access` | access token | temporary desktop access URL (default desktop) |
| `POST` | `/v1/desktop/access/revoke` | access token | revoke a temporary desktop access ticket (`{ticket}` or `{access_url}`) |
| `GET` | `/v1/desktop/screenshot` | access token | screenshot of the default desktop |
| `POST` | `/v1/desktop/reboot` | access token | reboot the caller's bound desktop (`{desktop_id?, idempotency_key?}`) |
| `POST` | `/v1/desktop/reset` | access token | reset the caller's bound desktop (`{desktop_id?, confirm:true, idempotency_key?}`) |
| `GET` | `/v1/desktop/operations/{id}` | access token | lifecycle operation status |
| `GET` | `/v1/invocations/{id}/desktop/access` | access token | access URL for the invocation's desktop |
| `GET` | `/v1/invocations/{id}/desktop/screenshot` | access token | screenshot of the invocation's desktop |
| `GET` | `/v1/diagnostics` | access token | reachability + desktop binding summary |
| `GET` | `/v1/desktop-options` | access token | selectable desktops (`id`, `name`, `ready`) |
| `POST` | `/v1/tasks` | access token | start a task (`{objective, desktop?, title?, context_id?, disable_ask_user?, wait_ms?}`) |
| `GET` | `/v1/tasks/{id}` | access token | task state |
| `GET` | `/v1/tasks/{id}/result` | access token | authoritative task result |
| `GET` | `/v1/tasks/{id}/artifacts` | access token | task artifacts |
| `POST` | `/v1/tasks/{id}/answer` | access token | answer (`{answer, wait_ms}`) |
| `POST` | `/v1/tasks/{id}/cancel` | access token | cancel |
| `GET` | `/v1/contexts` | access token | list contexts |
| `POST` | `/v1/contexts` | access token | create context (`{title?, desktop?}`) |
| `GET` | `/v1/contexts/{id}` | access token | context summary |
| `POST` | `/v1/contexts/{id}/notes` | access token | add a note (`{text}`) |
| `POST` | `/v1/contexts/{id}/tasks` | access token | continue (`{objective, wait_ms}`) |
| `GET` | `/v1/contexts/{id}/timeline` | access token | conversation timeline |
| `GET` | `/v1/schedules` | access token | list scheduled tasks |
| `POST` | `/v1/schedules/once` | access token | one-off (`{goal, run_at, ...}`) |
| `POST` | `/v1/schedules/recurring` | access token | recurring (`{goal, start_at, interval_hours, ...}`) |
| `GET` | `/v1/schedules/{id}` | access token | schedule status |
| `GET` | `/v1/schedules/{id}/history` | access token | executions + results |
| `POST` | `/v1/schedules/{id}/stop` | access token | stop future triggers |
| `DELETE` | `/v1/schedules/{id}` | access token | delete schedule |
| `GET` | `/v1/artifacts/{id}/content?task_id={task_id}` | access token | raw artifact bytes; legacy JSON/base64 may be accepted during migration only |

The gateway owns all platform `/api/**` calls (desktops, sessions, runs,
scheduled-tasks, artifacts) behind these stable semantic routes; the skill never
touches the platform directly.

## Token set (device poll / refresh `data`)

```json
{
  "status": "authorized",
  "access_token": "<jwt>",
  "expires_in": 900,
  "refresh_token": "<opaque>",
  "refresh_expires_in": 2592000,
  "user": { "subject": "org:user", "org_id": "org_x", "user_id": "user_x", "email": "u@bytedance.com" },
  "scopes": ["cua:read", "cua:invoke", "cua:observe", "cua:cancel"],
  "desktop_bound": true
}
```

## Auth model

- Login: AL OAuth Feishu member login in the browser. `device/start` returns a
  MemberFeishuLogin `login_url`; after the user signs in, the gateway exchanges
  the authorization code with PKCE, reads `/inner/UserInfo`, and uses
  `orgs[0].id`; `device/poll` then returns CUA tokens.
- Access token: short-lived signed JWT (HS256), sent as `Authorization: Bearer`.
- Refresh token: opaque, stored server-side only as a salted hash, rotated on
  every use; reuse of a rotated token revokes the whole session.
- Each business request is verified server-side (signature, expiry, active
  session, scope) before reaching CUA.

## Privacy

The gateway does not persist the user's objective, answers, final result text,
process traces, screenshots, full desktop access URLs, or artifact contents.
