"""ByteSSO / Access Hub authentication for the CUA Skill CLI.

The quick bare-metal environment uses ByteSSO through Access Hub. Users log in
in a browser, generate a `cua_mcp_...` Bearer Key on the MCP setup page, and the
CLI stores that key locally with 0600 permissions. Business calls are MCP tool
calls with `Authorization: Bearer <key>`.
"""

import getpass
import os
import sys
import time
import webbrowser

from cua_http import mcp_initialize, mcp_tool_call, mcp_tools_list
from cua_util import RETRYABLE_ERROR_CODES, SkillError, login_retry_command

DEFAULT_LOGIN_TIMEOUT_SEC = 300


def bearer_key_from_env():
    token = os.environ.get("CUA_SKILL_BEARER_KEY")
    return token.strip() if isinstance(token, str) and token.strip() else None


def ensure_bearer_key(state, access_hub_base_url):
    token = bearer_key_from_env() or state.bearer_key
    if token:
        return token
    raise SkillError(
        "AUTH_REQUIRED",
        "Login required for CUA Skill.",
        login_url=mcp_setup_url(access_hub_base_url),
        retry_command=login_retry_command(),
    )


def authorized_tool_call(state, access_hub_base_url, mcp_url, tool_name, arguments=None, timeout=None, retries=0):
    attempt = 0
    while True:
        try:
            token = ensure_bearer_key(state, access_hub_base_url)
            return mcp_tool_call(mcp_url, token, tool_name, arguments or {}, timeout=timeout or 120)
        except SkillError as exc:
            if exc.code in RETRYABLE_ERROR_CODES and attempt < retries:
                attempt += 1
                time.sleep(min(2 * attempt, 5))
                continue
            if exc.code in ("AUTH_REQUIRED", "TOKEN_EXPIRED", "REFRESH_FAILED") and "retry_command" not in exc.extra:
                exc.extra["retry_command"] = login_retry_command()
            raise


def login(state, access_hub_base_url, mcp_url, open_browser=True, bearer_key_stdin=False, no_validate=False):
    setup_url = mcp_setup_url(access_hub_base_url)
    if open_browser:
        try:
            webbrowser.open(setup_url)
        except Exception:  # noqa: BLE001 - headless environments are normal
            pass

    token = _read_login_token(bearer_key_stdin)
    _validate_token_shape(token)

    user = {}
    if not no_validate:
        ping = mcp_tool_call(mcp_url, token, "cua_ping", {}, timeout=30)
        auth = ping.get("auth") or {}
        user = {
            "auth_type": auth.get("auth_type"),
            "org_id": auth.get("org_id"),
            "user_id": auth.get("user_id"),
            "team_id": auth.get("team_id"),
            "desktop_bound": bool(auth.get("desktop_bound")),
        }

    state.set_bearer_key(
        access_hub_base_url=access_hub_base_url,
        mcp_url=mcp_url,
        bearer_key=token,
        user=user,
    )
    return {
        "status": "logged_in",
        "access_hub_url": access_hub_base_url,
        "mcp_url": mcp_url,
        "user": user,
        "credential": {"type": "access_hub_bearer_key", "source": "local_cache"},
    }


def auth_status(state, access_hub_base_url, mcp_url, online=True):
    token = bearer_key_from_env() or state.bearer_key
    if not token:
        return {
            "status": "logged_out",
            "access_hub_url": access_hub_base_url,
            "mcp_url": mcp_url,
            "login_url": mcp_setup_url(access_hub_base_url),
            "retry_command": login_retry_command(),
        }
    if not online:
        return {
            "status": "configured",
            "access_hub_url": access_hub_base_url,
            "mcp_url": mcp_url,
            "credential": {"type": "access_hub_bearer_key", "source": _credential_source(state)},
        }
    ping = mcp_tool_call(mcp_url, token, "cua_ping", {}, timeout=30)
    auth = ping.get("auth") or {}
    return {
        "status": "logged_in",
        "access_hub_url": access_hub_base_url,
        "mcp_url": mcp_url,
        "user": {
            "auth_type": auth.get("auth_type"),
            "org_id": auth.get("org_id"),
            "user_id": auth.get("user_id"),
            "team_id": auth.get("team_id"),
            "desktop_bound": bool(auth.get("desktop_bound")),
        },
        "credential": {"type": "access_hub_bearer_key", "source": _credential_source(state)},
    }


def logout(state):
    state.clear_tokens()
    return {"status": "logged_out"}


def online_self_test(state, access_hub_base_url, mcp_url):
    token = ensure_bearer_key(state, access_hub_base_url)
    init = mcp_initialize(mcp_url, token, timeout=30)
    tools = mcp_tools_list(mcp_url, token, timeout=30)
    ping = mcp_tool_call(mcp_url, token, "cua_ping", {}, timeout=30)
    return {
        "initialize": bool(init),
        "tool_count": len(tools.get("tools") or []),
        "ping": ping,
    }


def mcp_setup_url(access_hub_base_url):
    return access_hub_base_url.rstrip("/") + "/mcp/setup"


def _read_login_token(bearer_key_stdin):
    token = bearer_key_from_env()
    if token:
        return token
    if bearer_key_stdin:
        return sys.stdin.read().strip()
    try:
        return getpass.getpass("Paste Access Hub Bearer Key (input hidden): ").strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise SkillError(
            "AUTH_REQUIRED",
            "Bearer Key was not provided. Open the Access Hub setup page and run auth login again.",
            retry_command=login_retry_command(),
        ) from exc


def _validate_token_shape(token):
    if not token:
        raise SkillError("AUTH_REQUIRED", "Bearer Key was empty.", retry_command=login_retry_command())
    if not token.startswith("cua_mcp_"):
        raise SkillError(
            "VALIDATION_ERROR",
            "Expected an Access Hub Bearer Key starting with 'cua_mcp_'.",
        )


def _credential_source(state):
    if bearer_key_from_env():
        return "environment"
    if state.bearer_key:
        return "local_cache"
    return "none"
