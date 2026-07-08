"""Local caches for the ByteSSO CUA Skill CLI."""

import json
import os
import stat
import tempfile
from pathlib import Path

from cua_util import SkillError

DEFAULT_DIR = Path.home() / ".openclaw" / "cua-skill-bytesso"
DEFAULT_AUTH_FILE = DEFAULT_DIR / "auth.json"


def auth_file_path():
    override = os.environ.get("CUA_SKILL_AUTH_FILE")
    return Path(override).expanduser() if override else DEFAULT_AUTH_FILE


def session_file_path():
    override = os.environ.get("CUA_SKILL_SESSION_FILE")
    if override:
        return Path(override).expanduser()
    return auth_file_path().parent / "session.json"


class _JsonFile:
    def __init__(self, path, data):
        self.path = path
        self.data = data

    @classmethod
    def load(cls, path):
        if not path.exists():
            return cls(path, {})
        _ensure_secure_permissions(path)
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SkillError("INTERNAL", f"Cannot read {path}: {exc}")
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            raise SkillError("INTERNAL", f"{path} is corrupted; run auth login again")
        return cls(path, data)

    def save(self):
        path = self.path
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(self.data, handle, ensure_ascii=False, indent=2)
                os.chmod(tmp, 0o600)
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        except OSError as exc:
            raise SkillError("INTERNAL", f"Cannot persist {path}: {exc}")


class AuthState(_JsonFile):
    @classmethod
    def load(cls):
        return super().load(auth_file_path())

    @property
    def access_hub_base_url(self):
        return self.data.get("access_hub_base_url")

    @property
    def gateway_url(self):
        return self.data.get("gateway_url") or self.data.get("mcp_url")

    @property
    def mcp_url(self):
        return self.data.get("mcp_url")

    @property
    def bearer_key(self):
        return self.data.get("bearer_key")

    @property
    def credential_type(self):
        return self.data.get("credential_type")

    @property
    def user(self):
        return self.data.get("user") or {}

    def set_endpoints(self, *, access_hub_base_url, gateway_url):
        changed = False
        if access_hub_base_url and self.data.get("access_hub_base_url") != access_hub_base_url:
            self.data["access_hub_base_url"] = access_hub_base_url
            changed = True
        if gateway_url and self.data.get("gateway_url") != gateway_url:
            self.data["gateway_url"] = gateway_url
            changed = True
        if changed:
            self.save()

    def set_bearer_key(self, *, access_hub_base_url, gateway_url, bearer_key, user=None, credential_type=None):
        self.data.update({
            "access_hub_base_url": access_hub_base_url,
            "gateway_url": gateway_url,
            "bearer_key": bearer_key,
            "credential_type": credential_type or "access_hub_bearer",
            "user": user or {},
        })
        self.save()

    def clear_tokens(self):
        for key in ("bearer_key", "credential_type", "user"):
            self.data.pop(key, None)
        self.save()


class SessionState(_JsonFile):
    @classmethod
    def load(cls):
        return super().load(session_file_path())

    @property
    def last_invocation_id(self):
        return self.data.get("last_invocation_id")

    def set_last_invocation_id(self, invocation_id):
        if not invocation_id:
            return
        self.data["last_invocation_id"] = invocation_id
        self.save()


def _ensure_secure_permissions(path):
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            os.chmod(path, 0o600)
            if stat.S_IMODE(path.stat().st_mode) & 0o077:
                raise SkillError("INTERNAL", f"{path} has unsafe permissions and could not be repaired")
    except OSError as exc:
        raise SkillError("INTERNAL", f"Cannot inspect {path}: {exc}")
