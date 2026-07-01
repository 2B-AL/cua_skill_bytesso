# Authentication

The skill uses short-lived access tokens plus a rotating refresh token, obtained
through the gateway's AL OAuth Feishu member-login flow. The CLI does not accept
or send CloudIdentity `account_id`. During the current temporary rollout, the
browser login page may ask the user to enter their own CloudIdentity
`account_id`; after login the gateway resolves the user's AL organization from
`/inner/UserInfo.orgs[0].id`. You never handle raw tokens — the script does.

## Login

1. A business command (or `auth status`) returns `AUTH_REQUIRED` when there is no
   valid session. Run its `error.retry_command` (which is `auth login`).
2. `auth login` calls the gateway, prints a `login_url` and `user_code`, and
   polls. Show the `login_url` and `user_code` to the user and ask them to finish
   sign-in in a browser. If the browser page asks for `account_id`, the user
   fills it there; do not pass it through the CLI.
3. When the user approves, polling returns `status: "logged_in"` and tokens are
   cached locally.
4. If `auth login` times out before the user finishes, it returns `AUTH_REQUIRED`
   with a `retry_command` that includes `--session-id`; run it to keep polling
   the same login session.

Never ask the user to paste a token or API key. Never write an Authorization
header yourself.

## Token cache

- Location: `~/.openclaw/cua-skill/auth.json` (override with `CUA_SKILL_AUTH_FILE`).
- Directory mode `0700`, file mode `0600`; the script repairs unsafe permissions
  and refuses to continue if it cannot.
- `auth.json` holds the access token, refresh token, expiry timestamps, the API
  base URL, `desktop_bound`, and a minimal user record (org/user/email).
- `session.json` (same directory) holds only `last_invocation_id` for `--last`.

## Automatic refresh

Before each business call the script ensures a valid access token: if it is
expiring it silently refreshes using the refresh token (which the server rotates
on every use). You do not need to do anything.

## Error handling

| code | meaning | action |
| --- | --- | --- |
| `AUTH_REQUIRED` | no/invalid session | run `error.retry_command` (login), then retry |
| `TOKEN_EXPIRED` | access token expired | the script auto-refreshes; if it still fails it becomes `REFRESH_FAILED` |
| `REFRESH_FAILED` | refresh token invalid/expired/reused | run `error.retry_command` (login) again |
| `FORBIDDEN` | missing scope / not permitted | tell the user they lack permission |
| `DESKTOP_NOT_BOUND` | no CUA desktop allocated | tell the user CUA is not provisioned; contact admin |

## Logout

`auth logout` revokes the refresh token server-side and clears the local cache.
After logout the old refresh token cannot be reused.
