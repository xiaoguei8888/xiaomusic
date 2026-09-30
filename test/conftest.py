"""pytest 共享夹具与离线测试辅助。

约定（对应 task-9 验收）：
- 不访问小米云，不依赖真实 music/ 或 conf/ 目录内容；
- 音频文件用 tmp_path 下的最小假文件（静默字节流），不要求可解码音频；
- 兼容 test/ 下历史的“纯 assert 脚本”：它们直接 `python test/xxx.py` 仍可运行，
  在 pytest 下缺失的夹具由本文件补齐（见文件末尾的 config / filename）。
"""

from __future__ import annotations

import os

import pytest

from xiaomusic.config import Config
from xiaomusic.music_library import MusicLibrary

# 最小假音频：只用于路径/扫描断言，不经过解码器
FAKE_MEDIA_BYTES = b"\x00" * 256


class FakeLog:
    """记录日志调用，便于断言“冲突被记录 / 降级被感知”等可观测行为。"""

    def __init__(self):
        self.records = []

    def _record(self, level, msg, *args):
        text = msg % args if args else str(msg)
        self.records.append((level, text))

    def debug(self, msg, *args):
        self._record("debug", msg, *args)

    def info(self, msg, *args):
        self._record("info", msg, *args)

    def warning(self, msg, *args):
        self._record("warning", msg, *args)

    def error(self, msg, *args):
        self._record("error", msg, *args)

    def exception(self, msg, *args):
        self._record("exception", msg, *args)

    def messages(self, level=None):
        return [text for lv, text in self.records if level is None or lv == level]


def make_config(base_dir, **overrides):
    """构造指向临时目录、且与外部环境无关的 Config。"""
    params = {
        "music_path": os.path.join(str(base_dir), "music"),
        "conf_path": os.path.join(str(base_dir), "conf"),
        "hostname": "http://127.0.0.1",
        "public_port": 58090,
        "disable_httpauth": True,
        "enable_fuzzy_match": True,
        "fuzzy_match_cutoff": 0.6,
    }
    params.update(overrides)
    return Config(**params)


@pytest.fixture
def fake_log():
    return FakeLog()


@pytest.fixture
def config(tmp_path):
    """默认 Config：music/ 与 conf/ 都在 tmp_path 下，关闭 HTTP 鉴权。"""
    cfg = make_config(tmp_path)
    os.makedirs(cfg.music_path, exist_ok=True)
    os.makedirs(cfg.conf_path, exist_ok=True)
    return cfg


@pytest.fixture
def music_dir(config):
    return config.music_path


@pytest.fixture
def make_music_file(config):
    """在临时 music 目录里造文件，返回绝对路径；mtime 可指定。"""

    def _make(relpath, data=FAKE_MEDIA_BYTES, mtime=None):
        path = os.path.join(config.music_path, relpath)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    return _make


@pytest.fixture
def library(config, fake_log):
    """MusicLibrary 最小实例（离线）。

    禁用 tag 后台任务：pytest-asyncio 下存在 running loop 时，
    gen_all_music_list() 会真的排队解码假音频（依赖 ffprobe/解码器），
    本地能力测试不需要它。
    """
    lib = MusicLibrary(config, fake_log)
    lib.try_gen_all_music_tag = lambda *args, **kwargs: None
    return lib


# ---------------------------------------------------------------------------
# 历史纯 assert 脚本的兼容夹具
# test/test_music_duration.py 与 test/test_music_tags.py 中的 test_one_music
# 需要真实音频文件；离线 pytest 下显式 skip，直接 python 运行行为不变。
# ---------------------------------------------------------------------------
@pytest.fixture
def filename():
    pytest.skip("需要真实 music/ 下的音频文件；pytest 离线运行跳过该用例")
