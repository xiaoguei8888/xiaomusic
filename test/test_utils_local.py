"""MusicLibrary/设备层依赖的纯本地工具测试（离线）。

覆盖 file_utils / text_utils / system_utils 中不碰网络、不碰真实音乐库的函数：
- file_utils: traverse_music_directory（深度截断/排除/后缀过滤）、safe_join_path、
  not_in_dirs、_longest_common_prefix、remove_common_prefix、chmodfile/chmoddir
- text_utils: custom_sort_key、chinese_to_number、parse_ordinal_suffix、
  parse_str_to_dict、list2str、calculate_tts_elapse、find_key_by_partial_string、
  traditional_to_simple、keyword_detection、real_search/find_best_match/fuzzyfinder、
  split_sentences
- system_utils: cookiejar_from_dict、parse_cookie_string(_to_dict)、validate_proxy、
  get_random、deepcopy_data_no_sensitive_info、try_add_access_control_param
"""

from __future__ import annotations

import os
import stat

import pytest

from xiaomusic.const import SUPPORT_MUSIC_TYPE
from xiaomusic.utils.file_utils import (
    _longest_common_prefix,
    chmoddir,
    chmodfile,
    not_in_dirs,
    remove_common_prefix,
    safe_join_path,
    traverse_music_directory,
)
from xiaomusic.utils.system_utils import (
    cookiejar_from_dict,
    deepcopy_data_no_sensitive_info,
    get_random,
    parse_cookie_string,
    parse_cookie_string_to_dict,
    try_add_access_control_param,
    validate_proxy,
)
from xiaomusic.utils.text_utils import (
    calculate_tts_elapse,
    chinese_to_number,
    custom_sort_key,
    find_best_match,
    find_key_by_partial_string,
    fuzzyfinder,
    keyword_detection,
    list2str,
    parse_ordinal_suffix,
    parse_str_to_dict,
    real_search,
    split_sentences,
    traditional_to_simple,
)


# ---------------------------------------------------------------------------
# file_utils
# ---------------------------------------------------------------------------
def test_traverse_music_directory_respects_depth(config, make_music_file):
    root = os.path.basename(config.music_path)
    nested = make_music_file("a/deep/song.mp3")
    top = make_music_file("root.mp3")

    shallow = traverse_music_directory(config.music_path, 1, set(), SUPPORT_MUSIC_TYPE)
    deep = traverse_music_directory(config.music_path, 10, set(), SUPPORT_MUSIC_TYPE)

    assert nested in shallow[root]  # 超过深度上限的目录被截断归并
    assert deep["deep"] == [nested]
    assert deep[root] == [top]


def test_traverse_music_directory_filters_excluded_and_unsupported(config, make_music_file):
    keep = make_music_file("keep.mp3")
    make_music_file("skip_tmp/x.mp3")
    make_music_file("cover.jpg")

    result = traverse_music_directory(
        config.music_path, 10, {"skip_tmp"}, SUPPORT_MUSIC_TYPE
    )

    files = [path for group in result.values() for path in group]
    assert files == [keep]


def test_safe_join_path_allows_inside_and_blocks_escape(tmp_path):
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "f.txt").write_text("x", encoding="utf-8")

    inside = safe_join_path(str(root), "sub/f.txt")

    assert inside == os.path.realpath(str(root / "sub" / "f.txt"))
    with pytest.raises(ValueError):
        safe_join_path(str(root), "../outside.txt")


def test_not_in_dirs_and_longest_common_prefix(tmp_path):
    root = tmp_path / "music"
    root.mkdir()
    inside = root / "a.mp3"
    inside.write_bytes(b"x")
    outside = tmp_path / "b.mp3"
    outside.write_bytes(b"x")

    assert not_in_dirs(str(inside), [str(root)]) is False
    assert not_in_dirs(str(outside), [str(root)]) is True

    assert _longest_common_prefix(["abc", "abd"]) == "ab"
    assert _longest_common_prefix(["abc", "x"]) == ""
    assert _longest_common_prefix([]) == ""
    assert _longest_common_prefix([""]) == ""


def test_remove_common_prefix_renames_files(tmp_path):
    songs = tmp_path / "songs"
    songs.mkdir()
    for name in ("01 - a.mp3", "01 - b.mp3"):
        (songs / name).write_bytes(b"x")

    remove_common_prefix(str(songs))

    assert sorted(os.listdir(songs)) == ["a.mp3", "b.mp3"]


def test_remove_common_prefix_without_shared_prefix_keeps_names(tmp_path):
    songs = tmp_path / "songs2"
    songs.mkdir()
    (songs / "x.mp3").write_bytes(b"x")
    (songs / "y.mp3").write_bytes(b"x")

    remove_common_prefix(str(songs))

    assert sorted(os.listdir(songs)) == ["x.mp3", "y.mp3"]


def test_chmod_file_and_dir(tmp_path):
    target_dir = tmp_path / "chmod"
    target_dir.mkdir()
    target = target_dir / "a.mp3"
    target.write_bytes(b"x")

    chmodfile(str(target))
    chmoddir(str(target_dir))

    assert stat.S_IMODE(os.stat(target).st_mode) == 0o775


# ---------------------------------------------------------------------------
# text_utils
# ---------------------------------------------------------------------------
def test_custom_sort_key_variants():
    assert custom_sort_key("2 abc") == (0, 2, "2 abc")
    assert custom_sort_key("abc2") == (1, "abc", 2)
    assert custom_sort_key("abc") == (2, "abc")


def test_chinese_to_number():
    assert chinese_to_number("三") == 3
    assert chinese_to_number("十") == 10
    assert chinese_to_number("十五") == 15
    assert chinese_to_number("二十三") == 23
    assert chinese_to_number("一百二十三") == 123


def test_parse_ordinal_suffix():
    assert parse_ordinal_suffix("爸爸的头不见了第二个") == ("爸爸的头不见了", 2)
    assert parse_ordinal_suffix("歌第二十三个") == ("歌", 23)
    assert parse_ordinal_suffix("第十五个") == ("第十五个", None)  # 缺少基础名
    assert parse_ordinal_suffix("普通标题") == ("普通标题", None)
    assert parse_ordinal_suffix("") == ("", None)


def test_parse_str_to_dict():
    assert parse_str_to_dict("a:1,b:2") == {"a": "1", "b": "2"}
    assert parse_str_to_dict("a:1,bad") == {"a": "1"}


def test_list2str_truncates_long_list():
    assert list2str([1, 2]) == "[1, 2]"
    assert "with len: 10" in list2str(list(range(10)))
    assert list2str(list(range(10)), verbose=True) == str(list(range(10)))


def test_calculate_tts_elapse_ignores_quotes_and_brackets():
    assert calculate_tts_elapse("你好") == 2 / 4.5
    assert calculate_tts_elapse("「你好」") == 2 / 4.5


def test_find_key_by_partial_string():
    assert find_key_by_partial_string({"下一首": "play_next"}, "帮我下一首吧") == "play_next"
    assert find_key_by_partial_string({"x": "y"}, "无关") is None


def test_traditional_to_simple():
    assert traditional_to_simple("國") == "国"


def test_keyword_detection_orders_and_limits():
    matched, remains = keyword_detection("abc", ["abc def", "zzz", "xabc"], n=2)

    assert "abc def" in matched
    assert "zzz" in remains
    assert keyword_detection("abc", ["zzz"], n=-1) == ([], ["zzz"])


def test_real_search_and_fuzzy_helpers():
    assert real_search("abc", ["abc def", "zzz"], 0.6, 1) == ["abc def"]
    assert find_best_match("晴天", ["周杰伦 - 晴天", "test song"], 0.6, 1) == [
        "周杰伦 - 晴天"
    ]
    assert find_best_match("test songz", ["周杰伦 - 晴天", "test song"], 0.6, 1) == [
        "test song"
    ]
    assert find_best_match("完全无关", ["周杰伦 - 晴天"], 0.6, 1) == []
    assert fuzzyfinder("abc", ["abc def", "zzz"]) == ["abc def"]


def test_find_best_match_falls_back_to_extra_index():
    extra = {"/path/bbb.mp3": "bbb"}

    assert find_best_match("bbb", ["aaa"], 0.6, 1, extra_search_index=extra) == ["bbb"]
    assert fuzzyfinder("bbb", ["aaa"], extra) == ["bbb"]


async def test_split_sentences_splits_on_punctuation():
    async def stream():
        for chunk in ("你好。", "世界！", "没有标点"):
            yield chunk

    got = [sentence async for sentence in split_sentences(stream())]

    assert got == ["你好。", "世界！", "没有标点"]


# ---------------------------------------------------------------------------
# system_utils
# ---------------------------------------------------------------------------
def test_cookiejar_and_cookie_string_helpers():
    jar = cookiejar_from_dict({"a": "1", "b": "2"})
    assert {c.name for c in jar} == {"a", "b"}
    assert parse_cookie_string_to_dict("a=1; b=2") == {"a": "1", "b": "2"}
    assert {c.name for c in parse_cookie_string("a=1")} == {"a"}


def test_validate_proxy_rejects_bad_scheme_and_missing_port():
    assert validate_proxy("http://127.0.0.1:7890") is True
    with pytest.raises(ValueError):
        validate_proxy("socks5://127.0.0.1:7890")
    with pytest.raises(ValueError):
        validate_proxy("http://127.0.0.1")


def test_get_random_length():
    got = get_random(8)

    assert len(got) == 8
    assert got.isalnum()


def test_deepcopy_anonymizes_sensitive_fields():
    src = {"account": "u", "password": "p", "other": "keep"}

    out = deepcopy_data_no_sensitive_info(src)

    assert out["account"] == "******"
    assert out["password"] == "******"
    assert out["other"] == "keep"
    assert src["account"] == "u"  # 原对象不被修改

    class Holder:
        account = "u"
        password = "p"
        other = "keep"

    holder = Holder()
    anonymized = deepcopy_data_no_sensitive_info(holder)

    assert anonymized.account == "******"
    assert anonymized.other == "keep"
    assert holder.account == "u"


def test_try_add_access_control_param(config):
    url = "http://127.0.0.1:58090/music/a.mp3"

    assert try_add_access_control_param(config, url) == url  # 关闭鉴权时原样返回

    config.disable_httpauth = False
    with_code = try_add_access_control_param(config, url)

    assert "code=" in with_code
    assert try_add_access_control_param(config, url) == with_code  # code 稳定可复现
