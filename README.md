# CUA ByteSSO Skill

This repo is the ByteSSO/Access Hub variant of
[`2B-AL/cua_skill`](https://github.com/2B-AL/cua_skill). It keeps the same
install shape (`cua/` is the actual skill) but targets the bare-metal trial
environment:

- Access Hub: `http://10.37.98.200/cua-access`
- CUA Skill Gateway: `http://10.37.98.200`
- Auth credential: Access Hub CUA API token (`cua_api_...`) returned after
  browser ByteSSO login

Related repos:

- Access Hub: [`2B-AL/cua-mcp-access-hub`](https://github.com/2B-AL/cua-mcp-access-hub)
- Skill Gateway Lite: [`2B-AL/cua-skill-gateway-lite`](https://github.com/2B-AL/cua-skill-gateway-lite)
- Bare-metal CUA runtime: [`luohao.brian/my-cua`](https://code.byted.org/luohao.brian/my-cua)

## Install

Install the `cua/` subdirectory, not the repo root:

```bash
npx -y skills add 2B-AL/cua_skill_bytesso --full-depth --skill cua --agent '*' -g --copy -y
```

Verify the local install without creating a CUA task:

```bash
python3 <skill_dir>/scripts/cua.py self-test
```

## Login

```bash
python3 <skill_dir>/scripts/cua.py auth login
```

The script prints and opens an Access Hub ByteSSO login URL. Finish login in the
browser; the script polls Access Hub and stores the returned local CUA
credential in `~/.openclaw/cua-skill-bytesso/auth.json` with `0600`
permissions.

Legacy `cua_mcp_...` bearer keys can still be loaded through stdin:

```bash
printf '%s' "$CUA_MCP_KEY" | python3 <skill_dir>/scripts/cua.py auth login --bearer-key-stdin
```

Do not put bearer tokens in command lines, repo files, README examples, logs, or
chat messages.

## Use

```bash
python3 <skill_dir>/scripts/cua.py ping
python3 <skill_dir>/scripts/cua.py delegate --objective "<the user's request>"
python3 <skill_dir>/scripts/cua.py watch --last
```

Override endpoints without editing the repo:

```bash
export CUA_SKILL_ACCESS_HUB_BASE_URL=http://10.37.98.200/cua-access
export CUA_SKILL_GATEWAY_URL=http://10.37.98.200
```

## Update

```bash
npx -y skills update cua -g -y
```

If automatic update is unavailable, reinstall from this repo with the install
command above and restart the target agent session.
