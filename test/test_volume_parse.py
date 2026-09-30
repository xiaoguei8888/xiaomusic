"""校验播放状态/音量解析：顶层优先、info 兜底。

运行：  .venv/bin/python test/test_volume_parse.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xiaomusic.device_player import _extract_volume, _parse_player_status  # noqa: E402


def test_top_level_volume_is_used():
    """回归：真实响应把 volume 放在顶层，旧实现只读 data.info 会得到 0。"""
    raw = {"status": 1, "volume": 25, "loop_type": 1}
    assert _extract_volume(raw) == 25
    assert _parse_player_status(raw)["volume"] == 25
    print("top_level_volume_is_used OK")


def test_nested_info_is_fallback():
    raw = {
        "status": 0,
        "data": {"info": json.dumps({"volume": 40, "status": 1, "loop_type": 2})},
    }
    assert _extract_volume(raw) == 40
    print("nested_info_is_fallback OK")


def test_top_level_wins_over_nested():
    raw = {
        "volume": 7,
        "data": {"info": json.dumps({"volume": 99})},
    }
    assert _extract_volume(raw) == 7, "顶层应优先"
    print("top_level_wins_over_nested OK")


def test_bad_payload_is_safe():
    for bad in (None, {}, [], "x", {"data": {"info": "not json"}}, {"volume": "abc"}):
        assert _extract_volume(bad) == 0, bad
        assert isinstance(_parse_player_status(bad), dict)
    print("bad_payload_is_safe OK")


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
