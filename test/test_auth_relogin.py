"""认证重试路径的回归测试。

背景（真机缺陷）：patched_mi_request 在 mi_request 失败时想清 cookie 后重试，
但写成了 mi_account.session —— MiAccount 内部属性其实是 _session，
于是恢复逻辑本身抛 AttributeError，把「可恢复的登录失败」变成永久失败。

后果：等一次 token 过期后，player_pause / player_stop / player_get_status
全部报 'MiAccount' object has no attribute 'session'，
用户看到的现象就是「暂停不管用」。
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xiaomusic.auth import AuthManager  # noqa: E402

pytestmark = pytest.mark.asyncio


class FakeCookieJar:
    def __init__(self):
        self.cleared = 0

    def clear(self):
        self.cleared += 1


class FakeSession:
    def __init__(self):
        self.cookie_jar = FakeCookieJar()


class FakeAccount:
    """模拟 miservice.MiAccount：只有 _session，没有 session。"""

    def __init__(self, fail_times=1):
        self._session = FakeSession()
        self.token = {}
        self.calls = 0
        self._fail_times = fail_times

    async def mi_request(self, sid, url, data, headers, relogin=True):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise RuntimeError("token expired")
        return {"code": 0, "ok": True}


class FakeState:
    def __init__(self):
        self.loaded = 0

    def load(self):
        self.loaded += 1

    def to_miservice_token(self):
        return {"userId": "u1"}


def make_manager():
    log = SimpleNamespace(
        warning=lambda *a, **k: None,
        info=lambda *a, **k: None,
        debug=lambda *a, **k: None,
        error=lambda *a, **k: None,
    )
    mgr = AuthManager.__new__(AuthManager)
    mgr.log = log
    mgr.device_id = "did-1"
    mgr._state = FakeState()
    return mgr


async def test_patched_mi_request_recovers_from_expired_token():
    """核心回归：失败后必须能恢复重试，而不是抛 AttributeError。"""
    mgr = make_manager()
    acct = FakeAccount(fail_times=1)
    mgr._patch_account(acct)

    result = await acct.mi_request("sid", "http://x", {}, {})

    assert result == {"code": 0, "ok": True}
    assert acct.calls == 2, "应当重试一次"
    assert acct._session.cookie_jar.cleared == 1, "重试前应清理 cookie"
    assert mgr._state.loaded == 1
    assert acct.token["userId"] == "u1"
    assert acct.token["deviceId"] == "did-1", "缺少 deviceId 时应补上"


async def test_patched_mi_request_does_not_crash_without_session_attr():
    """即使拿不到 session 也不能崩，恢复流程要继续走完。"""
    mgr = make_manager()
    acct = FakeAccount(fail_times=1)
    acct._session = None  # 极端情况：没有可用 session
    mgr._patch_account(acct)

    result = await acct.mi_request("sid", "http://x", {}, {})

    assert result == {"code": 0, "ok": True}
    assert acct.calls == 2


async def test_patched_mi_request_reraises_when_relogin_disabled():
    """relogin=False 时必须原样抛出，不能吞掉异常。"""
    mgr = make_manager()
    acct = FakeAccount(fail_times=99)
    mgr._patch_account(acct)

    with pytest.raises(RuntimeError):
        await acct.mi_request("sid", "http://x", {}, {}, relogin=False)

    assert acct.calls == 1
