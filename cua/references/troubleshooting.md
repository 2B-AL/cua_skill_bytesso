# Troubleshooting

All errors arrive as:

```json
{ "ok": false, "action": "...", "error": { "code": "...", "message": "..." } }
```

Branch on `error.code`.

| code | cause | what to do |
| --- | --- | --- |
| `AUTH_REQUIRED` | no local CUA credential, revoked token, expired login flow, or missing auth header | run `error.retry_command`; it will print the one browser login URL to show the user |
| `FORBIDDEN` | the key is valid but not allowed for this action | tell the user they lack permission |
| `DESKTOP_NOT_BOUND` | Access Hub has not allocated a CUA desktop for the user | open Access Hub resources/setup page and allocate or contact an admin |
| `INVOCATION_NOT_FOUND` | wrong invocation id | use the id from `delegate` or run with `--last` |
| `INVOCATION_NOT_WAITING_INPUT` | `answer` was sent when CUA was not asking | run `watch` first |
| `CUA_BACKEND_UNAVAILABLE` | MCP gateway or CUA backend is unavailable | wait and retry |
| `GATEWAY_TIMEOUT` | gateway wait timed out | run `watch --last`; the task may still be running |
| `RATE_LIMITED` | too many requests | wait, then retry |
| `VALIDATION_ERROR` | bad local argument or wrong key format | fix the argument or login input |
| `NETWORK` | cannot reach Access Hub or `/skill/manifest` / `/skill/tools/{tool}` | check VPN/network and endpoint overrides |
| `INTERNAL` | unexpected protocol or server response | retry once; if it persists, collect logs |

## Common Situations

- **`Expected an Access Hub bearer token starting with 'cua_api_' or 'cua_mcp_'`**:
  rerun `auth login`; do not paste an OAuth access token or browser cookie.
- **Login page opens but the command keeps waiting**: make sure the browser
  completes the Access Hub callback page. If the flow expires, rerun
  `auth login`.
- **A URL ending in `/api/v1/skill-auth/start` was shown as the login link**:
  that is a machine API, not a browser login URL. Run `auth login` and show only
  the URL printed by that command.
- **Token worked yesterday but fails now**: it may have been revoked or the
  Access Hub HMAC secret may have rotated. Rerun `auth login`.
- **Remote MCP tools still return `401 missing authorization bearer token` after
  `auth login`**: the Agent's remote MCP client is not sending
  `Authorization: Bearer <local CUA credential>`. Configure that Agent's MCP
  transport to attach the header and reload the MCP client. If the Agent does
  not support auth headers for remote MCP, use the bundled `scripts/cua.py`
  commands instead.
- **`self-test` passes but `self-test --online` fails**: Local install is fine;
  investigate network, Skill Gateway, Access Hub key, or desktop allocation.
