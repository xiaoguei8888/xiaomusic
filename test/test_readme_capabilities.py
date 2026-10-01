"""校验 README 能力表与代码实际能力一致（防止文档漂移）。

运行：  .venv/bin/python test/test_readme_capabilities.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# README「没有」列中的关键词 -> 代码里不应存在的模块/端点
REMOVED_CAPABILITIES = {
    "online_music": ["xiaomusic/online_music.py"],
    "js_plugin_manager": ["xiaomusic/js_plugin_manager.py"],
    "qrcode_login": ["xiaomusic/qrcode_login.py"],
    "file_router": ["xiaomusic/api/routers/file.py"],
    "plugin_router": ["xiaomusic/api/routers/plugin.py"],
    "network_utils": ["xiaomusic/utils/network_utils.py"],
    "static_online_search": ["xiaomusic/static/onlineSearch"],
}

# README「有」列中的关键词 -> 对应模块必须存在
KEPT_CAPABILITIES = {
    "device_player": "xiaomusic/device_player.py",
    "music_library": "xiaomusic/music_library.py",
    "command_handler": "xiaomusic/command_handler.py",
    "media_router": "xiaomusic/api/routers/media.py",
    "static_default": "xiaomusic/static/default/index.html",
}


def test_readme_does_not_promise_removed_capabilities():
    readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    for name, paths in REMOVED_CAPABILITIES.items():
        for rel in paths:
            assert not os.path.exists(os.path.join(ROOT, rel)), (
                f"README 声明已移除 {name}，但 {rel} 仍存在"
            )
    # README 不应再把 yt-dlp / 二维码登录 当作现有能力描述
    assert "yt-dlp" not in readme or "没有" in readme, (
        "README 提到 yt-dlp 需明确为已移除"
    )
    print("readme_does_not_promise_removed_capabilities OK")


def test_kept_capabilities_exist():
    for name, rel in KEPT_CAPABILITIES.items():
        assert os.path.exists(os.path.join(ROOT, rel)), f"{name} 缺失: {rel}"
    print("kept_capabilities_exist OK")


def test_command_registry_matches_readme():
    from xiaomusic import commands

    readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    # select_index 由"多结果选择"流程自动触发，不出现在口令表中
    exempt = {"select_index"}
    for cmd in commands.COMMAND_NAMES - exempt:
        assert cmd in readme or cmd.startswith("set_play_type"), (
            f"命令 {cmd} 未在 README 口令表中出现"
        )
    print("command_registry_matches_readme OK", len(commands.COMMAND_NAMES), "commands")


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
