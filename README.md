# CUA Skill

Delegate computer-use tasks to CUA (an autonomous cloud-desktop agent) from any
Skill-capable agent. The skill talks to the CUA Skill Gateway over HTTPS; it
needs only Python 3 and the standard library — no MCP, curl, npm, or tokens.

## Install

The skill lives in the [`cua/`](cua/) subdirectory of this repo. Install that
subdirectory (do **not** install the repo root):

```bash
# with the `skills` CLI
npx -y skills add 2B-AL/cua_skill --full-depth --skill cua --agent '*' -g --copy -y
```

`--full-depth --skill cua` makes the installer discover the `cua/` subdirectory
as the actual skill. `--copy` installs a self-contained snapshot into the target
agent directories.

After install, verify it landed completely (this creates no CUA task):

```bash
python3 <skill_dir>/scripts/cua.py self-test
# expect {"ok": true, "action": "self-test", ...}; "logged_in": false is fine before first login
```

If `self-test` errors that `scripts/…` is missing, the checkout was incomplete —
re-install the `cua/` subdirectory (or do a full, non-sparse clone).

## Use

Everything goes through one script; see [`cua/SKILL.md`](cua/SKILL.md) for the
fixed workflow and [`cua/references/`](cua/references/) for details.

```bash
python3 <skill_dir>/scripts/cua.py auth login     # Bytedance SSO in the browser
python3 <skill_dir>/scripts/cua.py delegate --objective "<the user's request>"
python3 <skill_dir>/scripts/cua.py watch --last
```

Zero-config: the gateway URL is baked into [`cua/config.json`](cua/config.json).
Override per call with `--api-base-url <url>` or the `CUA_SKILL_API_BASE_URL`
environment variable.

## Update

If CUA Skill was installed with a recent `skills` CLI, update it in place:

```bash
npx -y skills update cua -g -y
```

If the CLI says the skill cannot be updated automatically, refresh it by
installing from GitHub again:

```bash
npx -y skills add 2B-AL/cua_skill --full-depth --skill cua --agent '*' -g --copy -y
```

Restart the target agent after updating so it reloads the new `SKILL.md` and
scripts.
