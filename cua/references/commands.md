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

`auth status` validates the cached Bearer Key online with `cua_ping` unless
`--offline` is set.

## Connectivity

```bash
python3 <skill_dir>/scripts/cua.py ping
```

`ping` is read-only. It validates MCP connectivity, auth, and desktop binding.

## Delegate

```bash
python3 <skill_dir>/scripts/cua.py delegate --objective "<user objective>"
python3 <skill_dir>/scripts/cua.py delegate --objective "<user objective>" --wait-ms 30000
```

Pass the user's original objective directly. `--wait-ms` only controls how long
this call waits for a state update; it does not cancel the task.

## Watch

```bash
python3 <skill_dir>/scripts/cua.py watch --invocation-id <id>
python3 <skill_dir>/scripts/cua.py watch --last
python3 <skill_dir>/scripts/cua.py watch --wait-ms 60000
```

If `--invocation-id` is omitted, the script uses the last invocation id saved by
`delegate`, `watch`, or `answer`.

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
python3 <skill_dir>/scripts/cua.py observe --last
python3 <skill_dir>/scripts/cua.py observe --invocation-id <id>
python3 <skill_dir>/scripts/cua.py observe --include-screenshot
```

`observe` is read-only. It returns the current environment state and a temporary
desktop access URL. With `--include-screenshot`, the script saves the image to a
temporary file and returns `screenshot_file`.

Do not use `observe` to decide whether a task is done. Use `watch`.

## Self-Test

```bash
python3 <skill_dir>/scripts/cua.py self-test
python3 <skill_dir>/scripts/cua.py self-test --online
```

Plain `self-test` checks local files and config only. `--online` performs MCP
initialize, tools/list, and `cua_ping`; it requires login and creates no CUA
task.
