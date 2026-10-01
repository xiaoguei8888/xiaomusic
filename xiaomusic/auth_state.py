"""认证状态存储：conf/auth.json v2 单一状态源。

把原先分散在 setting.json.cookie / auth.json / .mi.token / .device_id
的多份认证数据收敛到一个带 version 的文件，并提供 miservice 兼容视图。
"""

import json
import os
import tempfile
import time

AUTH_STATE_VERSION = 2

SID_MICOAPI = "micoapi"
SID_XIAOMIIO = "xiaomiio"

STATUS_OK = "ok"
STATUS_NEEDS_VERIFICATION = "needs_verification"
STATUS_EXPIRED = "expired"
STATUS_ERROR = "error"


def _empty_state() -> dict:
    return {
        "version": AUTH_STATE_VERSION,
        "account": "",
        "deviceId": "",
        "userId": None,
        "cUserId": None,
        "passToken": None,
        "passTokenUpdatedAt": 0,
        "sids": {},
        "cooldownUntil": 0,
    }


class AuthState:
    def __init__(self, path: str, log=None):
        self.path = path
        self.log = log
        self.data = _empty_state()

    def load(self) -> dict:
        if not os.path.isfile(self.path):
            self._import_legacy()
            return self.data
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:
            if self.log:
                self.log.warning(f"[AUTH-STATE] 读取 auth.json 失败: {e}")
            return self.data
        if raw.get("version") != AUTH_STATE_VERSION:
            self._import_legacy(raw)
            return self.data
        merged = _empty_state()
        merged.update(raw)
        merged["sids"] = raw.get("sids", {}) or {}
        self.data = merged
        return self.data

    def _import_legacy(self, raw: dict | None = None) -> None:
        conf_dir = os.path.dirname(self.path)
        state = _empty_state()

        legacy_auth = raw or {}
        if not legacy_auth:
            old_auth_path = os.path.join(conf_dir, "auth.json")
            if os.path.isfile(old_auth_path):
                try:
                    with open(old_auth_path, encoding="utf-8") as f:
                        legacy_auth = json.load(f)
                except Exception:
                    legacy_auth = {}

        state["account"] = legacy_auth.get("account", "")
        state["deviceId"] = legacy_auth.get("deviceId", "")
        state["userId"] = legacy_auth.get("userId")
        state["passToken"] = legacy_auth.get("passToken")

        device_id_path = os.path.join(conf_dir, ".device_id")
        if not state["deviceId"] and os.path.isfile(device_id_path):
            try:
                with open(device_id_path, encoding="utf-8") as f:
                    state["deviceId"] = f.read().strip()
            except Exception:
                pass

        mi_token_path = os.path.join(conf_dir, ".mi.token")
        if os.path.isfile(mi_token_path):
            try:
                with open(mi_token_path, encoding="utf-8") as f:
                    flat = json.load(f)
                if isinstance(flat, dict):
                    state["deviceId"] = state["deviceId"] or flat.get("deviceId", "")
                    state["userId"] = state["userId"] or flat.get("userId")
                    state["passToken"] = state["passToken"] or flat.get("passToken")
                    for sid, val in flat.items():
                        if isinstance(val, list) and len(val) >= 2:
                            state["sids"][sid] = {
                                "ssecurity": val[0],
                                "serviceToken": val[1],
                                "status": STATUS_OK,
                                "updatedAt": int(time.time()),
                                "lastError": None,
                                "notificationUrl": None,
                            }
            except Exception:
                pass

        if state["passToken"]:
            state["passTokenUpdatedAt"] = int(time.time())
        self.data = state
        if state["passToken"] or state["sids"]:
            self.save()

    def save(self) -> None:
        self.data["version"] = AUTH_STATE_VERSION
        conf_dir = os.path.dirname(self.path)
        try:
            os.makedirs(conf_dir, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=conf_dir, prefix=".auth.", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except Exception as e:
            if self.log:
                self.log.warning(f"[AUTH-STATE] 写入 auth.json 失败: {e}")

    def set_pass_token(
        self, pass_token: str, user_id, c_user_id=None, device_id=None
    ) -> None:
        self.data["passToken"] = pass_token
        self.data["userId"] = user_id
        if c_user_id:
            self.data["cUserId"] = c_user_id
        if device_id:
            self.data["deviceId"] = device_id
        self.data["passTokenUpdatedAt"] = int(time.time())

    def set_sid_ok(self, sid: str, ssecurity: str, service_token: str) -> None:
        self.data.setdefault("sids", {})[sid] = {
            "ssecurity": ssecurity,
            "serviceToken": service_token,
            "status": STATUS_OK,
            "updatedAt": int(time.time()),
            "lastError": None,
            "notificationUrl": None,
        }
        self.save()

    def set_sid_needs_verification(self, sid: str, notification_url: str) -> None:
        entry = self.data.setdefault("sids", {}).setdefault(sid, {})
        entry.update(
            {
                "status": STATUS_NEEDS_VERIFICATION,
                "notificationUrl": notification_url,
                "updatedAt": int(time.time()),
                "lastError": "securityStatus:16",
            }
        )
        self.save()

    def set_sid_error(self, sid: str, error: str) -> None:
        entry = self.data.setdefault("sids", {}).setdefault(sid, {})
        entry.update(
            {
                "status": STATUS_ERROR,
                "lastError": error,
                "updatedAt": int(time.time()),
            }
        )
        self.save()

    def sid_status(self, sid: str) -> str:
        return (self.data.get("sids", {}).get(sid) or {}).get("status", STATUS_EXPIRED)

    def needs_verification(self, sid: str) -> bool:
        return self.sid_status(sid) == STATUS_NEEDS_VERIFICATION

    def cooldown_active(self) -> bool:
        return time.time() < self.data.get("cooldownUntil", 0)

    def set_cooldown(self, seconds: int) -> None:
        self.data["cooldownUntil"] = int(time.time()) + seconds
        self.save()

    def to_miservice_token(self) -> dict:
        flat = {}
        if self.data.get("deviceId"):
            flat["deviceId"] = self.data["deviceId"]
        if self.data.get("userId") is not None:
            flat["userId"] = self.data["userId"]
        if self.data.get("passToken"):
            flat["passToken"] = self.data["passToken"]
        for sid, entry in (self.data.get("sids") or {}).items():
            if entry.get("ssecurity") and entry.get("serviceToken"):
                flat[sid] = [entry["ssecurity"], entry["serviceToken"]]
        return flat

    def sync_from_miservice_token(self, flat: dict) -> None:
        if not isinstance(flat, dict):
            return
        if flat.get("deviceId"):
            self.data["deviceId"] = flat["deviceId"]
        if flat.get("userId") is not None:
            self.data["userId"] = flat["userId"]
        if flat.get("passToken"):
            self.data["passToken"] = flat["passToken"]
        for sid, val in flat.items():
            # miservice 的 MiAccount.login() 写入的是 tuple：
            #   self.token[sid] = (ssecurity, serviceToken)
            # 只认 list 会静默丢弃每个 sid —— 于是「验证码正确、登录成功」
            # 也永远写不进 ok，前端只能一直显示「等待验证结果」。
            if isinstance(val, (list, tuple)) and len(val) >= 2:
                self.set_sid_ok(sid, val[0], val[1])
        self.save()


class AuthTokenStore:
    """miservice 的 token_store，读写直接落在 auth.json v2 上。

    与 miservice 自带 MiTokenStore 的区别：
    登录失败（token 为 None 或空）时保留原状态，绝不清空文件。
    """

    def __init__(self, state: AuthState, log=None):
        self.state = state
        self.log = log

    async def load_token(self):
        self.state.load()
        flat = self.state.to_miservice_token()
        if not flat or not flat.get("passToken"):
            return flat or None
        return flat

    async def save_token(self, token=None):
        if not token:
            return
        self.state.sync_from_miservice_token(token)
