"""统一错误类型（L0）。

所有 XiaoMusic 领域异常共享 error_code + message 二元组，便于：
* API 层统一转成 JSON：exc.to_dict()；
* 日志/事件（CommandFailed）持有稳定错误码。

本模块只依赖标准库，不得 import 任何 xiaomusic 业务模块。
"""

from __future__ import annotations

from typing import Any


class XiaomiMusicError(Exception):
    """所有 XiaoMusic 领域错误的基类。"""

    error_code: str = "xiaomi_music_error"

    def __init__(
        self,
        message: str = "",
        *,
        error_code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = str(message)
        if error_code is not None:
            self.error_code = str(error_code)
        self.details: dict[str, Any] = dict(details or {})
        super().__init__(self.message)

    def __str__(self) -> str:
        if self.message:
            return f"[{self.error_code}] {self.message}"
        return f"[{self.error_code}]"

    def to_dict(self) -> dict[str, Any]:
        """稳定的可序列化表示，供 API/事件消费。"""
        data: dict[str, Any] = {"error_code": self.error_code, "message": self.message}
        if self.details:
            data["details"] = dict(self.details)
        return data


class CommandError(XiaomiMusicError):
    """命令解析或执行失败。"""

    error_code = "command_error"


class AuthError(XiaomiMusicError):
    """登录 / token 相关失败。"""

    error_code = "auth_error"


class DeviceError(XiaomiMusicError):
    """设备不存在、离线或不可达。"""

    error_code = "device_error"


class PlaybackError(XiaomiMusicError):
    """播放投递或播放控制失败。"""

    error_code = "playback_error"


__all__ = [
    "AuthError",
    "CommandError",
    "DeviceError",
    "PlaybackError",
    "XiaomiMusicError",
]
