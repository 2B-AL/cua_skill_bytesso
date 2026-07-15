# Commands

All commands print one JSON object.

## Auth

```bash
python3 <skill_dir>/scripts/cua.py auth status
python3 <skill_dir>/scripts/cua.py auth status --offline
python3 <skill_dir>/scripts/cua.py auth login
python3 <skill_dir>/scripts/cua.py auth login --bearer-key-stdin
python3 <skill_dir>/scripts/cua.py auth logout
```

`auth login` prints and opens one Access Hub ByteSSO browser login URL, waits
for the browser callback, and stores the returned local CUA credential. Agents
should run this command themselves when auth is required; users should only open
the printed browser URL. `auth status` validates the cached credential online
with `cua_get_desktop_access` unless `--offline` is set.

## Connectivity

```bash
python3 <skill_dir>/scripts/cua.py ping
```

`ping` is read-only. It validates gateway connectivity, auth, and desktop binding.

## Desktops

```bash
python3 <skill_dir>/scripts/cua.py desktops list
python3 <skill_dir>/scripts/cua.py desktops allocate
python3 <skill_dir>/scripts/cua.py desktops allocate --label "qa-run"
python3 <skill_dir>/scripts/cua.py desktops allocate --spec-code s80 --label "qa-run"
python3 <skill_dir>/scripts/cua.py desktops use <desktop_id>
```

`desktops list` returns the caller's allocated desktops and quota. `desktops
allocate` requests one additional CUA desktop and is rejected if the caller is
over quota. `desktops use` stores a local default desktop for later `observe`
and `delegate` calls.

## Tasks

```bash
python3 <skill_dir>/scripts/cua.py tasks list
python3 <skill_dir>/scripts/cua.py tasks list --status all --limit 50
python3 <skill_dir>/scripts/cua.py tasks watch --task-id <id> --task-id <id>
python3 <skill_dir>/scripts/cua.py tasks watch --task-id <id> --wait-ms 60000
python3 <skill_dir>/scripts/cua.py tasks watch --last
```

`tasks list` returns delegated CUA tasks for the current credential. The default
filter is `active`, which is the right view for concurrent QA work. Use
`--status all` to recover recent terminal tasks.

`tasks watch` refreshes or waits on several task ids. `--wait-ms` is the total
client-side wait budget. The CLI splits budgets above 60 seconds into several
gateway calls because each server wait is capped at 60 seconds. It
returns `data.tasks`, where each item uses the same invocation envelope shape as
`watch`. Use this instead of repeatedly blocking on one task when multiple
desktops are running work.

## Parallel Tasks

Use several `delegate` calls only for independent subtasks. Track their task ids
with `tasks watch`:

```bash
python3 <skill_dir>/scripts/cua.py desktops list
python3 <skill_dir>/scripts/cua.py delegate --auto --objective "<subtask A>"
python3 <skill_dir>/scripts/cua.py delegate --auto --objective "<subtask B>"
python3 <skill_dir>/scripts/cua.py tasks watch --task-id <idA> --task-id <idB> --wait-ms 60000
```

Each subtask must be self-contained. Use completed `result.text` and artifacts
as the source for the final response.

## Delegate

```bash
python3 <skill_dir>/scripts/cua.py delegate --objective "<user objective>"
python3 <skill_dir>/scripts/cua.py delegate --desktop-id <desktop_id> --objective "<user objective>"
python3 <skill_dir>/scripts/cua.py delegate --auto --objective "<user objective>"
python3 <skill_dir>/scripts/cua.py delegate --objective "<user objective>" --wait-ms 30000
```

Pass the user's original objective directly. By default `delegate` starts the
task and returns quickly with an invocation id. `--wait-ms` is a total
client-side wait budget; the CLI polls in server-sized chunks of at most 60
seconds and does not cancel the task when the budget expires. `--auto` selects
an idle desktop, or allocates a new one when quota allows.

## Watch

```bash
python3 <skill_dir>/scripts/cua.py watch --invocation-id <id>
python3 <skill_dir>/scripts/cua.py watch --last
python3 <skill_dir>/scripts/cua.py watch --wait-ms 60000
```

If `--invocation-id` is omitted, the script uses the last invocation id saved by
`delegate`, `watch`, or `answer`. For example, `--wait-ms 900000` waits for up
to 15 minutes through multiple gateway calls while preserving the gateway's
60-second per-request maximum.

## Answer

```bash
python3 <skill_dir>/scripts/cua.py answer --invocation-id <id> --answer "<user answer>"
python3 <skill_dir>/scripts/cua.py answer --answer "<user answer>"
```

Use only after CUA returns `needs_input`. Preserve the user's answer.

## Cancel

```bash
python3 <skill_dir>/scripts/cua.py cancel --invocation-id <id>
python3 <skill_dir>/scripts/cua.py cancel
```

Use only when the user explicitly asks to stop.

## Observe

```bash
python3 <skill_dir>/scripts/cua.py observe
python3 <skill_dir>/scripts/cua.py observe --desktop-id <desktop_id>
python3 <skill_dir>/scripts/cua.py observe --last
python3 <skill_dir>/scripts/cua.py observe --invocation-id <id>
python3 <skill_dir>/scripts/cua.py observe --include-screenshot
```

`observe` is read-only. It returns the current environment state and a temporary
desktop access URL. With `--include-screenshot`, the script saves the image to a
temporary file and returns `screenshot_file`.

Do not ask the user to run `observe`. Run it after auth is ready when the user
asks for the CUA/cloud desktop link. Do not use `observe` to decide whether a
task is done. Use `watch`.

## Self-Test

```bash
python3 <skill_dir>/scripts/cua.py self-test
python3 <skill_dir>/scripts/cua.py self-test --online
```

Plain `self-test` checks local files and config only. `--online` checks
`/skill/manifest` and `cua_get_desktop_access`; it requires login and creates no
CUA task.
