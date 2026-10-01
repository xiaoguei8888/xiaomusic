"""登录状态语义的回归测试。

背景（用户可见缺陷）：
设置页「账号登录」显示「✅ 已登录（1 个设备）」，而同一页的「设备选择」
显示「没找到小爱音箱」——两处自相矛盾。

根因：logged_in 被定义为 bool(config.devices)，即「配置文件里填过 did」，
与小米账号的真实可用性无关。账号 xiaomiio sid 未验证时云端一律 401，
设备列表为空，但 logged_in 仍为 True。

正确语义：logged_in 由 micoapi sid 决定；设备列表是否可用由 xiaomiio 决定。
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xiaomusic.api.routers import login as login_router  # noqa: E402

pytestmark = pytest.mark.asyncio


class FakeFlow:
    def __init__(self, sids):
        self._sids = sids

    def status(self):
        mico = self._sids.get("micoapi")
        xio = self._sids.get("xiaomiio")
        if mico == "ok":
            overall = "authenticated" if xio == "ok" else "degraded"
        elif "needs_verification" in (mico, xio):
            overall = "needs_verification"
        else:
            overall = "unauthenticated"
        return {"state": overall, "sids": dict(self._sids), "account": "acc"}


def make_xiaomusic(sids, configured=None, live=None):
    auth = SimpleNamespace(
        _ensure_flow=lambda: FakeFlow(sids),
        config=SimpleNamespace(devices=configured or {}),
        device_manager=SimpleNamespace(devices=live or {}),
    )
    return SimpleNamespace(auth_manager=auth)


async def test_logged_in_true_only_when_micoapi_ok():
    xm = make_xiaomusic({"micoapi": "ok", "xiaomiio": "ok"})
    res = await login_router.login_status(xm)

    assert res["logged_in"] is True
    assert res["device_list_available"] is True


async def test_logged_in_not_implied_by_configured_devices():
    """核心回归：配置里有设备 ≠ 已登录。

    旧实现返回 logged_in=True，正是用户看到「已登录」却「没找到音箱」的原因。
    """
    xm = make_xiaomusic(
        {"micoapi": "needs_verification", "xiaomiio": "needs_verification"},
        configured={"did-1": object()},
    )
    res = await login_router.login_status(xm)

    assert res["logged_in"] is False, "未验证的账号不能报告为已登录"
    assert res["device_list_available"] is False
    assert res["state"] == "needs_verification"


async def test_degraded_account_reports_devices_unavailable():
    """micoapi 正常但 xiaomiio 未验证：已登录，但设备列表不可用。"""
    xm = make_xiaomusic(
        {"micoapi": "ok", "xiaomiio": "needs_verification"},
        configured={"did-1": object()},
    )
    res = await login_router.login_status(xm)

    assert res["logged_in"] is True
    assert res["device_list_available"] is False, "设备列表此时拉不到，必须明示"
    assert res["state"] == "degraded"


async def test_device_counts_distinguish_configured_from_live():
    """配置设备数与云端真实设备数分开暴露，不再混为一个数字。"""
    xm = make_xiaomusic(
        {"micoapi": "ok", "xiaomiio": "ok"},
        configured={"did-1": object()},
        live={"did-1": object(), "did-2": object()},
    )
    res = await login_router.login_status(xm)

    assert res["configured_device_count"] == 1
    assert res["device_count"] == 2


async def test_device_count_falls_back_to_configured_when_cloud_empty():
    """云端拉不到设备时，退化为配置里的数量，而不是显示 0 个设备。"""
    xm = make_xiaomusic(
        {"micoapi": "ok", "xiaomiio": "needs_verification"},
        configured={"did-1": object()},
        live={},
    )
    res = await login_router.login_status(xm)

    assert res["device_count"] == 1
    assert res["device_list_available"] is False
