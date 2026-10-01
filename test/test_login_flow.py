"""校验 LoginFlow 登录流程不变量：自动路径绝不请求 OTP/SMS。

运行：  .venv/bin/python test/test_login_flow.py
"""

import asyncio
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xiaomusic import login_flow  # noqa: E402
from xiaomusic.auth_state import STATUS_NEEDS_VERIFICATION, AuthState  # noqa: E402

SID = "xiaomiio"
OTP_MSG = "OTP verification required but no otp_callback provided."
_UNSET = object()


class _Log:
    def __init__(self):
        self.lines = []

    def _record(self, level, msg):
        self.lines.append((level, str(msg)))

    def warning(self, msg):
        self._record("warning", msg)

    def info(self, msg):
        self._record("info", msg)

    def debug(self, msg):
        self._record("debug", msg)


class _FakeMiAccount:
    instances = []
    login_calls = []
    raise_exc = None
    result = True
    login_error = ""

    def __init__(
        self, session, username, password, token_store=None, otp_callback=_UNSET
    ):
        self.session = session
        self.username = username
        self.password = password
        self.token_store = token_store
        self.otp_callback = otp_callback
        self.token = {"deviceId": "TESTDEV"}
        self._login_error = ""
        _FakeMiAccount.instances.append(self)

    async def login(self, sid):
        _FakeMiAccount.login_calls.append(sid)
        if _FakeMiAccount.raise_exc is not None:
            raise _FakeMiAccount.raise_exc
        if not _FakeMiAccount.result:
            self._login_error = _FakeMiAccount.login_error
        return _FakeMiAccount.result


class _PatchMiAccount:
    def __enter__(self):
        self._orig = login_flow.MiAccount
        _FakeMiAccount.instances = []
        _FakeMiAccount.login_calls = []
        _FakeMiAccount.raise_exc = None
        _FakeMiAccount.result = True
        _FakeMiAccount.login_error = ""
        login_flow.MiAccount = _FakeMiAccount
        return _FakeMiAccount

    def __exit__(self, *exc):
        login_flow.MiAccount = self._orig
        return False


def _make_flow():
    conf_dir = tempfile.mkdtemp(prefix="xm-login-flow-")
    state = AuthState(os.path.join(conf_dir, "auth.json"), _Log())
    flow = login_flow.LoginFlow(state, "user", "pass", None, _Log())
    return flow, state, conf_dir


def _ensure_sid(flow, sid=SID):
    return asyncio.run(flow.ensure_sid(sid))


def test_default_new_account_has_no_otp_callback():
    flow, _state, conf_dir = _make_flow()
    try:
        with _PatchMiAccount():
            acct = flow._new_account()
            assert acct.otp_callback is None, acct.otp_callback
        print("default_new_account_has_no_otp_callback OK")
    finally:
        shutil.rmtree(conf_dir)


def test_allow_otp_new_account_wires_callback():
    flow, _state, conf_dir = _make_flow()
    try:
        with _PatchMiAccount():
            acct = flow._new_account(allow_otp=True)
            assert acct.otp_callback == flow.otp.wait_code, acct.otp_callback
        print("allow_otp_new_account_wires_callback OK")
    finally:
        shutil.rmtree(conf_dir)


def test_needs_verification_state_is_terminal_no_login():
    flow, state, conf_dir = _make_flow()
    try:
        state.set_sid_needs_verification(SID, "")
        with _PatchMiAccount():
            result = _ensure_sid(flow)
            assert result == STATUS_NEEDS_VERIFICATION, result
            assert _FakeMiAccount.login_calls == [], _FakeMiAccount.login_calls
        print("needs_verification_state_is_terminal_no_login OK")
    finally:
        shutil.rmtree(conf_dir)


def test_otp_exception_sets_terminal_state():
    flow, state, conf_dir = _make_flow()
    try:
        state.set_sid_error(SID, "prev failure")
        with _PatchMiAccount() as patch:
            patch.raise_exc = Exception(OTP_MSG)
            result = _ensure_sid(flow)
        assert result == STATUS_NEEDS_VERIFICATION, result
        assert state.sid_status(SID) == STATUS_NEEDS_VERIFICATION
        print("otp_exception_sets_terminal_state OK")
    finally:
        shutil.rmtree(conf_dir)


def test_login_false_with_otp_error_sets_terminal_state():
    flow, state, conf_dir = _make_flow()
    try:
        state.set_sid_error(SID, "prev failure")
        with _PatchMiAccount() as patch:
            patch.result = False
            patch.login_error = OTP_MSG
            result = _ensure_sid(flow)
        assert result == STATUS_NEEDS_VERIFICATION, result
        reloaded = AuthState(os.path.join(conf_dir, "auth.json"), _Log())
        reloaded.load()
        assert reloaded.sid_status(SID) == STATUS_NEEDS_VERIFICATION
        print("login_false_with_otp_error_sets_terminal_state OK")
    finally:
        shutil.rmtree(conf_dir)


def test_open_verification_keeps_otp_callback():
    flow, _state, conf_dir = _make_flow()
    try:
        with _PatchMiAccount():
            asyncio.run(flow.open_verification(SID))
            assert _FakeMiAccount.instances, "open_verification must create an account"
            acct = _FakeMiAccount.instances[0]
            assert acct.otp_callback == flow.otp.wait_code, acct.otp_callback
        print("open_verification_keeps_otp_callback OK")
    finally:
        shutil.rmtree(conf_dir)


# ---------------------------------------------------------------------------
# 验证失败 / 重发路径的回归（用户可见缺陷：提交后一直「等待验证结果」）
# ---------------------------------------------------------------------------
class _OtpMiAccount:
    """login() 真的走到 otp_callback：先等码，再按 otp_ok 决定成败。

    对应真实链路：miservice 发短信 → 挂起等前端 submit_code → 校验验证码。
    """

    instances = []
    codes = []
    no_otp = False  # True 时模拟自动路径：不传回调，直接失败
    otp_ok = True

    def __init__(
        self, session, username, password, token_store=None, otp_callback=_UNSET
    ):
        self.otp_callback = otp_callback
        self.token = {"deviceId": "TESTDEV"}
        self._login_error = ""
        _OtpMiAccount.instances.append(self)

    async def login(self, sid):
        if _OtpMiAccount.no_otp:
            self._login_error = OTP_MSG
            return False
        code = await self.otp_callback("Phone")
        _OtpMiAccount.codes.append(code)
        if not code:
            self._login_error = "No OTP code provided"
            return False
        if not _OtpMiAccount.otp_ok:
            self._login_error = "OTP verification failed, no location in response: {}"
            return False
        self.token[sid] = ("ssec", "stoken")
        return True


class _PatchOtpMiAccount:
    def __enter__(self):
        self._orig = login_flow.MiAccount
        _OtpMiAccount.instances = []
        _OtpMiAccount.codes = []
        _OtpMiAccount.no_otp = False
        _OtpMiAccount.otp_ok = True
        login_flow.MiAccount = _OtpMiAccount
        return _OtpMiAccount

    def __exit__(self, *exc):
        login_flow.MiAccount = self._orig
        return False


async def _open_then_cancel(flow, sid=SID):
    """跑一次 open_verification；结束时收掉仍在等码的任务，避免悬挂。"""
    res = await flow.open_verification(sid)
    task = getattr(flow, "_verify_task", None)
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except BaseException:  # noqa: BLE001 - 取消即可
            pass
    return res


async def _submit_and_wait(flow, code="000000", sid=SID):
    res = await flow.open_verification(sid)
    assert res["state"] == "pending", res
    assert flow.submit_code(sid, code) is True, "提交的验证码必须被 OTP 桥接接受"
    for _ in range(200):
        if flow.status()["verify"]["state"] != "pending":
            break
        await asyncio.sleep(0.01)
    return flow.status()


def test_resend_after_finished_attempt_reports_pending_again():
    """第一轮验证结束后，「重新发送」必须真的能再发一次。

    缺陷：_verify_done 在 __init__ 里创建、只 set 不 clear，第二次
    open_verification 的探测循环第 0 圈就判定「已结束」，直接返回 failed，
    于是用户点「重新发送」永远只得到「验证发起失败」，没有任何重试入口。
    """
    flow, _state, conf_dir = _make_flow()
    try:
        with _PatchOtpMiAccount() as patch:
            patch.no_otp = True  # 第一轮：失败收场
            first = asyncio.run(flow.open_verification(SID))
            assert first["state"] == "failed", first
            patch.no_otp = False  # 第二轮：正常等码
            second = asyncio.run(_open_then_cancel(flow))
        assert second["state"] == "pending", (
            f"重新发送没有重新等待验证码，返回 {second!r}"
        )
        print("resend_after_finished_attempt_reports_pending_again OK")
    finally:
        shutil.rmtree(conf_dir)


def test_failed_verification_is_exposed_to_frontend():
    """验证码被拒时必须把终局失败暴露给前端。

    缺陷：_run() 只在 auth.json 里写 error，status() 不返回任何东西；
    前端只能靠 sids[sid]==ok 判断成功，于是失败=永远「等待验证结果」。
    """
    flow, _state, conf_dir = _make_flow()
    try:
        with _PatchOtpMiAccount() as patch:
            patch.otp_ok = False
            status = asyncio.run(_submit_and_wait(flow))
        assert status["verify"]["sid"] == SID, status["verify"]
        assert status["verify"]["state"] == "failed", status["verify"]
        assert status["verify"]["message"], "失败必须带人话文案，前端直接展示"
        print("failed_verification_is_exposed_to_frontend OK")
    finally:
        shutil.rmtree(conf_dir)


def test_successful_verification_reports_ok():
    """成功路径不能被上面的改动破坏：sid 变 ok，verify 也报 ok。"""
    flow, _state, conf_dir = _make_flow()
    try:
        with _PatchOtpMiAccount():
            status = asyncio.run(_submit_and_wait(flow, code="123456"))
        assert status["sids"][SID] == "ok", status["sids"]
        assert status["verify"]["state"] == "ok", status["verify"]
        print("successful_verification_reports_ok OK")
    finally:
        shutil.rmtree(conf_dir)


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
