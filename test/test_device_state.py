"""校验 DeviceStateStore：单一状态所有者 + 变更事件。

运行：  .venv/bin/python test/test_device_state.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xiaomusic.config import Device  # noqa: E402
from xiaomusic.device_state import DeviceStateStore  # noqa: E402
from xiaomusic.events import (  # noqa: E402
    PLAYER_STATE_CHANGED,
    TRACK_CHANGED,
    EventBus,
)


def _recorder():
    bus = EventBus()
    events = []
    bus.subscribe(
        TRACK_CHANGED, lambda **kw: events.append(("track", kw.get("cur_music")))
    )
    bus.subscribe(
        PLAYER_STATE_CHANGED,
        lambda **kw: events.append(("state", kw.get("is_playing"))),
    )
    return bus, events


def test_track_change_emits_and_mirrors():
    bus, events = _recorder()
    dev = Device(did="d1")
    store = DeviceStateStore("d1", bus, dev)
    store.set_track("songA")
    assert ("track", "songA") in events, events
    assert dev.cur_music == "songA"
    assert store.cur_music == "songA"
    print("track_change_emits_and_mirrors OK", events)


def test_track_not_emitted_when_unchanged():
    bus, events = _recorder()
    store = DeviceStateStore("d1", bus, Device(did="d1"))
    store.set_track("x")
    before = [e for e in events if e[0] == "track"]
    store.set_track("x")
    after = [e for e in events if e[0] == "track"]
    assert len(before) == len(after) == 1, (before, after)
    print("track_not_emitted_when_unchanged OK")


def test_playing_change_emits():
    bus, events = _recorder()
    store = DeviceStateStore("d1", bus, Device(did="d1"))
    store.set_playing(True)
    assert ("state", True) in events, events
    assert store.is_playing is True
    store.set_playing(True)
    assert len([e for e in events if e[0] == "state"]) == 1, events
    print("playing_change_emits OK", events)


def test_no_event_bus_is_safe():
    store = DeviceStateStore("d1", None, Device(did="d1"))
    store.set_track("a")
    store.set_playing(True)
    assert store.cur_music == "a" and store.is_playing is True
    print("no_event_bus_is_safe OK")


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
