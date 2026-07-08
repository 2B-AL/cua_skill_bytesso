#!/usr/bin/env python3
"""CUA Skill CLI for the ByteSSO Access Hub environment.

Every invocation prints exactly one JSON object. Credentials, objectives,
answers, final CUA text, and screenshot bytes are never printed outside the
structured response contract.
"""

import argparse
import base64
import json
import math
import os
import sys
import tempfile
from pathlib import Path

import cua_auth
from cua_http import gateway_manifest, gateway_tool_call
from cua_state import AuthState, SessionState
from cua_util import SkillError, emit_error, emit_success, ext_for_mime, script_path

IDEMPOTENT_RETRIES = 2
TERMINAL_OUTCOMES = ("completed", "failed", "cancelled")


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    action = getattr(args, "action", None)
    if not action:
        parser.print_help(sys.stderr)
        return 2
    try:
        state = AuthState.load()
        session = SessionState.load()
        data = args.handler(args, state, session)
        emit_success(action, data)
    except SkillError as exc:
        emit_error(action, exc)
    except BrokenPipeError:
        raise
    except Exception as exc:  # noqa: BLE001 - keep the CLI JSON-only
        emit_error(action, SkillError("INTERNAL", str(exc)))


def resolve_urls(args, state, persist=False):
    cfg = bundled_config()
    access_hub = (
        args.access_hub_base_url
        or os.environ.get("CUA_SKILL_ACCESS_HUB_BASE_URL")
        or state.access_hub_base_url
        or cfg.get("access_hub_base_url")
    )
    gateway_url = (
        args.gateway_url
        or os.environ.get("CUA_SKILL_GATEWAY_URL")
        or os.environ.get("CUA_SKILL_MCP_URL")
        or state.gateway_url
        or cfg.get("skill_gateway_url")
        or cfg.get("gateway_url")
        or cfg.get("skill_mcp_url")
        or cfg.get("mcp_url")
    )
    if not access_hub:
        raise SkillError(
            "VALIDATION_ERROR",
            "No Access Hub URL configured. Set access_hub_base_url in config.json, "
            "pass --access-hub-base-url, or set CUA_SKILL_ACCESS_HUB_BASE_URL.",
        )
    if not gateway_url:
        raise SkillError(
            "VALIDATION_ERROR",
            "No CUA Skill Gateway URL configured. Set skill_gateway_url in config.json, "
            "pass --gateway-url, or set CUA_SKILL_GATEWAY_URL.",
        )
    access_hub = access_hub.rstrip("/")
    gateway_url = gateway_url.rstrip("/")
    if persist:
        state.set_endpoints(access_hub_base_url=access_hub, gateway_url=gateway_url)
    return access_hub, gateway_url


def bundled_config():
    try:
        cfg_path = Path(__file__).resolve().parent.parent / "config.json"
        if not cfg_path.exists():
            return {}
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def cmd_auth_status(args, state, session):
    access_hub, gateway_url = resolve_urls(args, state)
    return {"data": cua_auth.auth_status(state, access_hub, gateway_url, online=not args.offline)}


def cmd_auth_login(args, state, session):
    access_hub, gateway_url = resolve_urls(args, state, persist=True)
    return {
        "data": cua_auth.login(
            state,
            access_hub,
            gateway_url,
            open_browser=not args.no_browser,
            bearer_key_stdin=args.bearer_key_stdin,
            no_validate=args.no_validate,
        )
    }


def cmd_auth_logout(args, state, session):
    return {"data": cua_auth.logout(state)}


def cmd_ping(args, state, session):
    access_hub, gateway_url = resolve_urls(args, state)
    manifest = gateway_manifest(gateway_url, timeout=30)
    access = cua_auth.authorized_tool_call(
        state,
        access_hub,
        gateway_url,
        "cua_get_desktop_access",
        {"ttl_seconds": 300},
        timeout=30,
        retries=IDEMPOTENT_RETRIES,
    )
    return {
        "data": {
            "ok": True,
            "server": {"name": manifest.get("name"), "version": manifest.get("version")},
            "tool_count": len(manifest.get("tools") or []),
            "desktop": access.get("desktop"),
            "has_desktop_access_url": bool((access.get("access") or {}).get("desktop_login_url")),
            "agent_hint": "Gateway auth is valid and the caller has a desktop binding.",
        }
    }


def cmd_delegate(args, state, session):
    access_hub, gateway_url = resolve_urls(args, state)
    wait_ms = _wait_ms(args.wait_ms)
    payload = cua_auth.authorized_tool_call(
        state,
        access_hub,
        gateway_url,
        "cua_run_task",
        {"input": args.objective},
        timeout=_tool_timeout(wait_ms),
    )
    if wait_ms and wait_ms > 0:
        payload = cua_auth.authorized_tool_call(
            state,
            access_hub,
            gateway_url,
            "cua_wait_task",
            {"task_id": payload.get("task_id"), "timeout_seconds": _wait_seconds(wait_ms)},
            timeout=_tool_timeout(wait_ms),
        )
    return _envelope_result(_task_envelope(payload), session)


def cmd_watch(args, state, session):
    access_hub, gateway_url = resolve_urls(args, state)
    invocation_id = _resolve_invocation_id(args, session)
    wait_ms = _wait_ms(args.wait_ms)
    request = {"task_id": invocation_id}
    if wait_ms is not None:
        request["timeout_seconds"] = _wait_seconds(wait_ms)
    payload = cua_auth.authorized_tool_call(
        state,
        access_hub,
        gateway_url,
        "cua_wait_task",
        request,
        timeout=_tool_timeout(wait_ms),
        retries=IDEMPOTENT_RETRIES,
    )
    return _envelope_result(_task_envelope(payload), session)


def cmd_answer(args, state, session):
    access_hub, gateway_url = resolve_urls(args, state)
    invocation_id = _resolve_invocation_id(args, session)
    wait_ms = _wait_ms(args.wait_ms)
    payload = cua_auth.authorized_tool_call(
        state,
        access_hub,
        gateway_url,
        "cua_resume_task",
        {"task_id": invocation_id, "input": args.answer},
        timeout=_tool_timeout(wait_ms),
    )
    if wait_ms and wait_ms > 0:
        payload = cua_auth.authorized_tool_call(
            state,
            access_hub,
            gateway_url,
            "cua_wait_task",
            {"task_id": invocation_id, "timeout_seconds": _wait_seconds(wait_ms)},
            timeout=_tool_timeout(wait_ms),
        )
    return _envelope_result(_task_envelope(payload), session)


def cmd_cancel(args, state, session):
    access_hub, gateway_url = resolve_urls(args, state)
    invocation_id = _resolve_invocation_id(args, session)
    payload = cua_auth.authorized_tool_call(
        state,
        access_hub,
        gateway_url,
        "cua_cancel_task",
        {"task_id": invocation_id},
        timeout=30,
        retries=IDEMPOTENT_RETRIES,
    )
    return {"data": _task_envelope(payload)}


def cmd_observe(args, state, session):
    access_hub, gateway_url = resolve_urls(args, state)
    token = cua_auth.ensure_bearer_key(state, access_hub)
    data = gateway_tool_call(gateway_url, token, "cua_get_desktop_access", {"ttl_seconds": 300}, timeout=60)
    if args.include_screenshot:
        shot = gateway_tool_call(gateway_url, token, "cua_take_screenshot", {}, timeout=60)
        screenshot_file = _save_screenshot_payload(shot.get("screenshot") or {})
        if screenshot_file:
            data["screenshot_file"] = screenshot_file
            data["screenshot"] = {k: v for k, v in (shot.get("screenshot") or {}).items() if k != "base64"}
    access = data.get("access") or {}
    if access.get("desktop_login_url") and "access_url" not in data:
        data["access_url"] = access.get("desktop_login_url")
    return {
        "data": data,
        "next": {
            "agent_hint": "The access_url is temporary. If it expires, run observe again. "
            "Use watch, not observe, to decide whether a delegated task is done.",
        },
    }


def cmd_self_test(args, state, session):
    access_hub, gateway_url = resolve_urls(args, state)
    skill_dir = Path(__file__).resolve().parent.parent
    required = [
        skill_dir / "SKILL.md",
        skill_dir / "config.json",
        skill_dir / "scripts" / "cua.py",
        skill_dir / "scripts" / "cua_auth.py",
        skill_dir / "scripts" / "cua_http.py",
        skill_dir / "scripts" / "cua_state.py",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SkillError("INTERNAL", "Skill install is incomplete.", missing=missing)
    data = {
        "skill_dir": str(skill_dir),
        "config": {"access_hub_url": access_hub, "gateway_url": gateway_url},
        "auth": cua_auth.auth_status(state, access_hub, gateway_url, online=False),
    }
    if args.online:
        data["online"] = cua_auth.online_self_test(state, access_hub, gateway_url)
    return {"data": data}


def _envelope_result(envelope, session):
    invocation_id = envelope.get("invocation_id") if isinstance(envelope, dict) else None
    if invocation_id:
        session.set_last_invocation_id(invocation_id)
    payload = {"data": envelope}
    next_hint = _next_for_envelope(envelope)
    if next_hint:
        payload["next"] = next_hint
    return payload


def _task_envelope(payload):
    payload = payload if isinstance(payload, dict) else {}
    task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
    upstream = payload.get("upstream") if isinstance(payload.get("upstream"), dict) else {}
    run = upstream.get("run") if isinstance(upstream.get("run"), dict) else {}
    task_id = payload.get("task_id") or task.get("task_id")
    status = payload.get("status") or task.get("status") or run.get("status") or upstream.get("status") or "running"
    outcome = _outcome_from_status(status)
    run_id = payload.get("mycua_run_id") or task.get("mycua_run_id") or run.get("id")
    return {
        "invocation_id": task_id,
        "outcome": outcome,
        "result": {
            "text": _result_text(upstream) if outcome == "completed" else None,
            "artifacts": _artifacts(upstream),
        },
        "input_request": _input_request(upstream) if outcome == "needs_input" else None,
        "progress": {
            "summary": _progress_summary(status, upstream),
            "step_count": 0,
            "updated_at": _updated_at(upstream),
        },
        "next_action": _next_action(outcome),
        "diagnostics": {"trace_id": run_id, "mycua_run_id": run_id, "raw_status": status},
    }


def _outcome_from_status(status):
    normalized = str(status or "").strip().lower()
    if normalized in ("succeeded", "completed", "success"):
        return "completed"
    if normalized in ("failed", "error"):
        return "failed"
    if normalized in ("cancelled", "canceled"):
        return "cancelled"
    if normalized in ("interrupted", "blocked", "waiting_input", "requires_input", "needs_input"):
        return "needs_input"
    return "in_progress"


def _result_text(upstream):
    if not isinstance(upstream, dict):
        return None
    candidates = [upstream.get("text")]
    for key in ("result", "output"):
        value = upstream.get(key)
        if isinstance(value, dict):
            candidates.append(value.get("text"))
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return None


def _input_request(upstream):
    if not isinstance(upstream, dict):
        return None
    for key in ("input_request", "ask_user", "question"):
        value = upstream.get(key)
        if isinstance(value, dict):
            return value
        if isinstance(value, str) and value.strip():
            return {"question": value, "choices": []}
    run = upstream.get("run") if isinstance(upstream.get("run"), dict) else {}
    value = run.get("input_request")
    return value if isinstance(value, dict) else {"question": "CUA needs user input.", "choices": []}


def _artifacts(upstream):
    artifacts = upstream.get("artifacts") if isinstance(upstream, dict) else None
    return artifacts if isinstance(artifacts, list) else []


def _progress_summary(status, upstream):
    progress = upstream.get("progress") if isinstance(upstream, dict) else None
    if isinstance(progress, dict) and isinstance(progress.get("summary"), str):
        return progress.get("summary")
    return f"CUA task status: {status}"


def _updated_at(upstream):
    if isinstance(upstream, dict):
        for key in ("updated_at", "completed_at", "created_at"):
            value = upstream.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _next_action(outcome):
    if outcome == "in_progress":
        return {"type": "watch", "agent_hint": "CUA is still working. Run watch again; do not answer from progress text."}
    if outcome == "needs_input":
        return {"type": "answer", "agent_hint": "Relay input_request.question to the user, then submit the user's answer."}
    return {"type": "done", "agent_hint": "This invocation reached a terminal outcome."}


def _next_for_envelope(envelope):
    if not isinstance(envelope, dict):
        return None
    invocation_id = envelope.get("invocation_id")
    outcome = envelope.get("outcome")
    if not invocation_id:
        return None
    hint = ((envelope.get("next_action") or {}).get("agent_hint") or "").strip()
    base = f"python3 {script_path()}"
    if outcome == "in_progress":
        return {
            "command": f"{base} watch --invocation-id {invocation_id}",
            "agent_hint": hint or "CUA is still working. Run watch again; do not answer from progress text.",
        }
    if outcome == "needs_input":
        return {
            "command": f"{base} answer --invocation-id {invocation_id} --answer '<user answer>'",
            "agent_hint": hint or "Relay input_request.question to the user, then submit the user's answer.",
        }
    if outcome in TERMINAL_OUTCOMES:
        return {"agent_hint": "This invocation reached a terminal outcome. If completed, use result.text as authoritative."}
    return None


def _resolve_invocation_id(args, session):
    invocation_id = getattr(args, "invocation_id", None) or session.last_invocation_id
    if not invocation_id:
        raise SkillError("VALIDATION_ERROR", "No invocation id. Pass --invocation-id or run delegate first.")
    return invocation_id


def _wait_ms(value):
    if value is None:
        return None
    if value < 0:
        raise SkillError("VALIDATION_ERROR", "--wait-ms must be >= 0")
    return value


def _tool_timeout(wait_ms):
    if wait_ms is None:
        return 120
    return max(30, min(720, int(wait_ms / 1000) + 30))


def _wait_seconds(wait_ms):
    if wait_ms is None:
        return None
    return max(1, min(60, int(math.ceil(wait_ms / 1000))))


def _save_screenshot_payload(screenshot):
    if not isinstance(screenshot, dict):
        return None
    b64 = screenshot.get("base64")
    if not isinstance(b64, str) or not b64:
        return None
    raw = base64.b64decode(b64)
    fd, path = tempfile.mkstemp(prefix="cua-screenshot-", suffix=ext_for_mime(screenshot.get("mime_type")))
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)
    return path


def build_parser():
    parser = argparse.ArgumentParser(description="Use CUA through ByteSSO Access Hub and Skill Gateway.")
    parser.add_argument("--access-hub-base-url", help="Override Access Hub base URL.")
    parser.add_argument("--gateway-url", help="Override CUA Skill Gateway base URL.")
    parser.add_argument("--mcp-url", dest="gateway_url", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command")

    auth = sub.add_parser("auth", help="Manage ByteSSO/Access Hub login.")
    auth_sub = auth.add_subparsers(dest="auth_command")

    p = auth_sub.add_parser("status", help="Show login status.")
    p.add_argument("--offline", action="store_true", help="Do not validate the credential online.")
    p.set_defaults(action="auth.status", handler=cmd_auth_status)

    p = auth_sub.add_parser("login", help="Open Access Hub SSO and store the returned CUA credential.")
    p.add_argument("--no-browser", action="store_true", help="Print the login URL without opening a browser.")
    p.add_argument("--bearer-key-stdin", action="store_true", help="Read a legacy Access Hub bearer key from stdin.")
    p.add_argument("--no-validate", action="store_true", help="Store the key without calling the gateway.")
    p.set_defaults(action="auth.login", handler=cmd_auth_login)

    p = auth_sub.add_parser("logout", help="Remove the local CUA credential cache.")
    p.set_defaults(action="auth.logout", handler=cmd_auth_logout)

    p = sub.add_parser("ping", help="Read-only connectivity check.")
    p.set_defaults(action="ping", handler=cmd_ping)

    p = sub.add_parser("delegate", help="Delegate a user objective to CUA.")
    p.add_argument("--objective", required=True, help="The user's original objective.")
    p.add_argument("--wait-ms", type=int, default=None, help="Optional wait window in milliseconds.")
    p.set_defaults(action="delegate", handler=cmd_delegate)

    p = sub.add_parser("watch", help="Wait for or check an invocation.")
    p.add_argument("--invocation-id", help="Invocation id returned by delegate.")
    p.add_argument("--last", action="store_true", help="Use the latest saved invocation id.")
    p.add_argument("--wait-ms", type=int, default=None, help="Optional wait window in milliseconds.")
    p.set_defaults(action="watch", handler=cmd_watch)

    p = sub.add_parser("answer", help="Submit the user's answer to CUA.")
    p.add_argument("--invocation-id", help="Invocation id returned by delegate.")
    p.add_argument("--last", action="store_true", help="Use the latest saved invocation id.")
    p.add_argument("--answer", required=True, help="The user's answer to input_request.question.")
    p.add_argument("--wait-ms", type=int, default=None, help="Optional wait window in milliseconds.")
    p.set_defaults(action="answer", handler=cmd_answer)

    p = sub.add_parser("cancel", help="Cancel an invocation when the user asks to stop.")
    p.add_argument("--invocation-id", help="Invocation id returned by delegate.")
    p.add_argument("--last", action="store_true", help="Use the latest saved invocation id.")
    p.set_defaults(action="cancel", handler=cmd_cancel)

    p = sub.add_parser("observe", help="Read-only desktop state and temporary access URL.")
    p.add_argument("--invocation-id", help="Accepted for compatibility; desktop access is caller-scoped.")
    p.add_argument("--last", action="store_true", help="Accepted for compatibility; desktop access is caller-scoped.")
    p.add_argument("--include-screenshot", action="store_true", help="Save an optional screenshot to a temp file.")
    p.set_defaults(action="observe", handler=cmd_observe)

    p = sub.add_parser("self-test", help="Validate local install; --online also checks gateway auth.")
    p.add_argument("--online", action="store_true", help="Run online manifest and desktop-access checks.")
    p.set_defaults(action="self-test", handler=cmd_self_test)

    return parser


if __name__ == "__main__":
    raise SystemExit(main())
