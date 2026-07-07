# CUA ByteSSO Skill

This repo is the ByteSSO/Access Hub variant of
[`2B-AL/cua_skill`](https://github.com/2B-AL/cua_skill). It keeps the same
install shape (`cua/` is the actual skill) but targets the bare-metal trial
environment:

- Access Hub: `http://10.37.98.200/cua-access`
- CUA Skill MCP: `http://10.37.98.200/skill/mcp`
- Auth credential: Access Hub Bearer Key (`cua_mcp_...`) generated after
  ByteSSO login

Related repos:

- Access Hub: [`2B-AL/cua-mcp-access-hub`](https://github.com/2B-AL/cua-mcp-access-hub)
- Skill MCP Gateway: [`2B-AL/cua-mcp-server`](https://github.com/2B-AL/cua-mcp-server)
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

The script opens the Access Hub MCP setup page. Finish ByteSSO login in the
browser, generate an Access Hub Bearer Key, then paste it into the hidden prompt.
The key is stored in `~/.openclaw/cua-skill-bytesso/auth.json` with `0600`
permissions.

For non-interactive setup, pass the key through stdin:

```bash
printf '%s' "$CUA_MCP_KEY" | python3 <skill_dir>/scripts/cua.py auth login --bearer-key-stdin
```

Do not put Bearer Keys in command lines, repo files, README examples, logs, or
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
export CUA_SKILL_MCP_URL=http://10.37.98.200/skill/mcp
```

## Update

```bash
npx -y skills update cua -g -y
```

If automatic update is unavailable, reinstall from this repo with the install
command above and restart the target agent session.
