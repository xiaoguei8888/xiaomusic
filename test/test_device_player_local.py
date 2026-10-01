"""XiaoMusicDevice 本地纯逻辑测试（离线，不打网络）。

覆盖：
- update_playlist：顺序歌单排序、RND 状态保持洗牌/置顶、增量追加/剔除、歌单缺失兜底
- get_music：当前曲目未知时的起点、RND 一轮结束后的重洗置顶
- check_play_next / get_display_state 的状态判定
- _parse_player_status / _extract_volume 的边界
- get_volume / get_player_status / get_cloud_status（fake mina_service，毫秒→秒归一化）

注：get_music 的两处回归（RND 洗牌后取新列表、缺失曲目跳过而不递归/死循环）
已随 lead 修复转为正式断言。
"""

from __future__ import annotations

import asyncio
import json
import time
import types

from xiaomusic.config import Device
from xiaomusic.const import PLAY_TYPE_ALL, PLAY_TYPE_RND, PLAY_TYPE_SEQ
from xiaomusic.device_player import (
    XiaoMusicDevice,
    _extract_volume,
    _parse_player_status,
)
from xiaomusic.events import EventBus


class FakeLibrary:
    """只实现 XiaoMusicDevice 用到的 MusicLibrary 接口。"""

    def __init__(self, music_list, missing=()):
        self.music_list = music_list
        self._missing = set(missing)

    def is_music_exist(self, name):
        return name not in self._missing


class FakeMina:
    """假的 mina_service：记录调用并按需返回/抛错，绝不发网络请求。"""

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []
        self.volume_calls = []
        self.pause_calls = []
        self.stop_calls = []
        self.play_calls = []

    async def player_get_status(self, device_id):
        self.calls.append(device_id)
        if self.error is not None:
            raise self.error
        return self.response

    async def player_set_volume(self, device_id, volume):
        self.volume_calls.append((device_id, volume))
        if self.error is not None:
            raise self.error

    async def player_pause(self, device_id):
        self.pause_calls.append(device_id)
        if self.error is not None:
            raise self.error
        return True

    async def player_stop(self, device_id):
        self.stop_calls.append(device_id)
        if self.error is not None:
            raise self.error
        return True

    async def player_play(self, device_id):
        self.play_calls.append(device_id)
        if self.error is not None:
            raise self.error
        return True


def make_device(
    config,
    log,
    songs=None,
    play_type=PLAY_TYPE_SEQ,
    cur_music="",
    cur_playlist="全部",
    missing=(),
    mina_service=None,
):
    """用最小 fake 宿主构造真实 XiaoMusicDevice（走公开构造函数）。"""
    library = FakeLibrary({cur_playlist: list(songs or [])}, missing=missing)
    host = types.SimpleNamespace(
        config=config,
        log=log,
        auth_manager=types.SimpleNamespace(
            mina_service=mina_service, miio_service=None
        ),
        event_bus=EventBus(),
        music_library=library,
    )
    device = Device(
        did="did-1",
        device_id="dev-1",
        play_type=play_type,
        cur_playlist=cur_playlist,
    )
    dev = XiaoMusicDevice(host, device, "客厅")
    dev.state.set_track(cur_music)
    return dev


# --------------------------------------------------------------------------
# update_playlist
# --------------------------------------------------------------------------
def test_init_sequence_mode_sorts_playlist(config, fake_log):
    dev = make_device(config, fake_log, songs=["c", "a", "b"], play_type=PLAY_TYPE_SEQ)

    assert dev._play_list == ["a", "b", "c"]
    assert dev.device.cur_playlist == "全部"


def test_unknown_cur_playlist_falls_back_to_all(config, fake_log):
    dev = make_device(config, fake_log, songs=["a"], play_type=PLAY_TYPE_SEQ)
    dev.device.cur_playlist = "不存在的歌单"

    dev.update_playlist()

    assert dev.device.cur_playlist == "全部"
    assert dev._play_list == ["a"]


def test_rnd_update_keeps_stateful_shuffle(config, fake_log):
    songs = ["s" + str(i) for i in range(12)]
    dev = make_device(config, fake_log, songs=songs, play_type=PLAY_TYPE_RND)
    first = list(dev._play_list)

    assert sorted(first) == sorted(songs)

    dev.update_playlist()

    assert dev._play_list == first  # 状态保持：非强洗不重新洗牌


def test_rnd_force_reshuffle_pins_current_music(config, fake_log):
    songs = ["s" + str(i) for i in range(12)]
    dev = make_device(config, fake_log, songs=songs, play_type=PLAY_TYPE_RND)
    dev.state.set_track("s5")

    dev.update_playlist(force_reshuffle=True)

    assert dev._play_list[0] == "s5"
    assert sorted(dev._play_list) == sorted(songs)
    assert len(dev._play_list) == len(songs)


def test_rnd_incremental_update_appends_new_songs_keeping_order(config, fake_log):
    dev = make_device(config, fake_log, songs=["a", "b", "c"], play_type=PLAY_TYPE_RND)
    before = list(dev._play_list)

    dev.xiaomusic.music_library.music_list["全部"] = ["a", "b", "c", "d", "e"]
    dev.update_playlist()

    assert dev._play_list[:3] == before  # 原有相对顺序不变
    assert set(dev._play_list[3:]) == {"d", "e"}


def test_rnd_incremental_update_drops_deleted_songs(config, fake_log):
    dev = make_device(config, fake_log, songs=["a", "b", "c"], play_type=PLAY_TYPE_RND)
    before = list(dev._play_list)

    dev.xiaomusic.music_library.music_list["全部"] = before[1:]
    dev.update_playlist()

    assert dev._play_list == before[1:]


# --------------------------------------------------------------------------
# get_music
# --------------------------------------------------------------------------
def test_get_music_with_unknown_current_starts_from_first(config, fake_log):
    dev = make_device(
        config, fake_log, songs=["a", "b", "c"], play_type=PLAY_TYPE_ALL, cur_music=""
    )

    assert dev.get_music("next") == "b"


def test_get_music_rnd_at_end_reshuffles_and_pins_current(config, fake_log):
    songs = ["s" + str(i) for i in range(8)]
    dev = make_device(config, fake_log, songs=songs, play_type=PLAY_TYPE_RND)
    last = dev._play_list[-1]
    dev.state.set_track(last)

    got = dev.get_music("next")

    assert dev._play_list[0] == last  # 一轮结束后重洗，并把当前曲置顶
    assert sorted(dev._play_list) == sorted(songs)
    assert got in songs  # 仍返回可用曲目


# --------------------------------------------------------------------------
# check_play_next / get_display_state
# --------------------------------------------------------------------------
def test_check_play_next_true_when_current_not_in_playlist(config, fake_log):
    dev = make_device(
        config, fake_log, songs=["a", "b"], play_type=PLAY_TYPE_SEQ, cur_music=""
    )

    assert dev.check_play_next() is True


def test_check_play_next_true_when_current_file_gone(config, fake_log):
    dev = make_device(
        config,
        fake_log,
        songs=["a", "b"],
        play_type=PLAY_TYPE_SEQ,
        cur_music="a",
        missing={"a"},
    )

    assert dev.check_play_next() is True


def test_check_play_next_false_when_current_playable(config, fake_log):
    dev = make_device(
        config, fake_log, songs=["a", "b"], play_type=PLAY_TYPE_SEQ, cur_music="a"
    )

    assert dev.check_play_next() is False


def test_display_state_falls_back_to_local_intent(config, fake_log):
    dev = make_device(config, fake_log, songs=["a"], play_type=PLAY_TYPE_SEQ)

    assert dev.get_display_state() == (False, None)

    dev.is_playing = True

    assert dev.get_display_state() == (True, None)
    assert dev.isplaying() is True


def test_display_state_prefers_cloud_snapshot(config, fake_log):
    dev = make_device(config, fake_log, songs=["a"], play_type=PLAY_TYPE_SEQ)
    dev._cloud_snapshot = {"_ok": True, "status": 1, "duration": 10}

    playing, snap = dev.get_display_state()

    assert playing is True
    assert snap is dev._cloud_snapshot


# --------------------------------------------------------------------------
# 状态解析
# --------------------------------------------------------------------------
def test_parse_player_status_merges_nested_only_fields():
    raw = {
        "volume": 7,
        "status": 1,
        "data": {"info": json.dumps({"volume": 99, "loop_type": 2})},
    }

    parsed = _parse_player_status(raw)

    assert parsed["volume"] == 7  # 顶层优先
    assert parsed["loop_type"] == 2  # 仅 info 里有的字段保留
    assert "data" not in parsed


def test_parse_and_extract_volume_edge_cases():
    assert _parse_player_status([1, 2]) == {"volume": 0, "status": 0}
    assert _extract_volume({"volume": None}) == 0
    assert _extract_volume({"volume": "12"}) == 12
    assert _extract_volume({"data": {"info": "not-json"}, "volume": 3}) == 3


# --------------------------------------------------------------------------
# 云端接口（fake，离线）
# --------------------------------------------------------------------------
async def test_get_volume_reads_top_level_field(config, fake_log):
    mina = FakeMina({"status": 1, "volume": 42})
    dev = make_device(config, fake_log, songs=["a"], mina_service=mina)

    assert await dev.get_volume() == 42
    assert mina.calls == ["dev-1"]


async def test_get_volume_returns_zero_on_error(config, fake_log):
    mina = FakeMina(error=RuntimeError("boom"))
    dev = make_device(config, fake_log, songs=["a"], mina_service=mina)

    assert await dev.get_volume() == 0


async def test_get_player_status_returns_parsed_status(config, fake_log):
    mina = FakeMina({"status": 1, "volume": 9})
    dev = make_device(config, fake_log, songs=["a"], mina_service=mina)

    status = await dev.get_player_status()

    assert status["volume"] == 9
    assert status["status"] == 1


async def test_get_cloud_status_normalizes_ms_to_seconds(config, fake_log):
    info = json.dumps(
        {
            "status": 1,
            "volume": 30,
            "loop_type": 1,
            "play_song_detail": {
                "position": 5000,
                "duration": 200000,
                "audio_id": "aid-1",
            },
            "track_list": [],
        }
    )
    # 真机形状：字段在顶层（data.info 只是部分固件的兼容分支）
    mina = FakeMina(json.loads(info))
    dev = make_device(config, fake_log, songs=["a"], mina_service=mina)

    snap = await dev.get_cloud_status()

    assert snap["position"] == 5.0
    assert snap["duration"] == 200.0
    assert snap["audio_id"] == "aid-1"
    assert dev.get_display_state()[0] is True

    # 兼容分支：只有 data.info、没有顶层字段时仍需解析（叠加一个标记字段）
    legacy = FakeMina({"status": 1, "data": {"info": info}})
    dev2 = make_device(config, fake_log, songs=["a"], mina_service=legacy)
    snap2 = await dev2.get_cloud_status()

    assert snap2 is not None and snap2["duration"] == 200.0
    assert dev2.get_display_state()[0] is True


async def test_get_cloud_status_returns_none_without_service(config, fake_log):
    dev = make_device(config, fake_log, songs=["a"], mina_service=None)

    assert await dev.get_cloud_status() is None

# --------------------------------------------------------------------------
# 属性 / 播放进度 / 定时器 / 歌单匹配
# --------------------------------------------------------------------------
def test_device_did_and_hardware_properties(config, fake_log):
    dev = make_device(config, fake_log, songs=["a"], play_type=PLAY_TYPE_SEQ)

    assert dev.did == "did-1"
    assert dev.hardware == ""
    assert dev.get_cur_play_list() == "全部"


def test_get_next_and_prev_music(config, fake_log):
    dev = make_device(
        config, fake_log, songs=["a", "b", "c"], play_type=PLAY_TYPE_SEQ, cur_music="b"
    )

    assert dev.get_next_music() == "c"
    assert dev.get_prev_music() == "a"


def test_get_offset_duration_from_cloud_snapshot(config, fake_log):
    dev = make_device(config, fake_log, songs=["a"], play_type=PLAY_TYPE_SEQ)
    dev._cloud_snapshot = {"_ok": True, "status": 1, "duration": 100, "position": 10}
    dev._cloud_snapshot_at = time.time()

    offset, duration = dev.get_offset_duration()

    assert duration == 100
    assert 10 <= offset <= 100.5  # 两次轮询之间按本地时钟线性插值

    dev._cloud_snapshot = {"_ok": True, "status": 0, "duration": 100, "position": 10}
    assert dev.get_offset_duration() == (0, 100)


def test_get_offset_duration_falls_back_to_local_clock(config, fake_log):
    dev = make_device(config, fake_log, songs=["a"], play_type=PLAY_TYPE_SEQ)

    assert dev.get_offset_duration() == (0, 0)  # 无快照且未播放

    dev.is_playing = True
    dev._start_time = time.time() - 5
    offset, duration = dev.get_offset_duration()

    assert 4.5 <= offset <= 5.5
    assert duration == 0


def test_display_state_grace_window_keeps_local_intent(config, fake_log):
    dev = make_device(config, fake_log, songs=["a"], play_type=PLAY_TYPE_SEQ)
    dev._cloud_snapshot = {"_ok": True, "status": 0, "duration": 10}
    dev._local_play_at = time.time()  # 3 秒宽限期内以本地意图为准
    dev.is_playing = True

    playing, snap = dev.get_display_state()

    assert playing is True
    assert snap is dev._cloud_snapshot


def test_find_cur_playlist_priority(config, fake_log):
    dev = make_device(config, fake_log, songs=["a"], play_type=PLAY_TYPE_SEQ)
    dev.xiaomusic.music_library.music_list = {
        "收藏": ["x"],
        "最近新增": ["y"],
        "全部": ["x", "y", "z", "w"],
        "所有歌曲": ["z", "w"],
        "专辑": ["z"],
    }

    assert dev.find_cur_playlist("x") == "收藏"
    assert dev.find_cur_playlist("y") == "最近新增"
    assert dev.find_cur_playlist("z") == "专辑"
    assert dev.find_cur_playlist("w") == "所有歌曲"
    assert dev.find_cur_playlist("不在任何歌单") == "全部"


def test_cancel_all_timer_clears_every_timer(config, fake_log):
    dev = make_device(config, fake_log, songs=["a"], play_type=PLAY_TYPE_SEQ)

    class Timer:
        def __init__(self):
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

    timers = [Timer() for _ in range(4)]
    dev._next_timer, dev._stop_timer, dev._tts_timer, dev._prefetch_timer = timers

    dev.cancel_all_timer()

    assert all(t.cancelled for t in timers)
    assert dev._next_timer is None
    assert dev._stop_timer is None
    assert dev._tts_timer is None
    assert dev._prefetch_timer is None


def test_dict_clear_cancels_each_device(config, fake_log):
    dev1 = make_device(config, fake_log, songs=["a"], play_type=PLAY_TYPE_SEQ)
    dev2 = make_device(config, fake_log, songs=["b"], play_type=PLAY_TYPE_SEQ)
    called = []
    dev1.cancel_all_timer = lambda: called.append("dev1")
    dev2.cancel_all_timer = lambda: called.append("dev2")
    devices = {"d1": dev1, "d2": dev2}

    XiaoMusicDevice.dict_clear(devices)

    assert devices == {}
    assert sorted(called) == ["dev1", "dev2"]


# --------------------------------------------------------------------------
# 云端接口的失败分支（fake，离线）
# --------------------------------------------------------------------------
async def test_get_cloud_status_error_paths(config, fake_log):
    mina = FakeMina(error=RuntimeError("net"))
    dev = make_device(config, fake_log, songs=["a"], mina_service=mina)
    assert await dev.get_cloud_status() is None

    # 真机返回：字段在顶层，没有 data.info 包装。
    # 旧实现只读 data.info，把这种「完全正常」的响应当成异常丢弃，
    # 导致 UI 播放状态与进度条永远是空。这里锁死这个回归。
    top_level = FakeMina(
        {
            "status": 2,
            "volume": 5,
            "play_song_detail": {"position": 1000, "duration": 2000},
        }
    )
    dev2 = make_device(config, fake_log, songs=["a"], mina_service=top_level)
    snap = await dev2.get_cloud_status()
    assert snap is not None, "顶层字段必须被解析"
    assert snap["status"] == 2 and snap["volume"] == 5 and snap["duration"] == 2.0

    # info 是坏 JSON 时回退到顶层，而不是整体判失败
    bad_json = FakeMina({"status": 1, "data": {"info": "{"}})
    dev3 = make_device(config, fake_log, songs=["a"], mina_service=bad_json)
    snap3 = await dev3.get_cloud_status()
    assert snap3 is not None and snap3["status"] == 1

    # 完全取不到任何字段（空 dict / 非 dict）才算异常
    dev4 = make_device(config, fake_log, songs=["a"], mina_service=FakeMina({}))
    assert await dev4.get_cloud_status() is None

    dev5 = make_device(config, fake_log, songs=["a"], mina_service=FakeMina(None))
    assert await dev5.get_cloud_status() is None


async def test_get_player_status_error_returns_default(config, fake_log):
    dev = make_device(
        config, fake_log, songs=["a"], mina_service=FakeMina(error=RuntimeError("boom"))
    )

    assert await dev.get_player_status() == {"volume": 0, "status": 0}


async def test_set_volume_calls_cloud_and_swallows_errors(config, fake_log):
    mina = FakeMina()
    dev = make_device(config, fake_log, songs=["a"], mina_service=mina)

    await dev.set_volume(30)
    assert mina.volume_calls == [("dev-1", 30)]

    dev2 = make_device(
        config, fake_log, songs=["a"], mina_service=FakeMina(error=RuntimeError("boom"))
    )
    await dev2.set_volume(10)  # 云端报错不应抛出


async def test_cloud_polling_starts_and_stops(config, fake_log):
    info = json.dumps({"status": 0, "volume": 1, "track_list": []})
    mina = FakeMina({"code": 0, "data": {"info": info}})
    dev = make_device(config, fake_log, songs=["a"], mina_service=mina)

    dev.start_cloud_polling()
    assert dev._ws_subscribers == 1
    await asyncio.sleep(0)
    assert mina.calls  # 已有订阅者，立即拉取一次快照

    dev.stop_cloud_polling()
    await asyncio.sleep(0)
    assert dev._ws_subscribers == 0
    assert dev._poll_task is None


async def test_cloud_polling_backs_off_on_failure(config, fake_log):
    mina = FakeMina(error=RuntimeError("boom"))
    dev = make_device(config, fake_log, songs=["a"], mina_service=mina)

    dev.start_cloud_polling()
    await asyncio.sleep(0)
    assert mina.calls  # 失败也调用过，随后进入退避
    assert dev._cloud_snapshot is None

    dev.stop_cloud_polling()
    await asyncio.sleep(0)
    assert dev._poll_task is None

# --------------------------------------------------------------------------
# get_music 修复后的正式回归（lead 已修，xfail 已转为断言）
# --------------------------------------------------------------------------
def test_get_music_skips_missing_file_without_recursion(config, fake_log):
    """回归（lead 已修）：歌单里的歌文件被删时应跳过它取下一首，不递归也不死循环。"""
    dev = make_device(
        config,
        fake_log,
        songs=["a", "b", "c"],
        play_type=PLAY_TYPE_SEQ,
        cur_music="a",
        missing={"b"},
    )

    got = dev.get_music("next")

    assert got == "c"


def test_get_music_returns_empty_when_no_file_exists(config, fake_log):
    dev = make_device(
        config,
        fake_log,
        songs=["a", "b"],
        play_type=PLAY_TYPE_SEQ,
        cur_music="a",
        missing={"a", "b"},
    )

    got = dev.get_music("next")

    assert got == ""  # 全不可用：返回空串而不是死循环


def test_get_music_rnd_reshuffle_picks_from_new_list(config, fake_log):
    """回归（lead 已修）：RND 一轮结束洗牌后必须从新列表取 index 1。"""
    songs = ["s" + str(i) for i in range(8)]
    dev = make_device(config, fake_log, songs=songs, play_type=PLAY_TYPE_RND)
    last = dev._play_list[-1]
    dev.state.set_track(last)

    got = dev.get_music("next")

    # 洗牌后 index 0 已置顶当前曲，下一首应是新列表的 index 1
    assert got == dev._play_list[1]


# --------------------------------------------------------------------------
# 「暂停不管用」回归：音箱把 status 放在顶层，旧实现只读 data.info
# --------------------------------------------------------------------------
async def test_playing_detected_from_top_level_fields(config, fake_log):
    """真机返回的顶层 status=1 必须被判为「正在播放」。"""
    mina = FakeMina({"status": 1, "volume": 5, "track_list": []})
    dev = make_device(config, fake_log, songs=["a"], mina_service=mina)

    assert await dev.get_if_xiaoai_is_playing() is True


async def test_idle_detected_from_top_level_fields(config, fake_log):
    mina = FakeMina({"status": 2, "volume": 5, "track_list": []})
    dev = make_device(config, fake_log, songs=["a"], mina_service=mina)

    assert await dev.get_if_xiaoai_is_playing() is False


async def test_force_stop_always_sends_player_stop(config, fake_log):
    """核心回归：即使暂停后云端已报 idle，也必须补一条 player_stop。"""
    mina = FakeMina({"status": 2, "volume": 5, "track_list": []})
    dev = make_device(config, fake_log, songs=["a"], mina_service=mina)

    await dev.force_stop_xiaoai("dev-1")

    assert mina.pause_calls == ["dev-1"]
    assert mina.stop_calls == ["dev-1"], "player_stop 被静默跳过 = 暂停不管用"


async def test_stop_if_playing_sends_stop_when_playing(config, fake_log):
    """云端报 playing 时，非强制路径也要发 player_stop。"""
    mina = FakeMina({"status": 1, "volume": 5, "track_list": []})
    dev = make_device(config, fake_log, songs=["a"], mina_service=mina)

    await dev.stop_if_xiaoai_is_playing("dev-1")

    assert mina.stop_calls == ["dev-1"]


async def test_stop_if_playing_skips_when_idle_and_not_forced(config, fake_log):
    """未播放且未强制时不应打扰云端（保持原有节流语义）。"""
    mina = FakeMina({"status": 2, "volume": 5, "track_list": []})
    dev = make_device(config, fake_log, songs=["a"], mina_service=mina)

    await dev.stop_if_xiaoai_is_playing("dev-1")

    assert mina.stop_calls == []


# --------------------------------------------------------------------------
# 「点暂停却从头播放」回归：播放按钮必须真的暂停（断点保留），可断点续播
# --------------------------------------------------------------------------
async def test_pause_only_sends_player_pause_and_stops_next_timer(config, fake_log):
    """暂停 = player_pause（不结束会话），并且要摘掉下一首定时器。"""
    mina = FakeMina({"status": 1, "volume": 5, "track_list": []})
    dev = make_device(config, fake_log, songs=["a"], cur_music="a", mina_service=mina)
    dev.is_playing = True
    dev._duration = 120
    dev._start_time = time.time()
    await dev.set_next_music_timeout(60)
    assert dev._next_timer is not None

    ok = await dev.pause()

    assert ok is True
    assert mina.pause_calls == ["dev-1"]
    assert mina.stop_calls == [], "暂停不能发 player_stop：会话一结束就只能从头播"
    assert dev.is_playing is False
    assert dev._next_timer is None, "暂停中若留着定时器，到点会自己播下一首"
    assert dev._paused_remain > 0


async def test_pause_then_resume_reuses_breakpoint(config, fake_log):
    """续播必须用 player_play 从断点继续，而不是重新下发播放。"""
    mina = FakeMina({"status": 2, "volume": 5, "track_list": []})
    dev = make_device(config, fake_log, songs=["a"], cur_music="a", mina_service=mina)
    dev.is_playing = True
    dev._duration = 120
    dev._start_time = time.time()
    await dev.pause()

    ok = await dev.resume()

    assert ok is True
    assert mina.play_calls == ["dev-1"], "续播没有调用 player_play"
    assert dev.is_playing is True
    assert dev._paused is False
    assert dev._next_timer is not None, "续播后要按剩余时长重新武装下一首定时器"
    await dev.cancel_next_timer()


async def test_resume_without_pause_session_plays_current_music(config, fake_log):
    """没有暂停会话（例如已停止）时不能盲发 player_play，退回从头播放当前歌曲。"""
    mina = FakeMina({"status": 0, "volume": 5, "track_list": []})
    dev = make_device(config, fake_log, songs=["a"], cur_music="a", mina_service=mina)
    calls = []

    async def fake_play_music_list(list_name, music_name):
        calls.append((list_name, music_name))

    dev.play_music_list = fake_play_music_list

    ok = await dev.resume()

    assert ok is True
    assert mina.play_calls == []
    assert calls == [("全部", "a")]


async def test_resume_with_other_song_plays_that_song(config, fake_log):
    """暂停后用户换了一首歌：应点播那首歌，而不是续播旧断点。"""
    mina = FakeMina({"status": 2, "volume": 5, "track_list": []})
    dev = make_device(config, fake_log, songs=["a", "b"], cur_music="a", mina_service=mina)
    dev.is_playing = True
    dev._duration = 120
    dev._start_time = time.time()
    await dev.pause()
    calls = []

    async def fake_play_music_list(list_name, music_name):
        calls.append((list_name, music_name))

    dev.play_music_list = fake_play_music_list

    ok = await dev.resume(music_name="b", list_name="全部")

    assert ok is True
    assert mina.play_calls == [], "换了歌还去续播旧断点 = 点播失效"
    assert dev._paused is False
    assert calls == [("全部", "b")]


async def test_pause_requires_cloud_service(config, fake_log):
    dev = make_device(config, fake_log, songs=["a"], mina_service=None)

    assert await dev.pause() is False


def test_paused_offset_keeps_breakpoint_instead_of_zero(config, fake_log):
    """暂停时进度条要停在断点，不能跳回 0（否则看起来像被重播）。"""
    dev = make_device(config, fake_log, songs=["a"])
    dev._cloud_snapshot = {"_ok": True, "status": 2, "position": 42.0, "duration": 180.0}
    dev._cloud_snapshot_at = time.time()

    assert dev.get_offset_duration() == (42.0, 180.0)


def test_display_state_shows_paused_immediately(config, fake_log):
    """点击暂停后 UI 立刻变成「未播放」，不能等云端慢几秒才更新。"""
    dev = make_device(config, fake_log, songs=["a"])
    dev._cloud_snapshot = {"_ok": True, "status": 1, "duration": 10, "position": 1}
    dev._cloud_snapshot_at = time.time()
    dev.is_playing = True
    dev._local_pause_at = time.time()

    playing, snap = dev.get_display_state()

    assert playing is False
    assert snap is dev._cloud_snapshot


def test_display_state_keeps_playing_until_cloud_confirms_resume(config, fake_log):
    """续播后云端快照还停在 status=2（每 3 秒才刷一次），UI 不能闪回「未播放」。"""
    dev = make_device(config, fake_log, songs=["a"])
    dev._cloud_snapshot = {"_ok": True, "status": 2, "duration": 10, "position": 1}
    dev._cloud_snapshot_at = time.time()
    dev.is_playing = True
    dev._local_play_at = time.time()

    assert dev.get_display_state()[0] is True

    # 超过等待窗口仍是 status=2（续播其实失败了）→ 以云端为准
    dev._local_play_at = time.time() - dev.INTENT_CONFIRM_SEC - 1

    assert dev.get_display_state()[0] is False


def test_display_state_waits_for_cloud_after_pause(config, fake_log):
    """暂停后云端仍报 status=1 时也不闪回「播放中」。"""
    dev = make_device(config, fake_log, songs=["a"])
    dev._cloud_snapshot = {"_ok": True, "status": 1, "duration": 10, "position": 1}
    dev._cloud_snapshot_at = time.time()
    dev.is_playing = False
    dev._local_pause_at = time.time()

    assert dev.get_display_state()[0] is False

    # 很长时间后云端还在播 → 说明是被别的方式恢复了，以云端为准
    dev._local_pause_at = time.time() - dev.INTENT_CONFIRM_SEC - 1

    assert dev.get_display_state()[0] is True


