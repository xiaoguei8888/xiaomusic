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
from xiaomusic.auth_state import AuthState, STATUS_NEEDS_VERIFICATION  # noqa: E402

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
