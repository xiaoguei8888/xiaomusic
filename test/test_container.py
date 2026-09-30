"""容器契约测试：注册/解析/循环依赖/模块装配。

运行：  .venv/bin/python test/test_container.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xiaomusic.bootstrap import Application, Container, ContainerError, Module  # noqa: E402


def test_register_and_resolve_singleton():
    c = Container()
    calls = []

    def factory(container):
        calls.append(1)
        return {"n": len(calls)}

    c.register("svc", factory)
    a = c.resolve("svc")
    b = c.resolve("svc")
    assert a is b, "同名单例应复用实例"
    assert len(calls) == 1, calls
    print("register_and_resolve_singleton OK")


def test_resolve_missing_raises():
    c = Container()
    try:
        c.resolve("nope")
    except ContainerError as e:
        assert "未注册" in str(e)
        print("resolve_missing_raises OK")
        return
    raise AssertionError("expected ContainerError")


def test_duplicate_registration_rejected():
    c = Container()
    c.register_instance("svc", 1)
    try:
        c.register("svc", lambda _: 2)
    except ContainerError:
        print("duplicate_registration_rejected OK")
        return
    raise AssertionError("expected ContainerError")


def test_cycle_detection():
    c = Container()
    c.register("a", lambda cc: cc.resolve("b"))
    c.register("b", lambda cc: cc.resolve("a"))
    try:
        c.resolve("a")
    except ContainerError as e:
        assert "循环依赖" in str(e), e
        print("cycle_detection OK")
        return
    raise AssertionError("expected ContainerError")


def test_optional_resolve():
    c = Container()
    assert c.resolve_optional("missing", "dflt") == "dflt"
    c.register_instance("hit", "v")
    assert c.resolve_optional("hit") == "v"
    print("optional_resolve OK")


class _RecordingModule(Module):
    name = "rec"

    def __init__(self, service_name="rec_value"):
        self.events = []
        self.service_name = service_name

    def register(self, container):
        container.register_instance(self.service_name, 42)

    def routes(self):
        return ["router"]

    async def start(self, container):
        self.events.append("start")

    async def stop(self, container):
        self.events.append("stop")


def test_application_wires_modules():
    mod = _RecordingModule()
    app = Application()
    app.add(mod)
    assert app.container.resolve("rec_value") == 42
    assert app.routers() == ["router"]

    async def run():
        await app.start()
        await app.stop()

    asyncio.run(run())
    assert mod.events == ["start", "stop"], mod.events
    print("application_wires_modules OK")


def test_removing_module_removes_capability():
    """移除模块 = 从装配列表删一行，其服务与路由随之消失。"""
    m1 = _RecordingModule("svc_a")
    m2 = _RecordingModule("svc_b")
    full = Application().add(m1).add(m2)
    assert len(full.routers()) == 2
    assert full.container.has("svc_a") and full.container.has("svc_b")

    # 只装配 m1：m2 的路由与 m2 的服务都不存在
    slim = Application().add(m1)
    assert len(slim.routers()) == 1
    assert slim.container.has("svc_a")
    assert not slim.container.has("svc_b")
    print("removing_module_removes_capability OK")


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
