---
name: cua
description: Use when the user wants to delegate a broad computer-use task to CUA through the ByteSSO Access Hub bare-metal environment, including web browsing, app use, file handling, multi-step desktop operation, progress watching, answering CUA questions, cancellation, or observing the cloud desktop state.
---

# CUA

CUA is an autonomous computer-use skill backed by a cloud desktop. Use it when
the user asks for work that is better done by operating web pages, applications,
files, dashboards, or a desktop session than by local reasoning alone.

This variant targets the ByteSSO Access Hub environment. All actions go through:

```bash
python3 <skill_dir>/scripts/cua.py <command> [options]
```

## Required Workflow

1. Check auth before real work:

   ```bash
   python3 <skill_dir>/scripts/cua.py auth status
   ```

2. If auth is missing or expired, run:

   ```bash
   python3 <skill_dir>/scripts/cua.py auth login
   ```

   Open the Access Hub setup URL, finish ByteSSO login, generate a Bearer Key,
   and paste it only into the script's hidden prompt or stdin. Never place a
   Bearer Key in chat, command-line arguments, repo files, or logs.

3. For real work, call `delegate` with the user's original objective:

   ```bash
   python3 <skill_dir>/scripts/cua.py delegate --objective "<user objective>"
   ```

4. Inspect `data.outcome`:
   - `completed`: use `data.result.text` as the authoritative final answer.
   - `in_progress`: run `next.command` or `watch --last`.
   - `needs_input`: relay `data.input_request.question` to the user, then run
     `answer`.
   - `failed` or `cancelled`: report the terminal state.

5. Do not use local browser/search/tools to finish the delegated objective after
   sending it to CUA unless the user explicitly redirects you away from CUA.

## Commands

- `auth status`, `auth login`, `auth logout`
- `ping`
- `delegate`
- `watch`
- `answer`
- `cancel`
- `observe`
- `self-test`

For exact arguments, read [commands.md](references/commands.md). For auth setup,
read [auth.md](references/auth.md). For output states, read
[outcomes.md](references/outcomes.md). For MCP contract details, read
[api-contract.md](references/api-contract.md). For errors, read
[troubleshooting.md](references/troubleshooting.md).

## Important Rules

- Pass the user's original objective directly to `delegate`; do not decompose,
  rewrite, or add hidden requirements.
- Treat progress summaries and screenshots as status signals only.
- Use `watch` to decide task completion; do not use `observe` for completion.
- Use `cancel` only when the user explicitly asks to stop.
- Keep credentials local and secret. The script stores the Access Hub Bearer Key
  under `~/.openclaw/cua-skill-bytesso/` with restrictive permissions.
