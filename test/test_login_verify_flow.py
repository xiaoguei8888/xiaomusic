"""登录验证流程的回归测试（后端）。

背景（用户可见缺陷）：
/api/login/start 只在 micoapi 失败时才发起二次验证。当 micoapi 已 ok、
xiaomiio 处于 needs_verification 时（真实用户的当前状态），没有任何代码
路径能发起 xiaomiio 验证，于是设备列表永远 401 → 设置页「没找到小爱音箱」，
而用户找不到任何可以点的验证入口。
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xiaomusic.api.routers import login as login_router  # noqa: E402

pytestmark = pytest.mark.asyncio


class FakeFlow:
    """记录 ensure_sid / open_verification 的调用，便于断言分支走向。"""

    def __init__(self, sids_after=None, open_result=None):
        self.ensure_calls = []
        self.open_calls = []
        self._sids = sids_after or {"micoapi": "ok", "xiaomiio": "ok"}
        self._open_result = open_result or {
            "state": "pending",
            "sid": "xiaomiio",
            "method": "Phone",
        }

    async def ensure_sid(self, sid):
        self.ensure_calls.append(sid)
        return self._sids.get(sid, "error")

    async def open_verification(self, sid):
        self.open_calls.append(sid)
        res = dict(self._open_result)
        res["sid"] = sid
        return res

    def submit_code(self, sid, code):
        return True

    def status(self):
        return {"state": "degraded", "sids": dict(self._sids)}


def make_recorder(calls):
    """构造一个可 await 的记录器。"""

    async def _rec(a):
        calls.append("update")

    return _rec


def make_xiaomusic(flow, bound=None):
    auth = SimpleNamespace(
        _ensure_flow=lambda: flow,
        _bind_services=lambda: (bound or []).append("bound"),
        config=SimpleNamespace(account="acc", password="pw", devices={}),
        device_manager=SimpleNamespace(devices={}, update_device_info=None),
    )
    return SimpleNamespace(auth_manager=auth)


async def test_login_start_opens_verification_for_xiaomiio():
    """核心回归：micoapi ok 但 xiaomiio 需验证时，必须为 xiaomiio 发起验证。"""
    flow = FakeFlow(sids_after={"micoapi": "ok", "xiaomiio": "needs_verification"})
    xm = make_xiaomusic(flow)

    res = await login_router.login_start({}, xm)

    assert "xiaomiio" in flow.ensure_calls
    assert flow.open_calls == ["xiaomiio"], "必须为 xiaomiio 发起验证"
    assert res["state"] == "pending"
    assert res["sid"] == "xiaomiio"


async def test_login_start_skips_verification_when_both_ok():
    """两个 sid 都正常时不应发短信。"""
    flow = FakeFlow(sids_after={"micoapi": "ok", "xiaomiio": "ok"})
    xm = make_xiaomusic(flow)

    res = await login_router.login_start({}, xm)

    assert flow.open_calls == [], "无需验证时绝不请求短信"
    assert res["success"] is True


async def test_login_start_verifies_micoapi_when_it_fails():
    """micoapi 未通过时，验证目标是 micoapi（原有行为不能破坏）。"""
    flow = FakeFlow(sids_after={"micoapi": "needs_verification", "xiaomiio": "ok"})
    xm = make_xiaomusic(flow)

    res = await login_router.login_start({}, xm)

    assert flow.open_calls == ["micoapi"]
    assert res["sid"] == "micoapi"


async def test_login_start_rejects_missing_credentials():
    flow = FakeFlow()
    auth = SimpleNamespace(
        _ensure_flow=lambda: flow,
        config=SimpleNamespace(account="", password="", devices={}),
        device_manager=SimpleNamespace(devices={}),
    )
    xm = SimpleNamespace(auth_manager=auth)

    res = await login_router.login_start({}, xm)

    assert res["success"] is False
    assert flow.open_calls == []


async def test_verify_open_endpoint_sends_sms():
    flow = FakeFlow()
    xm = make_xiaomusic(flow)

    res = await login_router.login_verify_open({"sid": "xiaomiio"}, xm)

    assert flow.open_calls == ["xiaomiio"]
    assert res["success"] is True
    assert res["state"] == "pending"
    assert "验证码已发送" in res["message"]


async def test_verify_open_rejects_unknown_sid():
    flow = FakeFlow()
    xm = make_xiaomusic(flow)

    res = await login_router.login_verify_open({"sid": "evil"}, xm)

    assert res["success"] is False
    assert flow.open_calls == [], "未知 sid 不应触发任何登录请求"


async def test_verify_open_reports_cooldown():
    flow = FakeFlow(open_result={"state": "cooldown", "sid": "xiaomiio"})
    xm = make_xiaomusic(flow)

    res = await login_router.login_verify_open({"sid": "xiaomiio"}, xm)

    assert res["success"] is False
    assert "频繁" in res["message"]


async def test_verify_check_refreshes_devices_after_xiaomiio_ok():
    """xiaomiio 验证成功后必须刷新设备列表，否则依旧是「没找到小爱音箱」。"""
    flow = FakeFlow(sids_after={"micoapi": "ok", "xiaomiio": "ok"})
    calls = []
    auth = SimpleNamespace(
        _ensure_flow=lambda: flow,
        _bind_services=lambda: calls.append("bind"),
        config=SimpleNamespace(account="acc", password="pw", devices={}),
        device_manager=SimpleNamespace(
            devices={}, update_device_info=make_recorder(calls)
        ),
    )
    xm = SimpleNamespace(auth_manager=auth)

    res = await login_router.login_verify_check(xm)

    assert calls == ["bind", "update"]
    assert res["sids"]["xiaomiio"] == "ok"
