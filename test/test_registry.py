"""校验命令注册表与口令表、XiaoMusic 方法三者保持一致。

运行：  .venv/bin/python test/test_registry.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xiaomusic import commands  # noqa: E402
from xiaomusic.config import Config  # noqa: E402
from xiaomusic.xiaomusic import XiaoMusic  # noqa: E402


def test_every_keyword_maps_to_registered_command():
    cfg = Config()
    missing = sorted(
        {v for v in cfg.key_word_dict.values() if v not in commands.COMMAND_NAMES}
    )
    assert not missing, f"口令表引用了未登记命令: {missing}"
    print("every_keyword_maps_to_registered_command OK", len(cfg.key_word_dict), "phrases")


def test_every_registered_command_exists_on_xiaomusic():
    missing = sorted(
        n for n in commands.COMMAND_NAMES if not callable(getattr(XiaoMusic, n, None))
    )
    assert not missing, f"注册表引用了 XiaoMusic 上不存在的方法: {missing}"
    print("every_registered_command_exists_on_xiaomusic OK", len(commands.COMMAND_NAMES), "commands")


def test_special_match_cmd_outputs_registered():
    for name in ("select_index",):
        assert commands.is_command(name), name
    print("special_match_cmd_outputs_registered OK")


def test_resolve_known_and_unknown():
    class Dummy:
        def play(self):
            return "ok"

    assert commands.resolve(Dummy(), "play") is not None
    assert commands.resolve(Dummy(), "no_such_cmd") is None
    print("resolve_known_and_unknown OK")


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
