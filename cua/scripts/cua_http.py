"""AP-style HTTP client for the CUA Skill Gateway.

Stdlib only. The client talks to:

- GET  /skill/manifest
- POST /skill/tools/{tool}

Tool errors are converted into stable SkillError codes used by the CLI.
"""

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cua_util import SkillError, login_retry_command

DEFAULT_TIMEOUT_SEC = 120


def gateway_manifest(gateway_url, timeout=30):
    req = Request(
        _join(gateway_url, "/skill/manifest"),
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            return _decode_json(resp.read())
    except HTTPError as exc:
        body = exc.read()
        _raise_http_error(exc.code, body)
    except URLError as exc:
        raise SkillError("NETWORK", f"Cannot reach CUA Skill Gateway at {gateway_url}: {exc.reason}")
    except TimeoutError:
        raise SkillError("NETWORK", f"Request to CUA Skill Gateway timed out: {gateway_url}")


def gateway_tool_call(gateway_url, bearer_key, tool_name, arguments=None, timeout=DEFAULT_TIMEOUT_SEC):
    payload = arguments or {}
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if bearer_key:
        headers["Authorization"] = "Bearer " + bearer_key
    req = Request(
        _join(gateway_url, f"/skill/tools/{tool_name}"),
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            envelope = _decode_json(resp.read())
            return _tool_result(envelope)
    except HTTPError as exc:
        body = exc.read()
        _raise_http_error(exc.code, body)
    except URLError as exc:
        raise SkillError("NETWORK", f"Cannot reach CUA Skill Gateway at {gateway_url}: {exc.reason}")
    except TimeoutError:
        raise SkillError("GATEWAY_TIMEOUT", f"Request to CUA Skill Gateway timed out: {gateway_url}")


def _tool_result(envelope):
    if not isinstance(envelope, dict):
        raise SkillError("INTERNAL", "CUA Skill Gateway returned a non-object response.")
    if envelope.get("ok") is True:
        result = envelope.get("result")
        return result if isinstance(result, dict) else {}
    error = envelope.get("error") if isinstance(envelope.get("error"), dict) else {}
    _raise_mapped_error(error.get("code"), error.get("upstream_status"), error.get("message"))


def _raise_http_error(status, body):
    text = body.decode("utf-8", errors="replace") if body else ""
    message = text[:300] or f"HTTP {status}"
    try:
        payload = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message") or message
            _raise_mapped_error(error.get("code"), status, message)
        _raise_mapped_error(payload.get("code") or payload.get("error"), status, payload.get("message") or message)
    _raise_mapped_error(None, status, message)


def _raise_mapped_error(code, status, message):
    stable = str(code or "").strip()
    msg = message or "CUA Skill Gateway request failed."
    if stable in ("Unauthorized", "AUTH_REQUIRED", "TOKEN_EXPIRED", "REFRESH_FAILED"):
        raise SkillError("AUTH_REQUIRED", msg, retry_command=login_retry_command())
    if stable in ("TaskNotOwned", "TaskNotStarted"):
        raise SkillError("INVOCATION_NOT_FOUND", msg)
    if stable in ("DesktopNotOwned",):
        raise SkillError("FORBIDDEN", msg)
    if stable in ("DesktopNotReady", "no_active_cua_allocation"):
        raise SkillError("DESKTOP_NOT_BOUND", msg)
    if stable in ("AccessHubUnavailable", "MyCUAUnavailable"):
        raise SkillError("CUA_BACKEND_UNAVAILABLE", msg)
    if stable:
        raise SkillError(stable, msg)

    if status in (401, "401"):
        raise SkillError("AUTH_REQUIRED", msg or "Login required for CUA Skill.", retry_command=login_retry_command())
    if status in (403, "403"):
        raise SkillError("FORBIDDEN", msg or "CUA Skill credential is forbidden.")
    if status in (404, "404"):
        raise SkillError("INVOCATION_NOT_FOUND", msg or "CUA invocation was not found.")
    if status in (409, "409"):
        raise SkillError("DESKTOP_NOT_BOUND", msg)
    if status in (429, "429"):
        raise SkillError("RATE_LIMITED", msg or "CUA Skill Gateway rate limited the request.")
    if status in (502, 503, "502", "503"):
        raise SkillError("CUA_BACKEND_UNAVAILABLE", msg or "CUA backend is unavailable.")
    if status in (504, "504"):
        raise SkillError("GATEWAY_TIMEOUT", msg or "CUA Skill Gateway timed out; the task may still be running.")
    raise SkillError("INTERNAL", msg or "CUA Skill Gateway returned an unexpected error.")


def _decode_json(raw):
    text = raw.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError as exc:
        raise SkillError("INTERNAL", f"CUA Skill Gateway returned non-JSON response: {text[:200]}") from exc
    if not isinstance(payload, dict):
        raise SkillError("INTERNAL", "CUA Skill Gateway JSON response was not an object.")
    return payload


def _join(base, path):
    return base.rstrip("/") + "/" + path.lstrip("/")
