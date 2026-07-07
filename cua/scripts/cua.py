#!/usr/bin/env python3
"""CUA Skill CLI for the ByteSSO Access Hub environment.

Every invocation prints exactly one JSON object. Credentials, objectives,
answers, final CUA text, and screenshot bytes are never printed by the script
outside the structured response contract.
"""

import argparse
import base64
import json
import os
import sys
import tempfile
from pathlib import Path

import cua_auth
from cua_http import extract_tool_payload, mcp_tool_call_raw
from cua_state import AuthState, SessionState
from cua_util import (
    SkillError,
    emit_error,
    emit_success,
    ext_for_mime,
    login_retry_command,
    script_path,
)

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


# -- configuration ---------------------------------------------------------


def resolve_urls(args, state, persist=False):
    cfg = bundled_config()
    access_hub = (
        args.access_hub_base_url
        or os.environ.get("CUA_SKILL_ACCESS_HUB_BASE_URL")
        or state.access_hub_base_url
        or cfg.get("access_hub_base_url")
    )
    mcp_url = (
        args.mcp_url
        or os.environ.get("CUA_SKILL_MCP_URL")
        or state.mcp_url
        or cfg.get("skill_mcp_url")
        or cfg.get("mcp_url")
    )
    if not access_hub:
        raise SkillError(
            "VALIDATION_ERROR",
            "No Access Hub URL configured. Set access_hub_base_url in config.json, "
            "pass --access-hub-base-url, or set CUA_SKILL_ACCESS_HUB_BASE_URL.",
        )
    if not mcp_url:
        raise SkillError(
            "VALIDATION_ERROR",
            "No CUA Skill MCP URL configured. Set skill_mcp_url in config.json, "
            "pass --mcp-url, or set CUA_SKILL_MCP_URL.",
        )
    access_hub = access_hub.rstrip("/")
    mcp_url = mcp_url.rstrip("/")
    if persist:
        state.set_endpoints(access_hub_base_url=access_hub, mcp_url=mcp_url)
    return access_hub, mcp_url


def bundled_config():
    try:
        cfg_path = Path(__file__).resolve().parent.parent / "config.json"
        if not cfg_path.exists():
            return {}
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# -- auth commands ---------------------------------------------------------


def cmd_auth_status(args, state, session):
    access_hub, mcp_url = resolve_urls(args, state)
    return {"data": cua_auth.auth_status(state, access_hub, mcp_url, online=not args.offline)}


def cmd_auth_login(args, state, session):
    access_hub, mcp_url = resolve_urls(args, state, persist=True)
    return {
        "data": cua_auth.login(
            state,
            access_hub,
            mcp_url,
            open_browser=not args.no_browser,
            bearer_key_stdin=args.bearer_key_stdin,
            no_validate=args.no_validate,
        )
    }


def cmd_auth_logout(args, state, session):
    return {"data": cua_auth.logout(state)}


# -- CUA commands ----------------------------------------------------------


def cmd_ping(args, state, session):
    access_hub, mcp_url = resolve_urls(args, state)
    data = cua_auth.authorized_tool_call(
        state, access_hub, mcp_url, "cua_ping", {}, timeout=30, retries=IDEMPOTENT_RETRIES
    )
    return {"data": data}


def cmd_delegate(args, state, session):
    access_hub, mcp_url = resolve_urls(args, state)
    wait_ms = _wait_ms(args.wait_ms)
    envelope = cua_auth.authorized_tool_call(
        state,
        access_hub,
        mcp_url,
        "cua_delegate",
        {"objective": args.objective, "wait_ms": wait_ms},
        timeout=_tool_timeout(wait_ms),
    )
    return _envelope_result(envelope, session)


def cmd_watch(args, state, session):
    access_hub, mcp_url = resolve_urls(args, state)
    invocation_id = _resolve_invocation_id(args, session)
    wait_ms = _wait_ms(args.wait_ms)
    envelope = cua_auth.authorized_tool_call(
        state,
        access_hub,
        mcp_url,
        "cua_watch",
        {"invocation_id": invocation_id, "wait_ms": wait_ms},
        timeout=_tool_timeout(wait_ms),
        retries=IDEMPOTENT_RETRIES,
    )
    return _envelope_result(envelope, session)


def cmd_answer(args, state, session):
    access_hub, mcp_url = resolve_urls(args, state)
    invocation_id = _resolve_invocation_id(args, session)
    wait_ms = _wait_ms(args.wait_ms)
    envelope = cua_auth.authorized_tool_call(
        state,
        access_hub,
        mcp_url,
        "cua_answer",
        {"invocation_id": invocation_id, "answer": args.answer, "wait_ms": wait_ms},
        timeout=_tool_timeout(wait_ms),
    )
    return _envelope_result(envelope, session)


def cmd_cancel(args, state, session):
    access_hub, mcp_url = resolve_urls(args, state)
    invocation_id = _resolve_invocation_id(args, session)
    data = cua_auth.authorized_tool_call(
        state,
        access_hub,
        mcp_url,
        "cua_cancel",
        {"invocation_id": invocation_id},
        timeout=30,
        retries=IDEMPOTENT_RETRIES,
    )
    return {"data": data}


def cmd_observe(args, state, session):
    access_hub, mcp_url = resolve_urls(args, state)
    invocation_id = args.invocation_id or (session.last_invocation_id if args.last else None)
    token = cua_auth.ensure_bearer_key(state, access_hub)
    result = mcp_tool_call_raw(
        mcp_url,
        token,
        "cua_observe",
        {"invocation_id": invocation_id, "include_screenshot": bool(args.include_screenshot)},
        timeout=60,
    )
    data = extract_tool_payload(result)
    if args.include_screenshot:
        screenshot_file = _save_first_image_content(result.get("content") or [])
        if screenshot_file:
            data["screenshot_file"] = screenshot_file
    return {
        "data": data,
        "next": {
            "agent_hint": "The access_url is temporary. If it expires, run observe again. "
            "Use watch, not observe, to decide whether a delegated task is done.",
        },
    }


def cmd_self_test(args, state, session):
    access_hub, mcp_url = resolve_urls(args, state)
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
        "config": {
            "access_hub_url": access_hub,
            "mcp_url": mcp_url,
        },
        "auth": cua_auth.auth_status(state, access_hub, mcp_url, online=False),
    }
    if args.online:
        data["online"] = cua_auth.online_self_test(state, access_hub, mcp_url)
    return {"data": data}


# -- helpers ---------------------------------------------------------------


def _envelope_result(envelope, session):
    invocation_id = envelope.get("invocation_id") if isinstance(envelope, dict) else None
    if invocation_id:
        session.set_last_invocation_id(invocation_id)
    payload = {"data": envelope}
    next_hint = _next_for_envelope(envelope)
    if next_hint:
        payload["next"] = next_hint
    return payload


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
        return {
            "agent_hint": "This invocation reached a terminal outcome. If completed, use result.text as authoritative.",
        }
    return None


def _resolve_invocation_id(args, session):
    invocation_id = getattr(args, "invocation_id", None) or session.last_invocation_id
    if not invocation_id:
        raise SkillError(
            "VALIDATION_ERROR",
            "No invocation id. Pass --invocation-id or run delegate first.",
        )
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


def _save_first_image_content(content):
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "image":
            continue
        b64 = item.get("data")
        if not isinstance(b64, str) or not b64:
            continue
        mime_type = item.get("mimeType") or item.get("mime_type")
        raw = base64.b64decode(b64)
        fd, path = tempfile.mkstemp(prefix="cua-screenshot-", suffix=ext_for_mime(mime_type))
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
        return path
    return None


# -- parser ----------------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(description="Use CUA through ByteSSO Access Hub and Skill MCP.")
    parser.add_argument("--access-hub-base-url", help="Override Access Hub base URL.")
    parser.add_argument("--mcp-url", help="Override CUA Skill MCP endpoint URL.")
    sub = parser.add_subparsers(dest="command")

    auth = sub.add_parser("auth", help="Manage ByteSSO/Access Hub login.")
    auth_sub = auth.add_subparsers(dest="auth_command")

    p = auth_sub.add_parser("status", help="Show login status.")
    p.add_argument("--offline", action="store_true", help="Do not validate the credential online.")
    p.set_defaults(action="auth.status", handler=cmd_auth_status)

    p = auth_sub.add_parser("login", help="Open Access Hub and store the generated Bearer Key.")
    p.add_argument("--no-browser", action="store_true", help="Print/use the setup URL without opening a browser.")
    p.add_argument("--bearer-key-stdin", action="store_true", help="Read the Bearer Key from stdin.")
    p.add_argument("--no-validate", action="store_true", help="Store the key without calling cua_ping.")
    p.set_defaults(action="auth.login", handler=cmd_auth_login)

    p = auth_sub.add_parser("logout", help="Remove the local Bearer Key cache.")
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
    p.add_argument("--invocation-id", help="Optional invocation id to observe.")
    p.add_argument("--last", action="store_true", help="Observe the latest invocation's environment.")
    p.add_argument("--include-screenshot", action="store_true", help="Save an optional screenshot to a temp file.")
    p.set_defaults(action="observe", handler=cmd_observe)

    p = sub.add_parser("self-test", help="Validate local install; --online also checks MCP auth.")
    p.add_argument("--online", action="store_true", help="Run online MCP initialize/tools/list/ping.")
    p.set_defaults(action="self-test", handler=cmd_self_test)

    return parser


if __name__ == "__main__":
    raise SystemExit(main())
