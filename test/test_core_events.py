"""校验 core.events：类型化 dataclass 事件 + 字符串兼容层。

运行：  .venv/bin/python test/test_core_events.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xiaomusic.core.events import (  # noqa: E402
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
    Event,
    EventBus,
    PlaybackStateChanged,
    PlaylistChanged,
    TrackChanged,
    event_name,
)


class _RecordingLog:
    """logging.Logger 兼容的最小记录器（任意 level 都可用）。"""

    def __init__(self):
        self.records = []

    def __getattr__(self, level):
        def _log(message, *args, **kwargs):
            self.records.append((level, message))

        return _log


def test_legacy_constants_keep_values():
    assert CONFIG_CHANGED == "config_changed"
    assert DEVICE_CONFIG_CHANGED == "device_config_changed"
    assert PLAYER_STATE_CHANGED == "player_state_changed"
    assert TRACK_CHANGED == "track_changed"
    print("legacy_constants_keep_values OK")


def test_event_types_cover_legacy_constants():
    for name in (
        CONFIG_CHANGED,
        DEVICE_CONFIG_CHANGED,
        PLAYER_STATE_CHANGED,
        TRACK_CHANGED,
        PLAYLIST_CHANGED,
        AUTH_CHANGED,
        COMMAND_FAILED,
    ):
        assert name in EVENT_TYPES, name
    assert EVENT_TYPES[TRACK_CHANGED] is TrackChanged
    print("event_types_cover_legacy_constants OK")


def test_string_subscribe_and_publish_kwargs():
    bus = EventBus()
    got = []
    bus.subscribe(TRACK_CHANGED, lambda **kw: got.append(kw))
    event = bus.publish(TRACK_CHANGED, did="d1", cur_music="songA")
    assert got == [{"did": "d1", "cur_music": "songA"}], got
    assert isinstance(event, TrackChanged)
    assert (event.did, event.cur_music) == ("d1", "songA")
    print("string_subscribe_and_publish_kwargs OK")


def test_string_publish_reaches_typed_subscriber():
    bus = EventBus()
    got = []
    bus.subscribe(TrackChanged, got.append)
    bus.publish(TRACK_CHANGED, did="d1", cur_music="songA")
    assert got == [TrackChanged(did="d1", cur_music="songA")], got
    print("string_publish_reaches_typed_subscriber OK")


def test_typed_publish_reaches_string_subscriber():
    bus = EventBus()
    got = []
    bus.subscribe(CONFIG_CHANGED, lambda **kw: got.append(kw))
    bus.publish(ConfigChanged(key="music_path"))
    assert got == [{"did": "", "key": "music_path"}], got
    print("typed_publish_reaches_string_subscriber OK")


def test_typed_publish_reaches_typed_subscriber():
    bus = EventBus()
    got = []
    bus.subscribe(PlaybackStateChanged, got.append)
    bus.publish(PlaybackStateChanged(did="d1", is_playing=True))
    assert got == [PlaybackStateChanged(did="d1", is_playing=True)], got
    print("typed_publish_reaches_typed_subscriber OK")


def test_base_event_subscription_sees_all_events():
    bus = EventBus()
    got = []
    bus.subscribe(Event, got.append)
    bus.publish(TrackChanged(did="d1", cur_music="a"))
    bus.publish(AuthChanged(logged_in=True, account="u"))
    assert [type(e).__name__ for e in got] == ["TrackChanged", "AuthChanged"], got
    print("base_event_subscription_sees_all_events OK")


def test_exception_is_isolated_and_logged():
    log = _RecordingLog()
    bus = EventBus(log=log)
    got = []

    def boom(**kwargs):
        raise RuntimeError("subscriber exploded")

    bus.subscribe(TRACK_CHANGED, boom)
    bus.subscribe(TRACK_CHANGED, lambda **kw: got.append(kw))
    bus.publish(TRACK_CHANGED, did="d1", cur_music="x")
    assert got == [{"did": "d1", "cur_music": "x"}], got
    assert any(level == "exception" for level, _ in log.records), log.records
    assert "subscriber exploded" in log.records[0][1], log.records
    print("exception_is_isolated_and_logged OK", log.records)


def test_unsubscribe_and_duplicate_subscribe():
    bus = EventBus()
    got = []

    def cb(**kwargs):
        got.append(kwargs)

    bus.subscribe(TRACK_CHANGED, cb)
    bus.subscribe(TRACK_CHANGED, cb)  # 重复订阅只生效一次
    assert bus.subscriber_count(TRACK_CHANGED) == 1
    bus.publish(TRACK_CHANGED, cur_music="a")
    bus.unsubscribe(TRACK_CHANGED, cb)
    bus.publish(TRACK_CHANGED, cur_music="b")
    assert len(got) == 1, got
    assert bus.subscriber_count(TRACK_CHANGED) == 0
    print("unsubscribe_and_duplicate_subscribe OK")


def test_event_instances_are_frozen():
    event = PlaylistChanged(did="d1", playlist="p", index=2)
    try:
        event.playlist = "other"
    except Exception:
        pass
    else:
        raise AssertionError("dataclass event should be frozen")
    print("event_instances_are_frozen OK")


def test_unknown_and_empty_events_are_safe():
    bus = EventBus()
    assert bus.publish("never_registered") is None
    assert bus.publish(COMMAND_FAILED, command="play", error_code="command_error") == CommandFailed(
        command="play", error_code="command_error"
    )
    assert event_name(TrackChanged()) == TRACK_CHANGED
    assert event_name(PLAYER_STATE_CHANGED) == PLAYER_STATE_CHANGED
    print("unknown_and_empty_events_are_safe OK")


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {type(e).__name__}: {e}")
    if failed:
        print(f"\n{failed} test(s) FAILED")
        sys.exit(1)
    print("\nALL PASS")
