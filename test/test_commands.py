"""特征化测试：锁定 CommandHandler.match_cmd 的行为（重构前基线）。

运行：  .venv/bin/python test/test_commands.py
无需 pytest，纯 assert 脚本，与 test/ 现有风格一致。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xiaomusic.command_handler import CommandHandler  # noqa: E402
from xiaomusic.config import Config  # noqa: E402


class _Log:
    def info(self, *a, **k):
        pass

    def debug(self, *a, **k):
        pass


class _Device:
    """match_cmd 只读 is_playing 与 _pending_selection 两个字段。"""

    def __init__(self, playing=False, pending=None):
        self.is_playing = playing
        self._pending_selection = pending


def _handler(config=None):
    return CommandHandler(config=config or Config(), log=_Log(), xiaomusic_instance=None)


def test_full_match():
    got = _handler().match_cmd(_Device(), "下一首", ctrl_panel=True)
    assert got == ("play_next", ""), got
    print("full_match OK", got)


def test_fuzzy_match_arg_after():
    got = _handler().match_cmd(_Device(), "下一首吧", ctrl_panel=True)
    assert got == ("play_next", "吧"), got
    print("fuzzy_match_arg_after OK", got)


def test_arg_before_keyword():
    got = _handler().match_cmd(_Device(), "5分钟后关机", ctrl_panel=True)
    assert got == ("stop_after_minute", "5"), got
    print("arg_before_keyword OK", got)


def test_active_cmd_blocks_voice_when_idle():
    cfg = Config()
    cfg.active_cmd = "play"
    cfg.init()
    got = _handler(cfg).match_cmd(_Device(playing=False), "下一首", ctrl_panel=False)
    assert got == (None, None), got
    print("active_cmd_blocks_voice_when_idle OK", got)


def test_active_cmd_allows_voice_when_playing():
    cfg = Config()
    cfg.active_cmd = "play"
    cfg.init()
    got = _handler(cfg).match_cmd(_Device(playing=True), "下一首", ctrl_panel=False)
    assert got == ("play_next", ""), got
    print("active_cmd_allows_voice_when_playing OK", got)


def test_ctrl_panel_bypasses_active_cmd():
    cfg = Config()
    cfg.active_cmd = "play"
    cfg.init()
    got = _handler(cfg).match_cmd(_Device(playing=False), "下一首", ctrl_panel=True)
    assert got == ("play_next", ""), got
    print("ctrl_panel_bypasses_active_cmd OK", got)


def test_exec_custom_command():
    cfg = Config()
    cfg.key_word_dict["测试指令"] = 'exec#code1("hi")'
    cfg.key_match_order.append("测试指令")
    got = _handler(cfg).match_cmd(_Device(), "测试指令", ctrl_panel=True)
    assert got == ("exec", 'code1("hi")'), got
    print("exec_custom_command OK", got)


def test_pending_selection():
    got = _handler().match_cmd(_Device(pending=["a", "b"]), "第二个", ctrl_panel=True)
    assert got == ("select_index", "第二个"), got
    print("pending_selection OK", got)


def test_no_match():
    got = _handler().match_cmd(_Device(), "今天天气不错", ctrl_panel=True)
    assert got == (None, None), got
    print("no_match OK", got)


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except AssertionError as e:
                failed += 1
                print(f"FAIL {name}: {e}")
    if failed:
        print(f"\n{failed} test(s) FAILED")
        sys.exit(1)
    print("\nALL PASS")
