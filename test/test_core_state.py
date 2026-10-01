"""校验 core.state：PlayerSnapshot 不可变快照 + DeviceStateStore/StateStore。

运行：  .venv/bin/python test/test_core_state.py
"""

import os
import sys
from dataclasses import FrozenInstanceError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xiaomusic.config import Device  # noqa: E402
from xiaomusic.core.events import (  # noqa: E402
    PLAYER_STATE_CHANGED,
    PLAYLIST_CHANGED,
    TRACK_CHANGED,
    EventBus,
)
from xiaomusic.core.state import (  # noqa: E402
    DeviceStateStore,
    PlayerSnapshot,
    StateStore,
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
    bus.subscribe(
        PLAYLIST_CHANGED, lambda **kw: events.append(("playlist", kw.get("playlist")))
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


def test_playing_change_emits_once():
    bus, events = _recorder()
    store = DeviceStateStore("d1", bus, Device(did="d1"))
    store.set_playing(True)
    assert ("state", True) in events, events
    store.set_playing(True)
    assert len([e for e in events if e[0] == "state"]) == 1, events
    store.set_playing(False)
    assert len([e for e in events if e[0] == "state"]) == 2, events
    print("playing_change_emits_once OK", events)


def test_no_event_bus_is_safe():
    store = DeviceStateStore("d1", None, Device(did="d1"))
    store.set_track("a")
    store.set_playing(True)
    store.set_playlist("p", 1)
    assert store.cur_music == "a" and store.is_playing is True
    print("no_event_bus_is_safe OK")


def test_snapshot_is_immutable_and_complete():
    bus, events = _recorder()
    store = DeviceStateStore("d1", bus, Device(did="d1"))
    store.set_track("songA")
    store.set_playing(True)
    store.set_playlist("listA", 3)
    snap = store.snapshot()
    assert isinstance(snap, PlayerSnapshot)
    assert (snap.did, snap.cur_music, snap.is_playing) == ("d1", "songA", True)
    assert (snap.playlist, snap.playlist_index) == ("listA", 3)
    assert snap.as_dict()["cur_music"] == "songA"
    try:
        snap.cur_music = "hacked"
    except FrozenInstanceError:
        pass
    else:
        raise AssertionError("PlayerSnapshot must be frozen")
    assert store.cur_music == "songA", "snapshot mutation must not affect store"
    print("snapshot_is_immutable_and_complete OK", snap.as_dict())


def test_version_increments_only_on_change():
    store = DeviceStateStore("d1")
    assert store.version == 0
    store.set_playing(True)
    assert store.version == 1
    store.set_playing(True)
    assert store.version == 1, "idempotent set must not bump version"
    store.set_track("a")
    assert store.version == 2
    store.set_track("a")
    assert store.version == 2
    store.set_playlist("p")
    assert store.version == 3
    store.set_playlist("p")
    assert store.version == 3
    assert store.snapshot().version == 3
    print("version_increments_only_on_change OK")


def test_set_playlist_emits_on_change():
    bus, events = _recorder()
    store = DeviceStateStore("d1", bus, Device(did="d1"))
    store.set_playlist("listA")
    store.set_playlist("listA")
    store.set_playlist("listA", 2)
    store.set_playlist("listB", 0)
    playlists = [e for e in events if e[0] == "playlist"]
    assert [p[1] for p in playlists] == ["listA", "listA", "listB"], playlists
    assert store.playlist == "listB" and store.playlist_index == 0
    print("set_playlist_emits_on_change OK", playlists)


def test_state_store_registry_and_snapshots():
    bus, events = _recorder()
    registry = StateStore(bus)
    dev = Device(did="d1")
    s1 = registry.register("d1", dev)
    s1.set_track("songA")
    s2 = registry.register("d1")  # 复用同一 store
    assert s2 is s1
    assert registry.get("d1") is s1
    assert "d1" in registry and "nope" not in registry
    assert len(registry) == 1
    assert list(registry) == ["d1"]
    assert registry.snapshot("d1").cur_music == "songA"
    assert registry.snapshot("nope") == PlayerSnapshot(did="nope")
    registry.register("d2").set_playing(True)
    snaps = registry.snapshots()
    assert set(snaps) == {"d1", "d2"}, snaps
    assert snaps["d2"].is_playing is True
    assert dev.cur_music == "songA"
    print("state_store_registry_and_snapshots OK", snaps)


def test_state_store_registry_events_flow():
    bus, events = _recorder()
    registry = StateStore(bus)
    registry.register("d1", Device(did="d1")).set_track("x")
    assert ("track", "x") in events, events
    print("state_store_registry_events_flow OK")


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
