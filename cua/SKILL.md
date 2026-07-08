---
name: cua
description: Use when the user wants to delegate one or more broad computer-use tasks to CUA through the ByteSSO Access Hub bare-metal environment, including web browsing, app use, file handling, multi-step desktop operation, allocating multiple CUA desktops, running independent tasks in parallel, progress watching, answering CUA questions, cancellation, or observing the cloud desktop state.
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

   Do not ask the user to run this command. Run it yourself, show the single
   ByteSSO browser login URL printed by the command, wait for the user to finish
   login, and let the command store the returned local CUA credential. Never
   tell the user to open `skill-auth/start`, the Access Hub root URL, or other
   Access Hub API endpoints directly. Never place bearer tokens in chat,
   command-line arguments, repo files, or logs.

3. To inspect or allocate desktops, use the local CLI instead of guessing:

   ```bash
   python3 <skill_dir>/scripts/cua.py desktops list
   python3 <skill_dir>/scripts/cua.py desktops allocate --label "<optional label>"
   python3 <skill_dir>/scripts/cua.py desktops use <desktop_id>
   ```

   Use `desktops list` before selecting a desktop for a QA task when multiple
   desktops may exist. Use `desktops allocate` only when the user asks for a new
   CUA instance or no suitable existing desktop is available. Use `desktops use`
   to set a local default desktop for later `observe` and `delegate` calls.
   Quota is enforced by the gateway.

4. Choose single or parallel delegation:

   - Use one CUA for dependent steps or shared browser/app/session state.
   - Use multiple CUAs for independent subtasks whose results can be merged.
   - A user may have several allocated CUA desktop instances, subject to quota.
     Allocate additional desktops when independent work would benefit from
     parallel execution and no idle allocated desktop is available.
   - When splitting, preserve the user's intent and make each subtask
     self-contained.

   For a parallel request such as "check tomorrow's Beijing weather, find hot
   Beijing attractions, and check this year's admissions for famous Beijing
   universities", run `desktops list`, allocate or select idle desktops as
   needed, then start one `delegate` per independent subtask using different
   `--desktop-id` values. Keep all returned invocation ids and use one
   `tasks watch --task-id ... --task-id ...` call to collect results before
   composing the final answer.

5. If the user only asks for their CUA/cloud desktop link, call `observe` after
   auth is ready. Pass `--desktop-id` if the user or prior `desktops list`
   selected a specific desktop:

   ```bash
   python3 <skill_dir>/scripts/cua.py observe
   python3 <skill_dir>/scripts/cua.py observe --desktop-id <desktop_id>
   ```

   Return the temporary desktop access URL from the command output. Do not ask
   the user to run `observe`.

6. For real work, call `delegate` with the user's original objective or one
   independent subtask. If a specific desktop was selected, pass `--desktop-id`.
   By default this starts a CUA task and returns quickly; do not block the chat
   waiting for completion unless the user explicitly asks you to wait for the
   result:

   ```bash
   python3 <skill_dir>/scripts/cua.py delegate --objective "<user objective>"
   python3 <skill_dir>/scripts/cua.py delegate --desktop-id <desktop_id> --objective "<user objective>"
   python3 <skill_dir>/scripts/cua.py delegate --auto --objective "<user objective>"
   ```

   `--auto` chooses an idle desktop or allocates one if quota allows. Do not use
   `--auto` if the user explicitly named a desktop. For deliberate parallel
   execution, prefer choosing concrete idle `desktop_id` values from
   `desktops list` so each subtask is bound to a different CUA instance.

7. Track running tasks with task commands when multiple CUA tasks may be active:

   ```bash
   python3 <skill_dir>/scripts/cua.py tasks list
   python3 <skill_dir>/scripts/cua.py tasks watch --task-id <id> --task-id <id> --wait-ms 60000
   ```

   Use `tasks list` to recover task ids and statuses. Use `tasks watch` to
   refresh or wait on several task ids in one call. For a single task,
   `watch --last` remains a shortcut.

8. Inspect `data.outcome` on single-task responses, or `outcome` on each item in
   `data.tasks` for `tasks watch`:
   - `completed`: use `result.text` from that response or task item as the
     authoritative final answer.
     If artifacts are present, mention useful `text`, `image`, or `file`
     artifact names, URLs, or paths. Treat `browser_snapshot` artifacts as
     evidence only.
   - `in_progress`: keep the task id, report that CUA accepted the work, and
     call `tasks watch` or `watch --last` later when the user asks for status or
     result.
   - `needs_input`: relay `input_request.question` to the user, then run
     `answer`.
   - `failed` or `cancelled`: report the terminal state and include useful
     `diagnostics.error` or `diagnostics.upstream_status` when present.

9. Do not use local browser/search/tools to finish the delegated objective after
   sending it to CUA unless the user explicitly redirects you away from CUA.

## Commands

- `auth status`, `auth login`, `auth logout`
- `ping`
- `desktops list`, `desktops allocate`, `desktops use`
- `tasks list`, `tasks watch`
- `delegate`
- `watch`
- `answer`
- `cancel`
- `observe`
- `self-test`

For exact arguments, read [commands.md](references/commands.md). For auth setup,
read [auth.md](references/auth.md). For output states, read
[outcomes.md](references/outcomes.md). For Skill Gateway contract details, read
[api-contract.md](references/api-contract.md). For errors, read
[troubleshooting.md](references/troubleshooting.md).

## Important Rules

- Pass the user's original objective directly to `delegate` for single-task
  work. For independent parallel work, split only along explicit user goals and
  pass each CUA a self-contained subtask without changing requirements or adding
  hidden work.
- Treat progress summaries and screenshots as status signals only.
- Use `watch` or `tasks watch` to decide task completion; do not use `observe`
  for completion.
- Use `cancel` only when the user explicitly asks to stop.
- Keep credentials local and secret. The script stores the Access Hub CUA
  credential under `~/.openclaw/cua-skill-bytesso/` with restrictive
  permissions.
