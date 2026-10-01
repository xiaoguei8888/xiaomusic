"""MusicLibrary 本地能力测试（离线，全部基于 tmp_path 假音乐目录）。

覆盖：
- gen_all_music_list：目录扫描、排除规则、文件夹歌单、"其他"归并、数字感知排序
- 最近新增歌单的数量上限与 mtime 排序
- 自定义歌单：JSON 加载、与系统歌单名冲突改名、增删改查、系统歌单不持久化
- 模糊匹配（enable_fuzzy_match 开关）、本地播放 URL、tag cache 往返
"""

from __future__ import annotations

import json
import os
import types
import urllib.parse

from xiaomusic.events import CONFIG_CHANGED, EventBus
from xiaomusic.music_library import MusicLibrary


def test_gen_all_music_list_scans_nested_dirs_and_filters(library, make_music_file):
    make_music_file("a.mp3")
    make_music_file("b.flac")
    make_music_file("专辑/song 2.mp3")
    make_music_file("tmp/ignored.mp3")  # exclude_dirs 默认含 tmp
    make_music_file("@eaDir/ignored.mp3")  # exclude_dirs 默认含 @eaDir
    make_music_file(".hidden.mp3")  # 隐藏文件
    make_music_file("notes.txt")  # 不支持的后缀

    library.gen_all_music_list()

    assert set(library.all_music) == {"a", "b", "song 2"}
    assert set(library.music_list["专辑"]) == {"song 2"}
    assert set(library.music_list["其他"]) == {"a", "b"}  # 主目录文件归入"其他"
    assert library.music_list["全部"] == library.music_list["所有歌曲"]
    assert set(library.music_list["全部"]) == {"a", "b", "song 2"}
    assert "tmp" not in library.music_list
    assert "@eaDir" not in library.music_list


def test_gen_all_music_list_uses_numeric_aware_sort(library, make_music_file):
    make_music_file("10 十.mp3")
    make_music_file("2 二.mp3")
    make_music_file("abc.mp3")

    library.gen_all_music_list()

    # custom_sort_key：数字前缀按数值排序，无数字的排最后
    assert library.music_list["全部"] == ["2 二", "10 十", "abc"]


def test_recently_added_respects_limit_and_mtime(library, config, make_music_file):
    base = 1_700_000_000
    make_music_file("old.mp3", mtime=base)
    make_music_file("mid.mp3", mtime=base + 10)
    make_music_file("new.mp3", mtime=base + 20)
    config.recently_added_playlist_len = 2

    library.gen_all_music_list()

    recent = library.music_list["最近新增"]
    # 只保留 mtime 最新的 2 首；第 3 首（old）被截断
    assert set(recent) == {"new", "mid"}
    assert "old" not in recent


def test_custom_playlist_loaded_from_json(library, config, make_music_file):
    make_music_file("a.mp3")
    config.custom_play_list_json = json.dumps({"我的歌单": ["a"]}, ensure_ascii=False)

    library.gen_all_music_list()

    assert library.get_play_list_names() == ["我的歌单"]
    assert library.music_list["我的歌单"] == ["a"]
    assert "我的歌单" not in library.default_music_list_names


def test_custom_playlist_conflicting_with_system_name_is_renamed(
    library, config, make_music_file
):
    make_music_file("a.mp3")
    config.custom_play_list_json = json.dumps({"全部": ["a"]}, ensure_ascii=False)

    library.gen_all_music_list()

    saved = json.loads(config.custom_play_list_json)
    assert "全部" not in saved
    assert saved["全部(自定义)"] == ["a"]
    assert library.music_list["全部(自定义)"] == ["a"]
    assert set(library.music_list["全部"]) == {"a"}  # 系统歌单未被自定义覆盖


def test_custom_playlist_conflict_suffix_increments(library, config, make_music_file):
    make_music_file("a.mp3")
    # 先把 "全部(自定义)" 占掉，再出现保留名 "全部"：
    # 这样才会走 _build_custom_conflict_name 的数字后缀循环（...2）
    config.custom_play_list_json = json.dumps(
        {"全部(自定义)": [], "全部": []}, ensure_ascii=False
    )

    library.gen_all_music_list()

    assert set(json.loads(config.custom_play_list_json)) == {
        "全部(自定义)",
        "全部(自定义)2",
    }


def test_play_list_crud(library, make_music_file):
    make_music_file("a.mp3")
    make_music_file("b.mp3")
    library.gen_all_music_list()

    assert library.play_list_add("我的收藏") is True
    assert library.play_list_add("我的收藏") is False  # 重名
    assert library.play_list_add("全部") is False  # 与系统歌单冲突
    assert library.get_play_list_names() == ["我的收藏"]

    assert library.play_list_update_music("我的收藏", ["a", "ghost", "a", "b"]) is True
    assert library.play_list_musics("我的收藏") == ("OK", ["a", "b"])
    assert library.music_list["我的收藏"] == ["a", "b"]

    assert library.play_list_update_name("我的收藏", "收藏夹") is True
    assert library.play_list_update_name("收藏夹", "全部") is False
    assert "我的收藏" not in library.music_list
    assert library.music_list["收藏夹"] == ["a", "b"]

    assert library.play_list_del("收藏夹") is True
    assert library.play_list_del("收藏夹") is False
    assert "收藏夹" not in library.music_list


def test_play_list_ops_on_missing_name(library, make_music_file):
    make_music_file("a.mp3")
    library.gen_all_music_list()

    assert library.play_list_del("不存在") is False
    assert library.play_list_update_name("不存在", "新名字") is False
    assert library.play_list_musics("不存在") == ("歌单不存在", [])
    assert library.play_list_del_music("不存在", ["a"]) is False


def test_reserved_playlist_name_is_rejected_and_logged(
    library, fake_log, make_music_file
):
    make_music_file("a.mp3")
    library.gen_all_music_list()
    fake_log.records.clear()

    assert library.play_list_add("全部") is False
    assert any("冲突" in msg for msg in fake_log.messages("info"))


def test_system_playlist_mutations_are_not_persisted(library, config, make_music_file):
    make_music_file("a.mp3")
    make_music_file("b.mp3")
    library.gen_all_music_list()
    library.music_list["全部"].remove("b")

    assert library.play_list_add_music("全部", ["b", "ghost"]) is True
    assert "b" in library.music_list["全部"]
    assert "ghost" not in library.music_list["全部"]
    assert config.custom_play_list_json == ""  # 系统歌单无需持久化

    assert library.play_list_del_music("全部", ["b"]) is True
    assert "b" not in library.music_list["全部"]


def test_saving_playlist_publishes_config_changed(config, fake_log, make_music_file):
    make_music_file("a.mp3")
    events = []
    bus = EventBus()
    bus.subscribe(CONFIG_CHANGED, lambda **kwargs: events.append(kwargs))
    lib = MusicLibrary(config, fake_log, event_bus=bus)
    lib.try_gen_all_music_tag = lambda *args, **kwargs: None

    lib.gen_all_music_list()
    assert lib.play_list_add("收藏") is True

    assert len(events) == 1, events
    assert "收藏" in json.loads(config.custom_play_list_json)


def test_music_library_without_event_bus_is_safe(config, fake_log, make_music_file):
    make_music_file("a.mp3")
    lib = MusicLibrary(config, fake_log)
    lib.try_gen_all_music_tag = lambda *args, **kwargs: None
    lib.gen_all_music_list()

    assert lib.play_list_add("安静歌单") is True
    assert "安静歌单" in lib.music_list


def test_get_filename_and_is_music_exist(library, make_music_file):
    path = make_music_file("a.mp3")
    library.gen_all_music_list()

    assert library.get_filename("a") == path
    assert library.is_music_exist("a") is True
    assert library.get_filename("ghost") == ""
    assert library.is_music_exist("ghost") is False

    os.remove(path)  # 扫描后文件被删：不再返回过期路径
    assert library.get_filename("a") == ""
    assert library.is_music_exist("a") is False


def test_find_real_music_name_exact_substring_and_fuzzy(library, make_music_file):
    make_music_file("周杰伦 - 晴天.mp3")
    make_music_file("test song.mp3")
    library.gen_all_music_list()

    assert library.find_real_music_name("周杰伦 - 晴天", 1) == ["周杰伦 - 晴天"]
    assert library.find_real_music_name("晴天", 1) == ["周杰伦 - 晴天"]  # 子串命中
    assert library.find_real_music_name("test songz", 1) == ["test song"]  # 模糊命中
    assert library.find_real_music_name("完全无关的名字", 1) == []


def test_find_real_music_name_disabled_returns_empty(library, config, make_music_file):
    make_music_file("周杰伦 - 晴天.mp3")
    library.gen_all_music_list()
    config.enable_fuzzy_match = False

    assert library.find_real_music_name("晴天", 1) == []


def test_searchmusic_returns_substring_match(library, make_music_file):
    make_music_file("周杰伦 - 晴天.mp3")
    library.gen_all_music_list()

    assert "周杰伦 - 晴天" in library.searchmusic("晴天")


async def test_get_music_url_points_to_local_file(library, config, make_music_file):
    make_music_file("专辑/song 2.mp3")
    library.gen_all_music_list()

    url = await library.get_music_url("song 2")

    expected = "http://127.0.0.1:58090/music/" + urllib.parse.quote("专辑/song 2.mp3")
    assert url == expected
    assert await library.get_music_url("ghost") == ""


async def test_get_music_url_adds_access_control_code_when_auth_enabled(
    library, config, make_music_file
):
    make_music_file("a.mp3")
    config.disable_httpauth = False
    library.gen_all_music_list()

    url = await library.get_music_url("a")

    assert "/music/a.mp3" in url
    assert "code=" in url


async def test_get_music_duration_uses_tag_cache(library, make_music_file):
    make_music_file("a.mp3")
    library.gen_all_music_list()
    library.all_music_tags["a"] = {"duration": 123.5}

    assert await library.get_music_duration("a") == 123.5
    assert await library.get_music_duration("ghost") == 0


def test_tag_cache_save_load_and_refresh(library, config):
    library.all_music_tags = {"a": {"duration": 1.5}}
    library.try_save_tag_cache()

    assert os.path.exists(config.tag_cache_path)
    assert library.try_load_from_tag_cache() == {"a": {"duration": 1.5}}

    library.refresh_music_tag()

    assert library.all_music_tags == {}
    with open(config.tag_cache_path, encoding="utf-8") as f:
        assert json.load(f) == {}


# --------------------------------------------------------------------------
# 其余本地分支：例外兜底、tag 读写、命名冲突链、访问器
# --------------------------------------------------------------------------
def test_invalid_custom_playlist_json_does_not_break_scan(
    config, fake_log, make_music_file
):
    make_music_file("a.mp3")
    config.custom_play_list_json = "{not-json"
    lib = MusicLibrary(config, fake_log)
    lib.try_gen_all_music_tag = lambda *args, **kwargs: None

    lib.gen_all_music_list()  # 不应抛出

    assert lib.music_list["全部"] == ["a"]  # 扫描结果仍然可用
    assert any("Execption" in msg for msg in fake_log.messages("exception"))


def test_custom_playlist_conflict_index_loop(library, config, make_music_file):
    make_music_file("a.mp3")
    config.custom_play_list_json = json.dumps(
        {"全部(自定义)": [], "全部(自定义)2": [], "全部": []}, ensure_ascii=False
    )

    library.gen_all_music_list()

    saved = json.loads(config.custom_play_list_json)
    assert "全部(自定义)3" in saved  # 前两个候选名都被占用时的序号自增


def test_play_list_rename_rejects_existing_target(library, make_music_file):
    make_music_file("a.mp3")
    library.gen_all_music_list()

    assert library.play_list_add("甲") is True
    assert library.play_list_add("乙") is True
    assert library.play_list_update_name("甲", "乙") is False
    assert set(library.get_play_list_names()) == {"甲", "乙"}


def test_play_list_update_music_rejects_reserved_name(library, make_music_file):
    make_music_file("a.mp3")
    library.gen_all_music_list()

    # "全部" 是系统歌单，play_list_add 会拒绝，进而整体返回 False
    assert library.play_list_update_music("全部", ["a"]) is False


def test_custom_playlist_add_and_del_music_persists(library, config, make_music_file):
    make_music_file("a.mp3")
    library.gen_all_music_list()

    assert library.play_list_add("收藏") is True
    assert library.play_list_add_music("收藏", ["a", "ghost"]) is True
    assert library.play_list_add_music("收藏", ["a"]) is True  # 幂等
    assert library.play_list_musics("收藏") == ("OK", ["a"])
    assert json.loads(config.custom_play_list_json)["收藏"] == ["a"]

    assert library.play_list_del_music("收藏", ["a"]) is True
    assert library.play_list_musics("收藏") == ("OK", [])
    assert json.loads(config.custom_play_list_json)["收藏"] == []


def test_music_and_playlist_accessors(library, make_music_file):
    make_music_file("a.mp3")
    library.gen_all_music_list()

    assert library.get_music_list() is library.music_list
    assert library.get_all_music() is library.all_music


def test_find_real_music_list_name_matching_and_disabled(
    library, config, make_music_file
):
    make_music_file("a.mp3")
    library.gen_all_music_list()

    assert library.find_real_music_list_name("最近新") == "最近新增"

    config.enable_fuzzy_match = False
    assert library.find_real_music_list_name("任意名字") == "任意名字"


async def test_get_music_tags_rewrites_picture_url(library, config, make_music_file):
    make_music_file("a.mp3")
    library.gen_all_music_list()
    library.all_music_tags["a"] = {
        "picture": os.path.join(config.picture_cache_path, "cover.jpg")
    }

    tags = await library.get_music_tags("a")

    assert tags["picture"] == "http://127.0.0.1:58090/picture/cover.jpg"


async def test_get_music_tags_default_for_unknown_music(library):
    tags = await library.get_music_tags("unknown")

    assert tags["title"] == ""
    assert tags["picture"] == ""


def test_set_music_tag_updates_cache_without_writing_file(
    library, config, make_music_file
):
    make_music_file("a.mp3")
    library.gen_all_music_list()
    info = types.SimpleNamespace(
        title="T",
        artist="A",
        album="AL",
        year="2024",
        genre="pop",
        lyrics="lrc",
        picture="",
    )

    assert library.set_music_tag("a", info) == "OK"

    assert library.all_music_tags["a"]["title"] == "T"
    assert library.all_music_tags["a"]["artist"] == "A"
    assert config.enable_save_tag is False  # 未开启写回，不改动音频文件


def test_set_music_tag_refuses_while_generating(library, make_music_file):
    make_music_file("a.mp3")
    library.gen_all_music_list()
    library._tag_generation_task = True
    info = types.SimpleNamespace(
        title="T",
        artist="A",
        album="AL",
        year="2024",
        genre="pop",
        lyrics="lrc",
        picture="",
    )

    assert library.set_music_tag("a", info) == "Tag generation task running"


def test_ensure_single_thread_for_tag(library):
    assert library.ensure_single_thread_for_tag() is True
    library._tag_generation_task = True
    assert library.ensure_single_thread_for_tag() is False


def test_refresh_music_tag_skips_while_generating(library, config):
    library._tag_generation_task = True

    library.refresh_music_tag()

    assert not os.path.exists(config.tag_cache_path)  # 未清空、未重建


def test_try_load_from_tag_cache_handles_corrupt_file(library, config):
    with open(config.tag_cache_path, "w", encoding="utf-8") as f:
        f.write("{not json")

    assert library.try_load_from_tag_cache() == {}


def test_try_gen_all_music_tag_without_running_loop_is_noop(config, fake_log):
    lib = MusicLibrary(config, fake_log)
    lib.all_music = {"a": os.path.join(config.music_path, "a.mp3")}

    lib.try_gen_all_music_tag()  # 无 running loop：直接返回

    assert lib.all_music_tags == {}


async def test_gen_all_music_tag_finishes_on_undecodable_file(
    library, config, make_music_file
):
    path = make_music_file("a.mp3")

    await library._gen_all_music_tag(only_items={"a": path})

    # 假音频无法解码也不能让 tag 任务卡死：标志复位且缓存已落盘
    assert library._tag_generation_task is False
    assert os.path.exists(config.tag_cache_path)


async def test_get_music_duration_undecodable_file_returns_zero(
    library, make_music_file
):
    make_music_file("a.mp3")
    library.gen_all_music_list()

    assert await library.get_music_duration("a") == 0
    assert "a" not in library.all_music_tags  # 失败不写缓存


async def test_get_music_duration_missing_file_returns_zero(library, make_music_file):
    path = make_music_file("a.mp3")
    library.gen_all_music_list()
    os.remove(path)

    assert await library.get_music_duration("a") == 0


# --------------------------------------------------------------------------
# 已知缺陷回归（lead 已修：空匹配回退为原始输入；xfail 已转为断言）
# --------------------------------------------------------------------------
def test_find_real_music_list_name_no_match_returns_input(library, make_music_file):
    """回归（lead 已修）：无匹配时应原样返回歌单名，而不是 IndexError。"""
    make_music_file("a.mp3")
    library.gen_all_music_list()

    assert library.find_real_music_list_name("完全不存在") == "完全不存在"
