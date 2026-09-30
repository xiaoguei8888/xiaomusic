"""每个设备的播放状态唯一所有者（单一可变源）。

历史上播放状态分散在 Device dataclass（cur_music）与 XiaoMusicDevice 实例字段
（is_playing）上，被任意方法直接改写，UI 只能靠 1s 轮询感知变化。本类收拢这两个
最常读写的易变态，并在变更时通过 EventBus 广播，供 WebSocket 即时推送。

device 传入时，cur_music 会镜像回 Device，保证持久化（save_cur_config）不失效。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from xiaomusic.events import (
    PLAYER_STATE_CHANGED,
    TRACK_CHANGED,
)

if TYPE_CHECKING:
    from typing import Any

    from xiaomusic.config import Device
    from xiaomusic.events import EventBus


class DeviceStateStore:
    def __init__(self, did: str, event_bus: "EventBus | None" = None, device: "Device | None" = None):
        self.did = did
        self.event_bus = event_bus
        self.device = device
        self._is_playing = False
        self._cur_music = ""

    @property
    def is_playing(self) -> bool:
        return self._is_playing

    @property
    def cur_music(self) -> str:
        return self._cur_music

    def _emit(self, event_type: str, **kwargs: "Any") -> None:
        if self.event_bus is not None:
            self.event_bus.publish(event_type, did=self.did, **kwargs)

    def set_playing(self, value: bool) -> None:
        if self._is_playing == value:
            return
        self._is_playing = value
        self._emit(PLAYER_STATE_CHANGED, is_playing=value)

    def set_track(self, name: str) -> None:
        changed = self._cur_music != name
        self._cur_music = name
        if self.device is not None:
            self.device.cur_music = name
        if changed:
            self._emit(TRACK_CHANGED, cur_music=name)
        self._emit(PLAYER_STATE_CHANGED)
