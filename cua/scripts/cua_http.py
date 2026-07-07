"""MCP Streamable HTTP client for the CUA Skill.

Stdlib only. The client posts JSON-RPC requests to the remote `/skill/mcp`
endpoint, accepts JSON or SSE responses, and converts protocol/tool errors into
stable SkillError codes.
"""

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cua_util import SkillError, login_retry_command

DEFAULT_TIMEOUT_SEC = 120
MCP_PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "cua-skill-bytesso", "version": "0.1.0"}


def mcp_initialize(mcp_url, bearer_key, timeout=30):
    payload = _jsonrpc(
        "initialize",
        {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        },
        rpc_id="init",
    )
    return _post_jsonrpc(mcp_url, bearer_key, payload, timeout=timeout).get("result", {})


def mcp_tools_list(mcp_url, bearer_key, timeout=30):
    payload = _jsonrpc("tools/list", {}, rpc_id="tools")
    return _post_jsonrpc(mcp_url, bearer_key, payload, timeout=timeout).get("result", {})


def mcp_tool_call(mcp_url, bearer_key, tool_name, arguments=None, timeout=DEFAULT_TIMEOUT_SEC):
    """Call a CUA MCP tool and return its structuredContent payload."""
    result = mcp_tool_call_raw(mcp_url, bearer_key, tool_name, arguments, timeout=timeout)
    return extract_tool_payload(result)


def mcp_tool_call_raw(mcp_url, bearer_key, tool_name, arguments=None, timeout=DEFAULT_TIMEOUT_SEC):
    payload = _jsonrpc(
        "tools/call",
        {"name": tool_name, "arguments": arguments or {}},
        rpc_id=tool_name,
    )
    response = _post_jsonrpc(mcp_url, bearer_key, payload, timeout=timeout)
    if "error" in response:
        _raise_jsonrpc_error(response["error"])
    result = response.get("result")
    if not isinstance(result, dict):
        raise SkillError("INTERNAL", "MCP tools/call returned an invalid result.")
    return result


def extract_tool_payload(result):
    if result.get("isError"):
        _raise_tool_error(result)
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    text_payload = _first_json_text(result)
    if isinstance(text_payload, dict):
        return text_payload
    raise SkillError("INTERNAL", "MCP tool result did not contain structured JSON.")


def _post_jsonrpc(mcp_url, bearer_key, payload, timeout):
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if bearer_key:
        headers["Authorization"] = "Bearer " + bearer_key
    req = Request(
        mcp_url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return _decode_jsonrpc_response(_headers_dict(resp), raw)
    except HTTPError as exc:
        body = exc.read()
        _raise_http_error(exc.code, body)
    except URLError as exc:
        raise SkillError("NETWORK", f"Cannot reach CUA Skill MCP at {mcp_url}: {exc.reason}")
    except TimeoutError:
        raise SkillError("NETWORK", f"Request to CUA Skill MCP timed out: {mcp_url}")


def _jsonrpc(method, params, rpc_id):
    return {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params}


def _decode_jsonrpc_response(headers, raw):
    text = raw.decode("utf-8", errors="replace")
    content_type = headers.get("content-type", "")
    if "text/event-stream" in content_type or text.lstrip().startswith(("event:", "data:")):
        return _decode_sse(text)
    try:
        payload = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError as exc:
        raise SkillError("INTERNAL", f"MCP returned non-JSON response: {text[:200]}") from exc
    if not isinstance(payload, dict):
        raise SkillError("INTERNAL", "MCP JSON-RPC response was not an object.")
    return payload


def _decode_sse(text):
    events = []
    data_lines = []
    for line in text.splitlines():
        if not line.strip():
            if data_lines:
                events.append("\n".join(data_lines))
                data_lines = []
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())
    if data_lines:
        events.append("\n".join(data_lines))

    candidate = None
    for data in events:
        if not data or data == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and ("result" in payload or "error" in payload):
            candidate = payload
    if candidate is None:
        raise SkillError("INTERNAL", "MCP SSE response did not contain a JSON-RPC payload.")
    return candidate


def _raise_tool_error(result):
    payload = _first_json_text(result)
    message = "CUA MCP tool returned an error."
    status = None
    code = None
    if isinstance(payload, dict):
        message = payload.get("message") or payload.get("error") or message
        status = payload.get("status")
        code = payload.get("code")
    _raise_mapped_error(code, status, message)


def _raise_jsonrpc_error(error):
    if not isinstance(error, dict):
        raise SkillError("INTERNAL", "MCP JSON-RPC error.")
    data = error.get("data") if isinstance(error.get("data"), dict) else {}
    _raise_mapped_error(data.get("code"), data.get("status"), error.get("message") or "MCP call failed.")


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
            code = error.get("code")
            _raise_mapped_error(code, status, message)
    _raise_mapped_error(None, status, message)


def _raise_mapped_error(code, status, message):
    if code:
        stable = str(code)
        extra = {}
        if stable in ("AUTH_REQUIRED", "TOKEN_EXPIRED", "REFRESH_FAILED"):
            extra["retry_command"] = login_retry_command()
        raise SkillError(stable, message, **extra)

    if status in (401, "401"):
        raise SkillError("AUTH_REQUIRED", message or "Login required for CUA Skill.", retry_command=login_retry_command())
    if status in (403, "403"):
        raise SkillError("FORBIDDEN", message or "CUA Skill credential is forbidden.")
    if status in (409, "409") and "desktop" in (message or "").lower():
        raise SkillError("DESKTOP_NOT_BOUND", message)
    if status in (429, "429"):
        raise SkillError("RATE_LIMITED", message or "CUA Skill MCP rate limited the request.")
    if status in (502, 503, "502", "503"):
        raise SkillError("CUA_BACKEND_UNAVAILABLE", message or "CUA backend is unavailable.")
    if status in (504, "504"):
        raise SkillError("GATEWAY_TIMEOUT", message or "CUA Skill MCP timed out; the task may still be running.")
    raise SkillError("INTERNAL", message or "CUA Skill MCP returned an unexpected error.")


def _first_json_text(result):
    content = result.get("content") if isinstance(result, dict) else None
    if not isinstance(content, list):
        return None
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        text = item.get("text")
        if not isinstance(text, str):
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"message": text}
    return None


def _headers_dict(response):
    return {k.lower(): v for k, v in response.headers.items()}
