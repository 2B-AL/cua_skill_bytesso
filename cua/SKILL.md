---
name: cua
description: Delegate a broad computer-use task to CUA — an autonomous agent that operates an authenticated cloud desktop (web browsing, app use, file handling, multi-step workflows). Use when the user wants work done by operating a computer rather than by local reasoning. Drives everything through scripts/cua.py; no MCP, curl, or tokens required.
---

# CUA Skill

CUA runs the user's task on an authenticated cloud desktop and reports back. You
drive it through ONE script. Do not call the gateway HTTP API directly, do not
ask the user for a token or API key, and never print tokens.

## The only command surface

```bash
python3 <skill_dir>/scripts/cua.py <command> [options]
```

Every call prints ONE JSON object. Parse it. On success `"ok": true` with a
`data` object and usually a `next` block. On failure `"ok": false` with
`error.code` and often `error.retry_command`.

Zero-config: the gateway URL is baked into the skill (`config.json`). The only
one-time step is login, which the workflow triggers for you. (Advanced override:
`--api-base-url <url>` or `CUA_SKILL_API_BASE_URL`.)

## Fixed workflow — follow in order

1. **Check auth**: run `auth status`.
   - If it returns `AUTH_REQUIRED`, run the command in `error.retry_command`
     (this is `auth login`). Show the user the `login_url` and `user_code` it
     prints, and wait for `status: "logged_in"`. Never ask for a token.
2. **Delegate**: `delegate --objective "<the user's original request>"`.
   - Pass the user's request as-is. Do NOT plan, decompose, or add constraints.
   - It returns almost immediately with `data.invocation_id` and
     `outcome: in_progress`. Note `data.invocation_id`. Do NOT call `delegate`
     again for the same request — that starts a second task.
3. **Drive the outcome** in `data.outcome`:
   - `in_progress` → run `next.command` (a `watch`). Each `watch` returns quickly
     (~20s); just call it again while it stays `in_progress`. For a long task you
     can instead run `result --invocation-id <id>` once to block until it
     finishes. Do NOT cancel just because it is slow.
   - `needs_input` → relay `data.input_request.question` to the user verbatim,
     then run `answer --invocation-id <id> --answer "<user's reply>"`.
   - `completed` → use `data.result.text` as the authoritative final answer.
   - `failed` → explain the failure. Retry only if the user asks.
   - `cancelled` → tell the user it was cancelled.
4. **Observe (optional)**: `observe` returns a temporary `access_url` so the user
   can view or manually operate the desktop. Add `--include-screenshot` to also
   save a screenshot to a local file (`data.screenshot_file`).

You can always use `--last` instead of `--invocation-id <id>` to act on the most
recent invocation (e.g. `watch --last`).

## When to leave the simple path (semantic commands)

The workflow above handles ~80% of requests. Switch to a semantic command when
the user's intent clearly calls for it:

- **Future / recurring** ("today at 8pm", "every morning", "每天/每小时检查",
  "到点提醒我") → create a **scheduled task**, do NOT delegate now:
  - one-off: `schedule create-once --goal "<goal>" --run-at <ISO>`
  - recurring: `schedule create-recurring --goal "<goal>" --start-at <ISO> --interval-hours <n>`
  - After creating it, do NOT run the goal immediately unless the user also asks
    for a one-off now. Read results later with `schedule history --schedule-id <id>`.
- **Pick a desktop** ("用 win10-… 那台桌面") → `desktop list`, then
  `task run --desktop <id-or-name> --objective "..."`.
- **Continue / add background** ("继续刚才那个会话", "先补充一点背景") →
  `context add-note --context-id <id> --text "..."` and/or
  `task continue --context-id <id> --objective "..."`. Use `task run`/`task
  continue` (not `delegate`) whenever you need the context to be reusable.
- **Save a produced file / screenshot / log** ("把结果文件下载下来") →
  `artifact list --task-id <id>` then `artifact save --artifact-id <id>`.
- **Inspect the full conversation** → `timeline show --context-id <id>`.
- **Just check it's working** → `diagnose` (never `delegate` to test).

`task run`/`task continue` drive the same outcome state machine as delegate:
`task status` / `task result` / `task answer` mirror `watch` / `result` /
`answer`. The task id shares the invocation id space, so `--last` works across both.

## Hard rules

- Always go through `scripts/cua.py`. Never hand-build HTTP, MCP, or OAuth calls.
- On `AUTH_REQUIRED`, `TOKEN_EXPIRED`, or `REFRESH_FAILED`: run
  `error.retry_command`, then retry the original command. Do not invent tokens.
- Treat `data.result.text` (when `outcome == completed`) as the only
  authoritative result. Never produce a final answer from `progress`,
  `input_request`, or a screenshot.
- While `outcome == in_progress`, do not answer the delegated task yourself and
  do not switch to your own browser/search tools — keep watching.
- A `GATEWAY_TIMEOUT` / `CUA_BACKEND_UNAVAILABLE` error is transient, NOT a
  failure: the task is still running. Just re-run the same command (`watch --last`
  or `result --last`). Never restart with a new `delegate`.
- `cancel` (or `task cancel`) only when the user explicitly says to stop.
- A scheduled task must NOT create, modify, stop, or delete other scheduled
  tasks. If a goal implies managing schedules, decline or ask the user — the
  gateway rejects nesting (`SCHEDULE_NESTING_NOT_ALLOWED`).
- Scheduled-task results come from `schedule history`, never from a live
  `watch`/`task status`.
- `ping` is a read-only auth/desktop check; it creates no task. `self-test` runs
  local checks only. Do not delegate just to test setup.
- Tokens, the user's objective, answers, result text, and screenshot bytes never
  appear in output — do not try to print or log them.

## References (read when needed)

- `references/commands.md` — every command, its flags and example output.
- `references/outcomes.md` — the outcome state machine.
- `references/auth.md` — login, token refresh, and auth error handling.
- `references/troubleshooting.md` — common failures and fixes.
- `references/api-contract.md` — gateway response and error-code contract.
