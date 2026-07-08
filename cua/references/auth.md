# Auth

This skill uses the bare-metal Access Hub ByteSSO flow.

## Flow

1. Run:

   ```bash
   python3 <skill_dir>/scripts/cua.py auth login
   ```

2. The script opens:

   ```text
   http://10.37.98.200/cua-access/mcp/setup
   ```

3. The user finishes ByteSSO login in the browser.
4. Access Hub allocates or resolves the user's CUA desktop.
5. The user generates an Access Hub Bearer Key that starts with `cua_mcp_`.
6. The user pastes that key into the script's hidden prompt.
7. The script validates the key with read-only `cua_get_desktop_access` and stores it in:

   ```text
   ~/.openclaw/cua-skill-bytesso/auth.json
   ```

The script uses `Authorization: Bearer <cua_mcp_...>` for all calls to the CUA
Skill Gateway.

## Non-Interactive Setup

Use stdin, not a command-line argument:

```bash
printf '%s' "$CUA_MCP_KEY" | python3 <skill_dir>/scripts/cua.py auth login --bearer-key-stdin
```

The environment variable `CUA_SKILL_BEARER_KEY` is also supported for temporary
sessions. Prefer the local encrypted/permissioned Agent config or the CLI cache
over long-lived shell environment variables.

## Endpoint Overrides

```bash
export CUA_SKILL_ACCESS_HUB_BASE_URL=http://10.37.98.200/cua-access
export CUA_SKILL_GATEWAY_URL=http://10.37.98.200
```

The bundled defaults live in `config.json`.

## Security Rules

- Do not paste Bearer Keys into chat.
- Do not pass Bearer Keys as command-line arguments.
- Do not commit Bearer Keys, API Keys, SSO secrets, or internal tokens.
- Do not log full request headers.
- Run `auth logout` before handing a machine to another user.
