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
import time
import uuid
from pathlib import Path

import cua_auth
from cua_http import PUBLIC_ERROR_CODES, gateway_manifest, gateway_tool_call
from cua_state import AuthState, SessionState
from cua_util import SkillError, emit_error, emit_success, ext_for_mime, script_path

IDEMPOTENT_RETRIES = 2
SERVER_WAIT_CHUNK_MS = 60000
TERMINAL_OUTCOMES = ("completed", "failed", "cancelled")
DESKTOP_REBOOT_DEFAULT_WAIT_MS = 600000
DESKTOP_OPERATION_POLL_INTERVAL_SEC = 2


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
        {},
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


def cmd_desktops_list(args, state, session):
    access_hub, gateway_url = resolve_urls(args, state)
    token = cua_auth.ensure_bearer_key(state, access_hub)
    data = gateway_tool_call(
        gateway_url,
        token,
        "cua_list_desktops",
        {},
        timeout=30,
    )
    session.remember_desktops(data.get("desktops") or [])
    return {"data": data}


def cmd_desktops_allocate(args, state, session):
    access_hub, gateway_url = resolve_urls(args, state)
    token = cua_auth.ensure_bearer_key(state, access_hub)
    request = {}
    if args.spec_code:
        request["spec_code"] = args.spec_code
    if args.label:
        request["label"] = args.label
    data = gateway_tool_call(gateway_url, token, "cua_allocate_desktop", request, timeout=60)
    desktop = data.get("desktop") if isinstance(data.get("desktop"), dict) else {}
    if desktop.get("desktop_id"):
        session.remember_desktops([desktop])
    return {"data": data}


def cmd_desktops_use(args, state, session):
    access_hub, gateway_url = resolve_urls(args, state)
    token = cua_auth.ensure_bearer_key(state, access_hub)
    data = gateway_tool_call(
        gateway_url,
        token,
        "cua_list_desktops",
        {},
        timeout=30,
    )
    desktops = data.get("desktops") or []
    session.remember_desktops(desktops)
    selected = _find_desktop(desktops, args.desktop_id)
    if not selected:
        raise SkillError("DESKTOP_NOT_BOUND", f"Desktop {args.desktop_id} is not allocated to this user.")
    desktop_id = selected.get("desktop_id")
    session.set_default_desktop_id(desktop_id)
    return {"data": {"desktop": selected, "default_desktop_id": desktop_id}}


def cmd_desktops_reboot(args, state, session):
    access_hub, gateway_url = resolve_urls(args, state)
    desktop_id = args.desktop_id or session.default_desktop_id
    request = {"idempotency_key": f"cua-skill-reboot-{uuid.uuid4().hex}"}
    if desktop_id:
        request["desktop_id"] = desktop_id
    data = cua_auth.authorized_tool_call(
        state,
        access_hub,
        gateway_url,
        "cua_reboot_desktop",
        request,
        timeout=30,
        retries=IDEMPOTENT_RETRIES,
    )
    operation = _desktop_operation(data)
    operation = _wait_desktop_operation(
        state,
        access_hub,
        gateway_url,
        operation,
        _wait_ms(args.wait_ms),
    )
    _require_successful_desktop_reboot(operation)
    return {
        "data": {
            "desktop": data.get("desktop"),
            "operation": operation,
        },
        "next": {
            "agent_hint": "Desktop reboot and readiness checks succeeded. It is now safe to submit a new task.",
        },
    }


def cmd_desktops_operation(args, state, session):
    access_hub, gateway_url = resolve_urls(args, state)
    operation = _get_desktop_operation(
        state,
        access_hub,
        gateway_url,
        args.operation_id,
    )
    operation = _wait_desktop_operation(
        state,
        access_hub,
        gateway_url,
        operation,
        _wait_ms(args.wait_ms),
    )
    _require_successful_desktop_reboot(operation)
    return {
        "data": {"operation": operation},
        "next": {
            "agent_hint": "Desktop reboot and readiness checks succeeded. It is now safe to submit a new task.",
        },
    }


def _desktop_operation(data):
    operation = data.get("operation") if isinstance(data, dict) else None
    if not isinstance(operation, dict) or not operation.get("operation_id"):
        raise SkillError("INTERNAL", "CUA Skill Gateway returned a desktop operation without an operation_id.")
    return operation


def _get_desktop_operation(state, access_hub, gateway_url, operation_id):
    data = cua_auth.authorized_tool_call(
        state,
        access_hub,
        gateway_url,
        "cua_get_desktop_operation",
        {"operation_id": operation_id},
        timeout=30,
        retries=IDEMPOTENT_RETRIES,
    )
    return _desktop_operation(data)


def _wait_desktop_operation(state, access_hub, gateway_url, operation, wait_ms):
    deadline = time.monotonic() + (wait_ms / 1000)
    while str(operation.get("status") or "").lower() == "running":
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return operation
        time.sleep(min(DESKTOP_OPERATION_POLL_INTERVAL_SEC, remaining))
        operation = _get_desktop_operation(
            state,
            access_hub,
            gateway_url,
            operation["operation_id"],
        )
    return operation


def _require_successful_desktop_reboot(operation):
    status = str(operation.get("status") or "").lower()
    if status == "succeeded":
        return
    operation_id = operation.get("operation_id")
    if status == "running":
        raise SkillError(
            "DESKTOP_REBOOT_IN_PROGRESS",
            "Desktop reboot is still running. Check the operation before submitting a new task.",
            operation=operation,
            retry_command=f"python3 {script_path()} desktops operation {operation_id}",
        )
    error = operation.get("error") if isinstance(operation.get("error"), dict) else {}
    upstream_code = str(error.get("code") or "DesktopRebootFailed")
    public_code = "DESKTOP_REBOOT_FAILED"
    if upstream_code in ("BrokerNotReady", "UIANotReady", "SpiceAgentNotReady"):
        public_code = "DESKTOP_UNHEALTHY"
    raise SkillError(
        public_code,
        error.get("message") or "Desktop reboot or readiness checks failed.",
        upstream_code=upstream_code,
        source="desktop_runtime",
        stage="desktop_reboot",
        accepted=True,
        operation=operation,
    )


def cmd_delegate(args, state, session):
    access_hub, gateway_url = resolve_urls(args, state)
    wait_ms = _wait_ms(args.wait_ms)
    request = {"input": args.objective}
    session_id = str(getattr(args, "session_id", None) or "").strip()
    if session_id and args.auto:
        raise SkillError(
            "VALIDATION_ERROR",
            "--session-id cannot be combined with --auto; pass the session's original --desktop-id instead.",
        )
    if session_id:
        request["session_id"] = session_id
    desktop_id = _resolve_delegate_desktop(args, state, session, access_hub, gateway_url)
    if desktop_id:
        request["desktop_id"] = desktop_id
    payload = cua_auth.authorized_tool_call(
        state,
        access_hub,
        gateway_url,
        "cua_run_task",
        request,
        timeout=_tool_timeout(None),
    )
    if desktop_id:
        session.set_last_task_desktop_id(desktop_id)
    if wait_ms and wait_ms > 0:
        payload = _wait_task_with_budget(
            state,
            access_hub,
            gateway_url,
            payload.get("task_id"),
            wait_ms,
        )
    return _envelope_result(_task_envelope(payload), session)


def cmd_watch(args, state, session):
    access_hub, gateway_url = resolve_urls(args, state)
    invocation_id = _resolve_invocation_id(args, session)
    wait_ms = _wait_ms(args.wait_ms)
    payload = _wait_task_with_budget(
        state,
        access_hub,
        gateway_url,
        invocation_id,
        wait_ms,
    )
    return _envelope_result(_task_envelope(payload), session)


def cmd_tasks_list(args, state, session):
    access_hub, gateway_url = resolve_urls(args, state)
    token = cua_auth.ensure_bearer_key(state, access_hub)
    request = {"status": args.status, "limit": args.limit}
    data = gateway_tool_call(
        gateway_url,
        token,
        "cua_list_tasks",
        request,
        timeout=30,
    )
    return {"data": data}


def cmd_tasks_watch(args, state, session):
    access_hub, gateway_url = resolve_urls(args, state)
    token = cua_auth.ensure_bearer_key(state, access_hub)
    task_ids = list(args.task_id or [])
    if args.last:
        task_ids.append(_resolve_invocation_id(args, session))
    task_ids = _compact(task_ids)
    if not task_ids:
        raise SkillError("VALIDATION_ERROR", "Pass one or more --task-id values, or use --last.")
    wait_ms = _wait_ms(args.wait_ms)
    data = _watch_tasks_with_budget(
        gateway_url,
        token,
        task_ids,
        args.include_upstream,
        wait_ms,
    )
    envelopes = []
    for item in data.get("tasks") or []:
        if not isinstance(item, dict):
            continue
        envelopes.append(_task_envelope(item))
    for envelope in envelopes:
        invocation_id = envelope.get("invocation_id")
        if invocation_id:
            session.set_last_invocation_id(invocation_id)
    return {
        "data": {
            "tasks": envelopes,
            "count": data.get("count", len(envelopes)),
            "completed_count": data.get("completed_count"),
            "failed_count": data.get("failed_count"),
            "cancelled_count": data.get("cancelled_count"),
            "needs_input_count": data.get("needs_input_count"),
            "pending_count": data.get("pending_count"),
            "terminal_count": data.get("terminal_count"),
            "settled_count": data.get("settled_count"),
        },
        "next": {
            "agent_hint": "Use completed task result.text as authoritative. Keep unfinished task ids and call tasks watch again later.",
        },
    }


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
        timeout=_tool_timeout(None),
    )
    if wait_ms and wait_ms > 0:
        payload = _wait_task_with_budget(
            state,
            access_hub,
            gateway_url,
            invocation_id,
            wait_ms,
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
    request = {}
    desktop_id = args.desktop_id or session.default_desktop_id
    if desktop_id:
        request["desktop_id"] = desktop_id
    data = gateway_tool_call(gateway_url, token, "cua_get_desktop_access", request, timeout=60)
    if args.include_screenshot:
        shot_request = {}
        if desktop_id:
            shot_request["desktop_id"] = desktop_id
        shot = gateway_tool_call(gateway_url, token, "cua_take_screenshot", shot_request, timeout=60)
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


def _resolve_delegate_desktop(args, state, session, access_hub, gateway_url):
    if args.desktop_id:
        return args.desktop_id
    if not args.auto:
        return session.default_desktop_id
    token = cua_auth.ensure_bearer_key(state, access_hub)
    listing = gateway_tool_call(
        gateway_url,
        token,
        "cua_list_desktops",
        {},
        timeout=30,
    )
    desktops = listing.get("desktops") or []
    quota = listing.get("quota") if isinstance(listing.get("quota"), dict) else {}
    session.remember_desktops(desktops)
    selected = _select_idle_desktop(desktops)
    if selected:
        return selected.get("desktop_id")
    max_active = int(quota.get("max_active_cuas") or 0)
    active_count = int(quota.get("active_count") or len(desktops))
    if not desktops or (max_active > 0 and active_count < max_active):
        allocated = gateway_tool_call(
            gateway_url,
            token,
            "cua_allocate_desktop",
            {},
            timeout=60,
        )
        desktop = allocated.get("desktop") if isinstance(allocated.get("desktop"), dict) else {}
        if desktop.get("desktop_id"):
            session.remember_desktops([desktop])
            return desktop.get("desktop_id")
    raise SkillError(
        "DESKTOP_BUSY",
        "All allocated CUA desktops are busy and quota is full. Wait for a task to finish or pass --desktop-id explicitly.",
        upstream_code="NO_IDLE_DESKTOP",
        source="skill_gateway",
        stage="desktop_resolve",
        accepted=False,
        quota=quota,
    )


def _select_idle_desktop(desktops):
    default = None
    first_idle = None
    for desktop in desktops:
        if not isinstance(desktop, dict):
            continue
        if not desktop.get("desktop_id"):
            continue
        if desktop.get("is_default"):
            default = desktop
        if not _desktop_busy(desktop) and first_idle is None:
            first_idle = desktop
            if not desktop.get("is_default"):
                continue
        if desktop.get("is_default") and not _desktop_busy(desktop):
            return desktop
    if first_idle:
        return first_idle
    if default and not _desktop_busy(default):
        return default
    return None


def _desktop_busy(desktop):
    if desktop.get("busy") is True or desktop.get("current_task_id"):
        return True
    status = str(desktop.get("current_task_status") or "").strip().lower()
    return status and status not in ("succeeded", "completed", "success", "failed", "error", "cancelled", "canceled")


def _find_desktop(desktops, selector):
    selector = str(selector or "").strip()
    if not selector:
        return None
    for desktop in desktops:
        if not isinstance(desktop, dict):
            continue
        aliases = {
            str(desktop.get("desktop_id") or "").strip(),
            str(desktop.get("cua_uid") or "").strip(),
            str(desktop.get("instance_name") or "").strip(),
            str(desktop.get("name") or "").strip(),
        }
        if selector in aliases:
            return desktop
    return None


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
    session_id = (
        payload.get("mycua_session_id")
        or task.get("mycua_session_id")
        or run.get("session_id")
        or run.get("sessionId")
    )
    run_id = payload.get("mycua_run_id") or task.get("mycua_run_id") or run.get("id")
    diagnostics = {
        "trace_id": run_id,
        "mycua_session_id": session_id,
        "mycua_run_id": run_id,
        "raw_status": status,
    }
    diagnostics.update(_error_diagnostics(payload, task, upstream))
    failure = _terminal_failure(upstream) if outcome == "failed" else None
    if failure:
        diagnostics.update(failure)
    return {
        "invocation_id": task_id,
        "session_id": session_id,
        "outcome": outcome,
        "result": {
            "text": _result_text(upstream) if outcome == "completed" else None,
            "artifacts": _artifacts(upstream),
            "error": failure,
        },
        "input_request": _input_request(upstream) if outcome == "needs_input" else None,
        "progress": {
            "summary": _progress_summary(status, upstream),
            "step_count": 0,
            "updated_at": _updated_at(upstream),
        },
        "next_action": _next_action(outcome),
        "diagnostics": diagnostics,
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
    candidates = [
        upstream.get("text"),
        upstream.get("finalText"),
        upstream.get("final_text"),
        upstream.get("outputText"),
        upstream.get("output_text"),
    ]
    for key in ("result", "output"):
        value = upstream.get(key)
        if isinstance(value, dict):
            candidates.extend([
                value.get("text"),
                value.get("finalText"),
                value.get("final_text"),
                value.get("outputText"),
                value.get("output_text"),
            ])
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
    if not isinstance(upstream, dict):
        return []
    candidates = []
    _extend_artifacts(candidates, upstream.get("artifacts"))
    for key in ("result", "output"):
        value = upstream.get(key)
        if isinstance(value, dict):
            _extend_artifacts(candidates, value.get("artifacts"))
            for single_key in ("artifact", "file", "image"):
                artifact = value.get(single_key)
                if isinstance(artifact, dict):
                    candidates.append(artifact)
    for single_key in ("artifact", "file", "image"):
        artifact = upstream.get(single_key)
        if isinstance(artifact, dict):
            candidates.append(artifact)

    normalized = []
    seen = set()
    for artifact in candidates:
        item = _normalize_artifact(artifact)
        identity = item.get("id") or item.get("url") or item.get("path") or json.dumps(item, sort_keys=True)
        if identity in seen:
            continue
        seen.add(identity)
        normalized.append(item)
    return normalized


def _extend_artifacts(out, value):
    if isinstance(value, list):
        out.extend(item for item in value if isinstance(item, dict))


def _normalize_artifact(artifact):
    meta = artifact.get("meta") if isinstance(artifact.get("meta"), dict) else {}
    mime_type = _first_string(artifact, "mime_type", "mimeType", "content_type", "contentType")
    kind = _first_string(artifact, "kind", "type")
    item = {
        "id": _first_string(artifact, "id", "artifact_id", "artifactId"),
        "type": _artifact_type(kind, mime_type),
        "kind": kind,
        "mime_type": mime_type,
        "name": _first_string(artifact, "name", "filename", "file_name", "fileName") or _first_string(meta, "title", "name"),
        "url": _first_string(artifact, "url", "download_url", "downloadUrl"),
        "path": _first_string(artifact, "path", "file_path", "filePath"),
        "size_bytes": _first_number(artifact, "size_bytes", "sizeBytes"),
        "width": _first_number(artifact, "width"),
        "height": _first_number(artifact, "height"),
        "status": _first_string(artifact, "storage_status", "storageStatus", "status"),
        "created_at": _first_string(artifact, "created_at", "createdAt"),
        "updated_at": _first_string(artifact, "updated_at", "updatedAt", "last_verified_at", "lastVerifiedAt"),
        "message_id": _first_string(artifact, "message_id", "messageId"),
        "run_id": _first_string(artifact, "run_id", "runId"),
        "title": _first_string(meta, "title"),
        "source_url": _first_string(meta, "url", "source_url", "sourceUrl"),
        "placeholder_text": _first_string(artifact, "placeholder_text", "placeholderText"),
        "meta": meta,
    }
    content = _artifact_text_content(artifact)
    if content:
        item["text"] = _truncate_text(content, 20000)
        if len(content) > 20000:
            item["truncated"] = True
    preview = _first_string(artifact, "preview", "preview_text", "previewText", "summary")
    if preview:
        item["preview_text"] = _truncate_text(preview, 2000)
    return {key: value for key, value in item.items() if value not in (None, "", {})}


def _artifact_type(kind, mime_type):
    kind_l = str(kind or "").lower()
    mime_l = str(mime_type or "").lower()
    if kind_l == "browser_snapshot":
        return "browser_snapshot"
    if mime_l.startswith("image/") or kind_l in ("image", "screenshot", "annotation"):
        return "image"
    if _is_text_mime(mime_l) or kind_l in ("text", "markdown", "json", "csv"):
        return "text"
    return "file"


def _is_text_mime(mime_type):
    if not mime_type:
        return False
    if mime_type.startswith("text/"):
        return True
    return mime_type.split(";", 1)[0] in {
        "application/json",
        "application/ld+json",
        "application/markdown",
        "application/xml",
        "application/yaml",
        "application/x-yaml",
        "application/javascript",
    }


def _artifact_text_content(artifact):
    for key in ("text", "content_text", "contentText", "content", "body"):
        value = artifact.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _first_string(source, *keys):
    for key in keys:
        value = source.get(key) if isinstance(source, dict) else None
        if isinstance(value, str) and value.strip():
            return value
    return None


def _first_number(source, *keys):
    for key in keys:
        value = source.get(key) if isinstance(source, dict) else None
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return value
    return None


def _truncate_text(value, limit):
    if len(value) <= limit:
        return value
    return value[:limit]


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


def _compact(values):
    out = []
    seen = set()
    for value in values:
        value = str(value or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _wait_task_with_budget(state, access_hub, gateway_url, task_id, wait_ms):
    remaining_ms = wait_ms
    while True:
        request = {"task_id": task_id}
        chunk_ms = None
        if remaining_ms is not None:
            chunk_ms = min(SERVER_WAIT_CHUNK_MS, remaining_ms)
            request["timeout_seconds"] = _wait_seconds(chunk_ms)
        payload = cua_auth.authorized_tool_call(
            state,
            access_hub,
            gateway_url,
            "cua_wait_task",
            request,
            timeout=_tool_timeout(chunk_ms),
            retries=IDEMPOTENT_RETRIES,
        )
        if _task_envelope(payload).get("outcome") != "in_progress":
            return payload
        if remaining_ms is None or remaining_ms <= chunk_ms:
            return payload
        remaining_ms -= chunk_ms


def _watch_tasks_with_budget(gateway_url, token, task_ids, include_upstream, wait_ms):
    remaining_ms = wait_ms
    while True:
        request = {"task_ids": task_ids, "include_upstream": include_upstream}
        chunk_ms = None
        if remaining_ms is not None:
            chunk_ms = min(SERVER_WAIT_CHUNK_MS, remaining_ms)
            request["timeout_seconds"] = _wait_seconds(chunk_ms)
        data = gateway_tool_call(
            gateway_url,
            token,
            "cua_watch_tasks",
            request,
            timeout=_tool_timeout(chunk_ms),
        )
        if data.get("pending_count") == 0:
            return data
        if remaining_ms is None or remaining_ms <= chunk_ms:
            return data
        remaining_ms -= chunk_ms


def _error_diagnostics(payload, task, upstream):
    diagnostics = {}
    sources = [upstream]
    for key in ("error", "upstream", "upstream_body"):
        value = upstream.get(key) if isinstance(upstream, dict) else None
        if isinstance(value, dict):
            sources.append(value)

    error_value = upstream.get("error") if isinstance(upstream, dict) else None
    if isinstance(error_value, str) and error_value.strip():
        diagnostics["error"] = error_value

    field_aliases = {
        "code": ("code", "error_code", "errorCode"),
        "reason": ("reason", "error_description", "failure_reason"),
        "message": ("message",),
        "upstream_code": ("upstream_code",),
        "upstream_status": ("upstream_status",),
        "source": ("source",),
        "stage": ("stage",),
        "accepted": ("accepted",),
        "retryable": ("retryable",),
        "retry_after_ms": ("retry_after_ms",),
        "error_schema_version": ("error_schema_version",),
    }
    for output_key, aliases in field_aliases.items():
        for source in sources:
            value = _first_value(source, *aliases)
            if value is not None:
                diagnostics[output_key] = value
                break

    request_id = _first_value(payload, "request_id", "gateway_request_id") or _first_value(task, "request_id")
    if request_id is not None:
        diagnostics["request_id"] = request_id
    context = upstream.get("context") if isinstance(upstream, dict) else None
    if isinstance(context, dict) and context:
        diagnostics["context"] = context
    return diagnostics


def _terminal_failure(upstream):
    if not isinstance(upstream, dict):
        return None
    candidates = []
    for parent in (
        upstream,
        upstream.get("result"),
        upstream.get("run"),
    ):
        if not isinstance(parent, dict):
            continue
        error = parent.get("error")
        if isinstance(error, dict):
            candidates.append(error)
        candidates.append(parent)

    raw_code = None
    message = None
    details = {}
    for candidate in candidates:
        if raw_code is None:
            raw_code = _first_string(candidate, "code", "error_code", "errorCode")
            raw_error = candidate.get("error")
            if raw_code is None and isinstance(raw_error, str) and _looks_like_error_code(raw_error):
                raw_code = raw_error.strip()
        if message is None:
            message = _first_string(
                candidate,
                "message",
                "error_message",
                "errorMessage",
                "reason",
                "error_description",
            )
            raw_error = candidate.get("error")
            if message is None and isinstance(raw_error, str) and not _looks_like_error_code(raw_error):
                message = raw_error.strip()
        for key in (
            "error_schema_version",
            "source",
            "stage",
            "accepted",
            "retryable",
            "reason",
            "upstream_code",
            "upstream_status",
            "retry_after_ms",
        ):
            if key not in details and candidate.get(key) not in (None, ""):
                details[key] = candidate.get(key)
        if "context" not in details and isinstance(candidate.get("context"), dict):
            details["context"] = candidate["context"]

    public_code = _terminal_public_error_code(raw_code, details.get("upstream_status"))
    if raw_code and public_code != raw_code and not details.get("upstream_code"):
        details["upstream_code"] = raw_code
    details["code"] = public_code
    unsafe_internal_message = (
        public_code == "UPSTREAM_FAILURE"
        and raw_code not in (None, "", "UPSTREAM_FAILURE")
    )
    details["message"] = (
        _terminal_error_message(public_code)
        if unsafe_internal_message
        else message or _terminal_error_message(public_code)
    )
    details.setdefault("reason", details["message"])
    details.setdefault("error_schema_version", "cua.error.v1")
    details.setdefault("source", _terminal_error_source(public_code, raw_code, details.get("upstream_code")))
    details.setdefault("stage", _terminal_error_stage(details["source"]))
    details.setdefault("accepted", True)
    details.setdefault("retryable", public_code in {
        "RATE_LIMITED",
        "MODEL_TIMEOUT",
        "UPSTREAM_TIMEOUT",
        "CUA_BACKEND_UNAVAILABLE",
    })
    return details


def _terminal_public_error_code(raw_code, upstream_status):
    normalized = str(raw_code or "").strip()
    lower = normalized.lower()
    aliases = {
        "active_run_conflict": "DESKTOP_BUSY",
        "desktop_busy": "DESKTOP_BUSY",
        "model_timeout": "MODEL_TIMEOUT",
        "provider_timeout": "MODEL_TIMEOUT",
        "llm_timeout": "MODEL_TIMEOUT",
        "model_rate_limited": "RATE_LIMITED",
        "provider_rate_limited": "RATE_LIMITED",
        "rate_limited": "RATE_LIMITED",
        "ratelimited": "RATE_LIMITED",
        "model_auth_failed": "UPSTREAM_FAILURE",
        "provider_request_failed": "UPSTREAM_FAILURE",
        "provider_response_invalid": "UPSTREAM_FAILURE",
        "provider_stream_failed": "UPSTREAM_FAILURE",
        "provider_pool_exhausted": "UPSTREAM_FAILURE",
        "desktop_unhealthy": "DESKTOP_UNHEALTHY",
        "guest_unhealthy": "DESKTOP_UNHEALTHY",
        "session_cleanup": "SESSION_CLEANUP",
        "session_cleanup_failed": "SESSION_CLEANUP",
        "upstream_timeout": "UPSTREAM_TIMEOUT",
        "upstreamtimeout": "UPSTREAM_TIMEOUT",
        "invalidaccesshubresponse": "UPSTREAM_PROTOCOL_ERROR",
        "invalidmycuaresponse": "UPSTREAM_PROTOCOL_ERROR",
        "upstream_protocol_error": "UPSTREAM_PROTOCOL_ERROR",
    }
    if normalized in PUBLIC_ERROR_CODES:
        return normalized
    if lower in aliases:
        return aliases[lower]
    try:
        status = int(upstream_status) if upstream_status is not None else None
    except (TypeError, ValueError):
        status = None
    status_codes = {
        400: "VALIDATION_ERROR",
        401: "AUTH_REQUIRED",
        403: "FORBIDDEN",
        404: "INVOCATION_NOT_FOUND",
        409: "CONFLICT",
        429: "RATE_LIMITED",
        502: "CUA_BACKEND_UNAVAILABLE",
        503: "CUA_BACKEND_UNAVAILABLE",
        504: "UPSTREAM_TIMEOUT",
    }
    return status_codes.get(status, "UPSTREAM_FAILURE")


def _terminal_error_source(public_code, raw_code, upstream_code):
    signal = " ".join(str(value or "").lower() for value in (raw_code, upstream_code))
    if public_code == "MODEL_TIMEOUT" or (
        public_code == "RATE_LIMITED"
        and any(fragment in signal for fragment in (
            "model",
            "provider",
            "ratelimit",
            "rate_limit",
            "too_many_requests",
            "toomanyrequests",
        ))
    ):
        return "model_provider"
    if public_code in ("DESKTOP_BUSY", "DESKTOP_UNHEALTHY"):
        return "desktop_runtime"
    return "my_cua"


def _terminal_error_stage(source):
    if source == "model_provider":
        return "model_execute"
    if source == "desktop_runtime":
        return "desktop_execute"
    return "run_execute"


def _terminal_error_message(code):
    messages = {
        "RATE_LIMITED": "model provider rate limited the request",
        "MODEL_TIMEOUT": "model provider request timed out",
        "UPSTREAM_FAILURE": "CUA failed with an internal upstream error",
    }
    return messages.get(code, "CUA could not complete the request")


def _looks_like_error_code(value):
    text = str(value or "").strip()
    return bool(text and " " not in text and ("\n" not in text) and ("_" in text or text.isupper()))


def _first_value(source, *keys):
    if not isinstance(source, dict):
        return None
    for key in keys:
        value = source.get(key)
        if value is not None and value != "":
            return value
    return None


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

    desktops = sub.add_parser("desktops", help="List, allocate, select, or reboot CUA desktops.")
    desktops_sub = desktops.add_subparsers(dest="desktops_command")

    p = desktops_sub.add_parser("list", help="List allocated CUA desktops and quota.")
    p.set_defaults(action="desktops.list", handler=cmd_desktops_list)

    p = desktops_sub.add_parser("allocate", help="Allocate an additional CUA desktop.")
    p.add_argument("--spec-code", help="Optional CUA spec code.")
    p.add_argument("--label", help="Optional human label for this desktop.")
    p.set_defaults(action="desktops.allocate", handler=cmd_desktops_allocate)

    p = desktops_sub.add_parser("use", help="Set the local default desktop for observe and delegate.")
    p.add_argument("desktop_id", help="Desktop id, CUA uid, or instance name.")
    p.set_defaults(action="desktops.use", handler=cmd_desktops_use)

    p = desktops_sub.add_parser("reboot", help="Reboot a desktop and wait for readiness checks.")
    p.add_argument("desktop_id", nargs="?", help="Optional desktop id; defaults to the selected desktop.")
    p.add_argument(
        "--wait-ms",
        type=int,
        default=DESKTOP_REBOOT_DEFAULT_WAIT_MS,
        help="Total wait budget in milliseconds (default: 600000).",
    )
    p.set_defaults(action="desktops.reboot", handler=cmd_desktops_reboot)

    p = desktops_sub.add_parser("operation", help="Wait for a desktop reboot operation.")
    p.add_argument("operation_id", help="Operation id returned by desktops reboot.")
    p.add_argument(
        "--wait-ms",
        type=int,
        default=DESKTOP_REBOOT_DEFAULT_WAIT_MS,
        help="Total wait budget in milliseconds (default: 600000).",
    )
    p.set_defaults(action="desktops.operation", handler=cmd_desktops_operation)

    p = sub.add_parser("delegate", help="Delegate a user objective to a new or existing CUA session.")
    p.add_argument("--objective", required=True, help="The user's original objective.")
    p.add_argument("--desktop-id", help="Optional desktop id/cua uid/instance name.")
    p.add_argument(
        "--session-id",
        help="Optional existing my-cua session id. Omit it to create a new session.",
    )
    p.add_argument("--auto", action="store_true", help="Choose an idle desktop or allocate one if quota allows.")
    p.add_argument("--wait-ms", type=int, default=None, help="Optional total wait budget in milliseconds; server calls are chunked at 60 seconds.")
    p.set_defaults(action="delegate", handler=cmd_delegate)

    p = sub.add_parser("watch", help="Wait for or check an invocation.")
    p.add_argument("--invocation-id", help="Invocation id returned by delegate.")
    p.add_argument("--last", action="store_true", help="Use the latest saved invocation id.")
    p.add_argument("--wait-ms", type=int, default=None, help="Optional total wait budget in milliseconds; server calls are chunked at 60 seconds.")
    p.set_defaults(action="watch", handler=cmd_watch)

    tasks = sub.add_parser("tasks", help="List or watch multiple delegated CUA tasks.")
    tasks_sub = tasks.add_subparsers(dest="tasks_command")

    p = tasks_sub.add_parser("list", help="List delegated CUA tasks.")
    p.add_argument("--status", default="active", help="Filter: active, all, or an exact status.")
    p.add_argument("--limit", type=int, default=20, help="Maximum tasks to return.")
    p.set_defaults(action="tasks.list", handler=cmd_tasks_list)

    p = tasks_sub.add_parser("watch", help="Refresh or wait on several CUA tasks.")
    p.add_argument("--task-id", action="append", help="Task/invocation id returned by delegate. Repeat for multiple tasks.")
    p.add_argument("--last", action="store_true", help="Also include the latest saved invocation id.")
    p.add_argument("--wait-ms", type=int, default=None, help="Optional total wait budget in milliseconds; server calls are chunked at 60 seconds.")
    p.add_argument("--include-upstream", action="store_true", help="Include raw gateway/my-cua status for non-terminal tasks.")
    p.set_defaults(action="tasks.watch", handler=cmd_tasks_watch)

    p = sub.add_parser("answer", help="Submit the user's answer to CUA.")
    p.add_argument("--invocation-id", help="Invocation id returned by delegate.")
    p.add_argument("--last", action="store_true", help="Use the latest saved invocation id.")
    p.add_argument("--answer", required=True, help="The user's answer to input_request.question.")
    p.add_argument("--wait-ms", type=int, default=None, help="Optional total wait budget in milliseconds; server calls are chunked at 60 seconds.")
    p.set_defaults(action="answer", handler=cmd_answer)

    p = sub.add_parser("cancel", help="Cancel an invocation when the user asks to stop.")
    p.add_argument("--invocation-id", help="Invocation id returned by delegate.")
    p.add_argument("--last", action="store_true", help="Use the latest saved invocation id.")
    p.set_defaults(action="cancel", handler=cmd_cancel)

    p = sub.add_parser("observe", help="Read-only desktop state and temporary access URL.")
    p.add_argument("--invocation-id", help="Accepted for compatibility; desktop access is caller-scoped.")
    p.add_argument("--last", action="store_true", help="Accepted for compatibility; desktop access is caller-scoped.")
    p.add_argument("--desktop-id", help="Optional desktop id/cua uid/instance name.")
    p.add_argument("--include-screenshot", action="store_true", help="Save an optional screenshot to a temp file.")
    p.set_defaults(action="observe", handler=cmd_observe)

    p = sub.add_parser("self-test", help="Validate local install; --online also checks gateway auth.")
    p.add_argument("--online", action="store_true", help="Run online manifest and desktop-access checks.")
    p.set_defaults(action="self-test", handler=cmd_self_test)

    return parser


if __name__ == "__main__":
    raise SystemExit(main())
