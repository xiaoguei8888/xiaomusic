"""设备状态内核（L0）。

把 M0 的 DeviceStateStore 升级为「did -> frozen PlayerSnapshot」的通用 Store：

* DeviceStateStore 仍是单设备播放状态的唯一可变源，保留 set_playing/set_track
  旧接口与「变化才广播」语义；
* 新增 snapshot() 返回不可变 PlayerSnapshot（WebSocket / API 只读消费）；
* StateStore 是 did -> DeviceStateStore 的注册表，统一提供快照视图。

核心模块只依赖标准库（typing.Protocol 描述宿主 Device），
不得 import 任何 xiaomusic 业务模块。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol

from xiaomusic.core.events import (
    PLAYER_STATE_CHANGED,
    PLAYLIST_CHANGED,
    TRACK_CHANGED,
    Event,
    EventBus,
)


class DeviceLike(Protocol):
    """DeviceStateStore 需要把 cur_music 镜像回的宿主对象（结构类型，避免 import 业务层）。"""

    cur_music: str


@dataclass(frozen=True)
class PlayerSnapshot:
    """某一时刻的单设备播放状态只读快照。"""

    did: str = ""
    is_playing: bool = False
    cur_music: str = ""
    playlist: str = ""
    playlist_index: int = 0
    version: int = 0

    def as_dict(self) -> dict[str, Any]:
        """返回字段字典（JSON 友好）。"""
        return asdict(self)


class DeviceStateStore:
    """单设备播放状态的唯一可变源。

    保留 M0 构造签名与行为：device 传入时 cur_music 镜像回 Device；
    event_bus 传入时在变更处广播；两者都可为 None。
    """

    def __init__(
        self,
        did: str,
        event_bus: EventBus | None = None,
        device: DeviceLike | None = None,
    ) -> None:
        self.did = did
        self.event_bus = event_bus
        self.device = device
        self._is_playing = False
        self._cur_music = ""
        self._playlist = ""
        self._playlist_index = 0
        self._version = 0

    @property
    def is_playing(self) -> bool:
        return self._is_playing

    @property
    def cur_music(self) -> str:
        return self._cur_music

    @property
    def playlist(self) -> str:
        return self._playlist

    @property
    def playlist_index(self) -> int:
        return self._playlist_index

    @property
    def version(self) -> int:
        """状态版本号，每次真实变更 +1，可用于判断快照是否新鲜。"""
        return self._version

    def snapshot(self) -> PlayerSnapshot:
        """返回当前状态的不可变快照。"""
        return PlayerSnapshot(
            did=self.did,
            is_playing=self._is_playing,
            cur_music=self._cur_music,
            playlist=self._playlist,
            playlist_index=self._playlist_index,
            version=self._version,
        )

    def _emit(self, event: str | Event, **kwargs: Any) -> None:
        if self.event_bus is not None:
            self.event_bus.publish(event, did=self.did, **kwargs)

    def set_playing(self, value: bool) -> None:
        """设置播放/暂停；值未变化时不广播（幂等）。"""
        value = bool(value)
        if self._is_playing == value:
            return
        self._is_playing = value
        self._version += 1
        self._emit(PLAYER_STATE_CHANGED, is_playing=value)

    def set_track(self, name: str) -> None:
        """设置当前曲目；曲目变化才广播 TRACK_CHANGED，始终广播 PLAYER_STATE_CHANGED（M0 语义）。"""
        changed = self._cur_music != name
        self._cur_music = name
        if self.device is not None:
            self.device.cur_music = name
        if changed:
            self._version += 1
            self._emit(TRACK_CHANGED, cur_music=name)
        self._emit(PLAYER_STATE_CHANGED)

    def set_playlist(self, name: str, index: int | None = None) -> None:
        """设置当前歌单 / 歌单内序号；变化才广播 PLAYLIST_CHANGED。"""
        name = name or ""
        index_changed = index is not None and self._playlist_index != int(index)
        if self._playlist == name and not index_changed:
            return
        self._playlist = name
        if index is not None:
            self._playlist_index = int(index)
        self._version += 1
        self._emit(PLAYLIST_CHANGED, playlist=name, index=self._playlist_index)


class StateStore:
    """did -> DeviceStateStore 的通用注册表 + 只读快照视图。"""

    def __init__(self, event_bus: EventBus | None = None) -> None:
        self.event_bus = event_bus
        self._stores: dict[str, DeviceStateStore] = {}

    def register(self, did: str, device: DeviceLike | None = None) -> DeviceStateStore:
        """注册/取回某设备的 Store；重复注册时只更新 device 引用。"""
        store = self._stores.get(did)
        if store is None:
            store = DeviceStateStore(did, self.event_bus, device)
            self._stores[did] = store
        elif device is not None:
            store.device = device
        return store

    def get(self, did: str) -> DeviceStateStore | None:
        return self._stores.get(did)

    def snapshot(self, did: str) -> PlayerSnapshot:
        """取某设备快照；未注册返回空快照（did 保留）。"""
        store = self._stores.get(did)
        if store is None:
            return PlayerSnapshot(did=did)
        return store.snapshot()

    def snapshots(self) -> dict[str, PlayerSnapshot]:
        return {did: store.snapshot() for did, store in self._stores.items()}

    def __contains__(self, did: object) -> bool:
        return did in self._stores

    def __len__(self) -> int:
        return len(self._stores)

    def __iter__(self):
        return iter(self._stores)


__all__ = [
    "DeviceLike",
    "DeviceStateStore",
    "PlayerSnapshot",
    "StateStore",
]
