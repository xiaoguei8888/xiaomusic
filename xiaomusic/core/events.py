"""类型化事件内核（L0）。

M0 版 EventBus 只认「字符串事件名 + **kwargs」；本模块在其上引入 dataclass
事件类型，同时完整保留字符串兼容层：

* 订阅：subscribe(TRACK_CHANGED, cb) 与 subscribe(TrackChanged, cb) 都可用。
  字符串订阅者收到 **kwargs，类型订阅者收到不可变事件实例。
* 发布：publish(TRACK_CHANGED, did=..., cur_music=...) 与
  publish(TrackChanged(did=..., cur_music=...)) 等价。

发布保持同步语义；任一订阅者抛异常都会被隔离并记日志，不影响其余订阅者。
（注入 log 后走 log.exception/error/warning，未注入时退回 print，与 M0 行为一致。）

注意：publish(事件实例) 时字符串订阅者会收到该事件的全部字段（含 did/key 等），
因此字符串订阅者必须用 **kwargs 形参接收。

本模块只依赖标准库，不得 import 任何 xiaomusic 业务模块。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, fields
from typing import Any, ClassVar

# ---------------------------------------------------------------------------
# 事件名常量（字符串兼容层）
# ---------------------------------------------------------------------------
CONFIG_CHANGED = "config_changed"
DEVICE_CONFIG_CHANGED = "device_config_changed"
PLAYER_STATE_CHANGED = "player_state_changed"
TRACK_CHANGED = "track_changed"
PLAYLIST_CHANGED = "playlist_changed"
AUTH_CHANGED = "auth_changed"
COMMAND_FAILED = "command_failed"


@dataclass(frozen=True)
class Event:
    """所有类型化事件的基类。

    字段全部带默认值，便于按需构造；did 为空串表示非设备级事件。
    """

    event_type: ClassVar[str] = "event"
    did: str = ""

    def as_dict(self) -> dict[str, Any]:
        """返回事件字段的浅快照，供字符串订阅者 / JSON 序列化使用。"""
        return {field.name: getattr(self, field.name) for field in fields(self)}


@dataclass(frozen=True)
class TrackChanged(Event):
    """曲目切换。"""

    event_type: ClassVar[str] = TRACK_CHANGED
    cur_music: str = ""


@dataclass(frozen=True)
class PlaybackStateChanged(Event):
    """播放/暂停状态变化；is_playing=None 表示「状态可能变化，未知具体值」。"""

    event_type: ClassVar[str] = PLAYER_STATE_CHANGED
    is_playing: bool | None = None


@dataclass(frozen=True)
class PlaylistChanged(Event):
    """歌单切换或歌单内序号变化。"""

    event_type: ClassVar[str] = PLAYLIST_CHANGED
    playlist: str = ""
    index: int = 0


@dataclass(frozen=True)
class ConfigChanged(Event):
    """配置项变化；key 为空表示整份配置变化。"""

    event_type: ClassVar[str] = CONFIG_CHANGED
    key: str = ""


@dataclass(frozen=True)
class DeviceConfigChanged(Event):
    """单设备配置变化（旧字符串事件 DEVICE_CONFIG_CHANGED 的类型化对偶）。"""

    event_type: ClassVar[str] = DEVICE_CONFIG_CHANGED


@dataclass(frozen=True)
class AuthChanged(Event):
    """登录态变化。"""

    event_type: ClassVar[str] = AUTH_CHANGED
    logged_in: bool = False
    account: str = ""


@dataclass(frozen=True)
class CommandFailed(Event):
    """命令执行失败。"""

    event_type: ClassVar[str] = COMMAND_FAILED
    command: str = ""
    error: str = ""
    error_code: str = ""


#: 字符串事件名 -> 类型化事件类
EVENT_TYPES: dict[str, type[Event]] = {
    TrackChanged.event_type: TrackChanged,
    PlaybackStateChanged.event_type: PlaybackStateChanged,
    PlaylistChanged.event_type: PlaylistChanged,
    ConfigChanged.event_type: ConfigChanged,
    DeviceConfigChanged.event_type: DeviceConfigChanged,
    AuthChanged.event_type: AuthChanged,
    CommandFailed.event_type: CommandFailed,
}

Subscriber = Callable[..., None]
SubscriptionKey = "str | type[Event]"


def event_name(event: str | Event) -> str:
    """取事件名：字符串原样返回，事件实例取其 event_type。"""
    if isinstance(event, Event):
        return event.event_type
    return str(event)


class EventBus:
    """同步事件总线：字符串事件名与 dataclass 事件类型双轨兼容。

    * 订阅键可以是字符串事件名，也可以是 Event 子类（含 Event 基类，匹配全部）。
    * 订阅者异常隔离：记日志后继续通知其余订阅者。
    * 发布期间增删订阅者不会破坏本次遍历（遍历快照）。
    """

    def __init__(self, log: Any = None) -> None:
        self._subscribers: dict[Any, list[Subscriber]] = {}
        self._log = log

    def __repr__(self) -> str:
        keys = [
            key if isinstance(key, str) else key.__name__ for key in self._subscribers
        ]
        return f"<EventBus events={sorted(keys)}>"

    # -- 订阅 ---------------------------------------------------------------
    def subscribe(self, event: str | type[Event], callback: Subscriber) -> None:
        """订阅事件；重复订阅同一回调只生效一次。"""
        bucket = self._subscribers.setdefault(event, [])
        if callback not in bucket:
            bucket.append(callback)

    def unsubscribe(self, event: str | type[Event], callback: Subscriber) -> None:
        """取消订阅；未订阅时静默返回。"""
        bucket = self._subscribers.get(event)
        if bucket and callback in bucket:
            bucket.remove(callback)

    def subscriber_count(self, event: str | type[Event]) -> int:
        """返回某事件键上的订阅者数量（测试/诊断用）。"""
        return len(self._subscribers.get(event, ()))

    def clear(self) -> None:
        """移除全部订阅者。"""
        self._subscribers.clear()

    # -- 发布 ---------------------------------------------------------------
    def publish(self, event: str | Event, **kwargs: Any) -> Event | None:
        """同步发布事件。

        返回归一化后的 Event 实例；字符串事件名若无对应类型则返回 None。
        """
        name, payload, event_obj = self._normalize(event, kwargs)
        self._dispatch_string(name, payload)
        if event_obj is not None:
            self._dispatch_typed(event_obj)
        return event_obj

    @staticmethod
    def _normalize(
        event: str | Event, kwargs: dict[str, Any]
    ) -> tuple[str, dict[str, Any], Event | None]:
        """把两种发布形式归一化为 (事件名, 字符串订阅者 kwargs, 类型事件或 None)。

        字符串发布保留调用方原始 kwargs（不注入/不裁剪），确保 M0 调用方行为不变。
        """
        if isinstance(event, Event):
            return event.event_type, event.as_dict(), event
        name = str(event)
        cls = EVENT_TYPES.get(name)
        if cls is None:
            return name, dict(kwargs), None
        allowed = {field.name for field in fields(cls)}
        event_obj = cls(
            **{key: value for key, value in kwargs.items() if key in allowed}
        )
        return name, dict(kwargs), event_obj

    def _dispatch_string(self, name: str, payload: dict[str, Any]) -> None:
        for callback in list(self._subscribers.get(name, ())):
            try:
                callback(**payload)
            except Exception as exc:  # noqa: BLE001 - 订阅者异常必须隔离
                self._report_error(name, callback, exc)

    def _dispatch_typed(self, event_obj: Event) -> None:
        for cls in type(event_obj).__mro__:
            if cls is object:
                continue
            for callback in list(self._subscribers.get(cls, ())):
                try:
                    callback(event_obj)
                except Exception as exc:  # noqa: BLE001 - 订阅者异常必须隔离
                    self._report_error(cls.__name__, callback, exc)

    def _report_error(
        self, source: str, callback: Subscriber, exc: BaseException
    ) -> None:
        name = getattr(callback, "__name__", None) or repr(callback)
        message = (
            f"{self!r} subscriber {name} on {source} raised {type(exc).__name__}: {exc}"
        )
        log = self._log
        if log is not None:
            for level in ("exception", "error", "warning"):
                reporter = getattr(log, level, None)
                if callable(reporter):
                    reporter(message)
                    return
        print(f"Error in event callback for {source}: {exc}")


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
    "CommandFailed",
    "ConfigChanged",
    "DeviceConfigChanged",
    "Event",
    "EventBus",
    "PlaybackStateChanged",
    "PlaylistChanged",
    "TrackChanged",
    "event_name",
]
