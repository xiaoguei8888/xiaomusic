"""特征化测试：锁定 XiaoMusicDevice 的选曲行为（重构前基线）。

运行：  .venv/bin/python test/test_track_selection.py
用 __new__ 绕过重量级构造，只注入 get_music/update_playlist 真正依赖的字段。
RND（随机）模式非确定，不在此断言。
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xiaomusic.config import Device  # noqa: E402
from xiaomusic.const import (  # noqa: E402
    PLAY_TYPE_ALL,
    PLAY_TYPE_SEQ,
)
from xiaomusic.device_player import XiaoMusicDevice  # noqa: E402
from xiaomusic.device_state import DeviceStateStore  # noqa: E402
from xiaomusic.events import EventBus  # noqa: E402


class _Log:
    def info(self, *a, **k):
        pass

    def debug(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


class _Lib:
    def __init__(self, music_list):
        self.music_list = music_list

    def is_music_exist(self, name):
        return True

    def is_online_music(self, name):
        return False


def _device(play_type, cur_music, songs, cur_playlist="全部"):
    dev = XiaoMusicDevice.__new__(XiaoMusicDevice)
    dev.device = Device(
        cur_playlist=cur_playlist, cur_music=cur_music, play_type=play_type
    )
    dev._play_list = []
    dev.log = _Log()
    dev.xiaomusic = types.SimpleNamespace(
        music_library=_Lib({cur_playlist: list(songs)})
    )
    dev.state = DeviceStateStore(dev.device.did, EventBus(), dev.device)
    dev.state.set_track(cur_music)
    return dev


def test_seq_next_mid():
    dev = _device(PLAY_TYPE_SEQ, "b", ["a", "b", "c"])
    got = dev.get_music("next")
    assert got == "c", got
    print("seq_next_mid OK", got)


def test_seq_next_at_end_stops():
    dev = _device(PLAY_TYPE_SEQ, "c", ["a", "b", "c"])
    got = dev.get_music("next")
    assert got == "", got
    print("seq_next_at_end_stops OK", repr(got))


def test_all_next_at_end_wraps():
    dev = _device(PLAY_TYPE_ALL, "c", ["a", "b", "c"])
    got = dev.get_music("next")
    assert got == "a", got
    print("all_next_at_end_wraps OK", got)


def test_prev_from_first_wraps_to_last():
    dev = _device(PLAY_TYPE_ALL, "a", ["a", "b", "c"])
    got = dev.get_music("prev")
    assert got == "c", got
    print("prev_from_first_wraps_to_last OK", got)


def test_single_song_stays():
    dev = _device(PLAY_TYPE_ALL, "a", ["a"])
    assert dev.get_music("next") == "a"
    print("single_song_stays OK")


def test_non_rnd_update_playlist_sorts():
    dev = _device(PLAY_TYPE_SEQ, "", ["c", "a", "b"])
    dev.update_playlist()
    assert dev._play_list == ["a", "b", "c"], dev._play_list
    print("non_rnd_update_playlist_sorts OK", dev._play_list)


def test_empty_playlist_returns_empty():
    dev = _device(PLAY_TYPE_SEQ, "", [])
    got = dev.get_music("next")
    assert got == "", got
    print("empty_playlist_returns_empty OK", repr(got))


def test_invalid_direction_returns_empty():
    dev = _device(PLAY_TYPE_ALL, "a", ["a", "b", "c"])
    got = dev.get_music("sideways")
    assert got == "", got
    print("invalid_direction_returns_empty OK", repr(got))


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
