"""Authentication orchestration for the CUA Skill CLI.

Implements the OAuth device-login flow, token cache reads, and automatic
access-token refresh against the CUA Skill Gateway. Tokens are saved to the
local cache and are never written to stdout/stderr.
"""

import time
import webbrowser

from cua_http import gateway_call, raw_request
from cua_util import (
    RETRYABLE_ERROR_CODES,
    SkillError,
    epoch_to_iso,
    iso_to_epoch,
    login_retry_command,
    now_epoch,
)

ACCESS_SKEW_SEC = 60
DEFAULT_LOGIN_TIMEOUT_SEC = 300


def ensure_access_token(state, base_url):
    """Return a valid access token, refreshing automatically when needed."""
    expires_at = iso_to_epoch(state.access_token_expires_at)
    if state.access_token and expires_at - ACCESS_SKEW_SEC > now_epoch():
        return state.access_token
    if state.refresh_token:
        return refresh_access_token(state, base_url)
    raise SkillError(
        "AUTH_REQUIRED",
        "Login required for CUA Skill.",
        retry_command=login_retry_command(),
    )


def refresh_access_token(state, base_url):
    try:
        data = gateway_call("POST", base_url, "/v1/auth/refresh", body={"refresh_token": state.refresh_token})
    except SkillError as exc:
        if exc.code in ("REFRESH_FAILED", "AUTH_REQUIRED", "TOKEN_EXPIRED"):
            state.clear_tokens()
            raise SkillError(
                "REFRESH_FAILED",
                "Session expired. Please log in again.",
                retry_command=login_retry_command(),
            )
        raise
    _save_token_set(state, base_url, data)
    return data["access_token"]


def authorized_call(state, base_url, method, path, body=None, query=None, timeout=None, retries=0):
    """Call a business endpoint with auto-refresh and an optional retry on
    transient gateway/backend timeouts.

    `retries` should only be > 0 for idempotent calls (GET, or watch/observe/ping
    which are safe to repeat). Never retry delegate/answer — they create state.
    """
    attempt = 0
    while True:
        try:
            return _authorized_call_once(state, base_url, method, path, body=body, query=query, timeout=timeout)
        except SkillError as exc:
            if exc.code in RETRYABLE_ERROR_CODES and attempt < retries:
                attempt += 1
                time.sleep(min(2 * attempt, 5))
                continue
            raise


def authorized_raw_call(state, base_url, method, path, body=None, query=None, timeout=None, retries=0):
    """Call a business endpoint and return (headers, raw_bytes), with the same
    token refresh/retry behavior as authorized_call."""
    attempt = 0
    while True:
        try:
            return _authorized_raw_call_once(state, base_url, method, path, body=body, query=query, timeout=timeout)
        except SkillError as exc:
            if exc.code in RETRYABLE_ERROR_CODES and attempt < retries:
                attempt += 1
                time.sleep(min(2 * attempt, 5))
                continue
            raise


def _authorized_call_once(state, base_url, method, path, body=None, query=None, timeout=None):
    kwargs = {"body": body, "query": query}
    if timeout is not None:
        kwargs["timeout"] = timeout
    token = ensure_access_token(state, base_url)
    try:
        return gateway_call(method, base_url, path, token=token, **kwargs)
    except SkillError as exc:
        if exc.code in ("TOKEN_EXPIRED", "AUTH_REQUIRED") and state.refresh_token:
            token = refresh_access_token(state, base_url)
            return gateway_call(method, base_url, path, token=token, **kwargs)
        if exc.code in ("AUTH_REQUIRED", "TOKEN_EXPIRED") and "retry_command" not in exc.extra:
            exc.extra["retry_command"] = login_retry_command()
        raise


def _authorized_raw_call_once(state, base_url, method, path, body=None, query=None, timeout=None):
    kwargs = {"body": body, "query": query}
    if timeout is not None:
        kwargs["timeout"] = timeout
    token = ensure_access_token(state, base_url)
    try:
        _status, headers, raw = raw_request(method, base_url, path, token=token, **kwargs)
        return headers, raw
    except SkillError as exc:
        if exc.code in ("TOKEN_EXPIRED", "AUTH_REQUIRED") and state.refresh_token:
            token = refresh_access_token(state, base_url)
            _status, headers, raw = raw_request(method, base_url, path, token=token, **kwargs)
            return headers, raw
        if exc.code in ("AUTH_REQUIRED", "TOKEN_EXPIRED") and "retry_command" not in exc.extra:
            exc.extra["retry_command"] = login_retry_command()
        raise


def login(state, base_url, open_browser=True, timeout=DEFAULT_LOGIN_TIMEOUT_SEC, session_id=None):
    """Run the device-login flow and persist the resulting tokens."""
    if session_id:
        session = {"session_id": session_id, "interval": 3, "expires_at": None,
                   "login_url": None, "user_code": None}
    else:
        session = gateway_call("POST", base_url, "/v1/auth/device/start", body={"device": _device_label()})
        login_url = session.get("login_url")
        if open_browser and login_url:
            try:
                webbrowser.open(login_url)
            except Exception:  # noqa: BLE001 - headless environments are expected
                pass

    interval = max(1, int(session.get("interval") or 3))
    deadline = now_epoch() + timeout
    session_expiry = iso_to_epoch(session.get("expires_at")) if session.get("expires_at") else 0
    if session_expiry:
        deadline = min(deadline, session_expiry)

    while now_epoch() < deadline:
        data = gateway_call("POST", base_url, "/v1/auth/device/poll", body={"session_id": session["session_id"]})
        if data.get("status") == "authorized" and data.get("access_token"):
            user = _save_token_set(state, base_url, data)
            return {
                "status": "logged_in",
                "user": _safe_user(user),
                "desktop_bound": bool(data.get("desktop_bound")),
                "scopes": data.get("scopes", []),
                "access_token_expires_at": state.access_token_expires_at,
            }
        time.sleep(interval)

    raise SkillError(
        "AUTH_REQUIRED",
        "Login was not completed in time.",
        login_url=session.get("login_url"),
        user_code=session.get("user_code"),
        session_id=session["session_id"],
        retry_command=login_retry_command() + f" --session-id {session['session_id']}",
    )


def auth_status(state, base_url):
    """Verify the current session against /v1/auth/me without exposing tokens."""
    data = authorized_call(state, base_url, "GET", "/v1/auth/me")
    return {
        "status": "logged_in",
        "user": _safe_user(data.get("user", {})),
        "scopes": data.get("scopes", []),
        "desktop_bound": state.desktop_bound,
        "access_token_expires_at": state.access_token_expires_at,
    }


def logout(state, base_url):
    refresh_token = state.refresh_token
    if refresh_token:
        try:
            gateway_call("POST", base_url, "/v1/auth/logout", body={"refresh_token": refresh_token})
        except SkillError:
            pass  # Best-effort server revoke; always clear the local cache.
    state.clear_tokens()
    return {"status": "logged_out"}


# -- internals -------------------------------------------------------------


def _save_token_set(state, base_url, data):
    now = now_epoch()
    user = data.get("user", {})
    state.set_tokens(
        api_base_url=base_url,
        user=_safe_user(user),
        access_token=data["access_token"],
        access_token_expires_at=epoch_to_iso(now + float(data.get("expires_in", 0))),
        refresh_token=data["refresh_token"],
        refresh_token_expires_at=epoch_to_iso(now + float(data.get("refresh_expires_in", 0))),
        desktop_bound=bool(data.get("desktop_bound")),
    )
    return user


def _safe_user(user):
    if not isinstance(user, dict):
        return {}
    return {
        "org_id": user.get("org_id"),
        "user_id": user.get("user_id"),
        "email": user.get("email"),
    }


def _device_label():
    import platform

    try:
        return platform.node() or "cua-skill"
    except Exception:  # noqa: BLE001
        return "cua-skill"
