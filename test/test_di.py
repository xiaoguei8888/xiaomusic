"""校验 DI：get_xiaomusic 依赖与 app.state 单一状态源。

运行：  .venv/bin/python test/test_di.py
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xiaomusic.api import dependencies as deps  # noqa: E402
from xiaomusic.api.app import app  # noqa: E402


def _fake_request(state_attrs):
    state = types.SimpleNamespace(**state_attrs)
    return types.SimpleNamespace(app=types.SimpleNamespace(state=state))


def test_get_xiaomusic_returns_instance():
    got = deps.get_xiaomusic(_fake_request({"xiaomusic": "XM"}))
    assert got == "XM", got
    print("get_xiaomusic_returns_instance OK")


def test_get_xiaomusic_raises_when_uninitialized():
    try:
        deps.get_xiaomusic(_fake_request({}))
    except RuntimeError:
        print("get_xiaomusic_raises_when_uninitialized OK")
        return
    raise AssertionError("expected RuntimeError")


def test_initialize_state_sets_app_state():
    fake = types.SimpleNamespace(config="C", log="L")
    deps.initialize_state(fake)
    assert app.state.xiaomusic is fake
    assert app.state.config == "C"
    assert app.state.log == "L"
    assert deps.is_initialized() is True
    print("initialize_state_sets_app_state OK")


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
