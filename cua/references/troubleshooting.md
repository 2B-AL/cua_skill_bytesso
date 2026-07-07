# Troubleshooting

All errors arrive as:

```json
{ "ok": false, "action": "...", "error": { "code": "...", "message": "..." } }
```

Branch on `error.code`.

| code | cause | what to do |
| --- | --- | --- |
| `AUTH_REQUIRED` | no Bearer Key, revoked key, or missing auth header | run `error.retry_command`, finish ByteSSO in Access Hub, generate a key, then retry |
| `FORBIDDEN` | the key is valid but not allowed for this action | tell the user they lack permission |
| `DESKTOP_NOT_BOUND` | Access Hub has not allocated a CUA desktop for the user | open Access Hub resources/setup page and allocate or contact an admin |
| `INVOCATION_NOT_FOUND` | wrong invocation id | use the id from `delegate` or run with `--last` |
| `INVOCATION_NOT_WAITING_INPUT` | `answer` was sent when CUA was not asking | run `watch` first |
| `CUA_BACKEND_UNAVAILABLE` | MCP gateway or CUA backend is unavailable | wait and retry |
| `GATEWAY_TIMEOUT` | gateway wait timed out | run `watch --last`; the task may still be running |
| `RATE_LIMITED` | too many requests | wait, then retry |
| `VALIDATION_ERROR` | bad local argument or wrong key format | fix the argument or login input |
| `NETWORK` | cannot reach Access Hub or `/skill/mcp` | check VPN/network and endpoint overrides |
| `INTERNAL` | unexpected protocol or server response | retry once; if it persists, collect logs |

## Common Situations

- **`Expected an Access Hub Bearer Key starting with 'cua_mcp_'`**: The v1
  ByteSSO flow expects the legacy/direct Access Hub Bearer Key, not an OAuth
  access token.
- **Login page opens but no key is shown**: Finish ByteSSO, go to the MCP setup
  page, and click generate Bearer Key.
- **Key worked yesterday but fails now**: It may have been revoked or the Access
  Hub HMAC secret may have rotated. Generate a new key.
- **`self-test` passes but `self-test --online` fails**: Local install is fine;
  investigate network, `/skill/mcp`, Access Hub key, or desktop allocation.
