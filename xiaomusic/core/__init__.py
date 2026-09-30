"""L0 内核：事件、状态、错误、任务监管。

被所有层依赖，自身不依赖任何业务模块（只依赖标准库）。
旧的 xiaomusic.events / xiaomusic.device_state 仍然可用（re-export）。
"""

from xiaomusic.core.errors import (
    AuthError,
    CommandError,
    DeviceError,
    PlaybackError,
    XiaomiMusicError,
)
from xiaomusic.core.events import (
    AUTH_CHANGED,
    COMMAND_FAILED,
    CONFIG_CHANGED,
    DEVICE_CONFIG_CHANGED,
    EVENT_TYPES,
    PLAYER_STATE_CHANGED,
    PLAYLIST_CHANGED,
    TRACK_CHANGED,
    AuthChanged,
    CommandFailed,
    ConfigChanged,
    DeviceConfigChanged,
    Event,
    EventBus,
    PlaybackStateChanged,
    PlaylistChanged,
    TrackChanged,
    event_name,
)
from xiaomusic.core.state import DeviceLike, DeviceStateStore, PlayerSnapshot, StateStore
from xiaomusic.core.task_supervisor import TaskSupervisor

__all__ = [
    "AUTH_CHANGED",
    "COMMAND_FAILED",
    "CONFIG_CHANGED",
    "DEVICE_CONFIG_CHANGED",
    "EVENT_TYPES",
    "PLAYER_STATE_CHANGED",
    "PLAYLIST_CHANGED",
    "TRACK_CHANGED",
    "AuthChanged",
    "AuthError",
    "CommandError",
    "CommandFailed",
    "ConfigChanged",
    "DeviceConfigChanged",
    "DeviceError",
    "DeviceLike",
    "DeviceStateStore",
    "Event",
    "EventBus",
    "PlaybackError",
    "PlaybackStateChanged",
    "PlayerSnapshot",
    "PlaylistChanged",
    "StateStore",
    "TaskSupervisor",
    "TrackChanged",
    "XiaomiMusicError",
    "event_name",
]
