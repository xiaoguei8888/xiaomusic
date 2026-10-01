"""登录状态机与逐 sid 认证。

三个入口（账号密码 / 扫码 / 验证码）统一只负责产出账号级 passToken，
之后由本模块按 sid 独立换取 serviceToken；遇到二次验证时通过
OTP 回调把短信码交回给调用方，实现页面内嵌验证。

关键不变量：
- 任一 sid 失败绝不影响其它 sid 已拿到的 token。
- 只有用户显式发起的验证（open_verification）才允许传 otp_callback 触发短信；
  自动路径（ensure_sid）一律 otp_callback=None，绝不请求 OTP/SMS。
- 需要验证 / 被限流时进入终态，不做自动重试，避免触发风控。
"""

import asyncio

from miservice import MiAccount

from .auth_state import (
    SID_MICOAPI,
    SID_XIAOMIIO,
    STATUS_ERROR,
    STATUS_NEEDS_VERIFICATION,
    STATUS_OK,
    AuthState,
    AuthTokenStore,
)

LOGIN_TIMEOUT_SEC = 60
VERIFY_WAIT_SEC = 300
RATE_LIMIT_COOLDOWN_SEC = 180


def _err_text(acct) -> str:
    return str(getattr(acct, "_login_error", "") or "login returned False")[:200]


def _verify_failure_text(err: str) -> str:
    """把 miservice 的异常原文收敛成用户能照着做的一句话。

    原始错误形如 "OTP verification failed, no location in response: {…}"，
    直接展示给用户没有任何可操作性。
    """
    if "No OTP code provided" in err:
        return "验证码等待超时，请重新发送验证码"
    if "OTP verification failed" in err:
        return "验证码错误或已过期，请重新发送验证码"
    if "OTP" in err:
        return "验证码发送失败，请稍后重试"
    return "验证失败，请重试"


class _OTPBridge:
    """miservice 的 otp_callback 实现：等待前端提交短信码。"""

    def __init__(self, log):
        self.log = log
        self._future: asyncio.Future | None = None
        self.method = ""

    def reset(self) -> None:
        self._future = None
        self.method = ""

    async def wait_code(self, method: str) -> str:
        self.method = method
        loop = asyncio.get_running_loop()
        self._future = loop.create_future()
        try:
            return await asyncio.wait_for(self._future, timeout=VERIFY_WAIT_SEC)
        except asyncio.TimeoutError:
            return ""
        finally:
            self._future = None

    def submit(self, code: str) -> bool:
        if self._future and not self._future.done():
            self._future.set_result(code)
            return True
        return False


class LoginFlow:
    def __init__(self, state: AuthState, account: str, password: str, session, log):
        self.state = state
        self.account = account
        self.password = password
        self.session = session
        self.log = log
        self.otp = _OTPBridge(log)
        self._lock = asyncio.Lock()
        self._verify_sid: str | None = None
        self._verify_done = asyncio.Event()
        # 每轮验证带一个代号：被新轮次取代的旧任务不允许再写任何结果，
        # 否则它超时后会把自己的失败覆盖到新一轮已经成功的结果上。
        self._verify_gen = 0
        self._verify_task: asyncio.Task | None = None
        # 暴露给前端的终局状态：idle / pending / ok / failed。
        # 前端只能靠 sids[sid]=="ok" 判断成功，失败与否必须显式告知，
        # 否则「提交后一直等待」没有任何出口。
        self._verify_state: dict = {"sid": "", "state": "idle", "message": ""}

    def _new_account(self, allow_otp: bool = False) -> MiAccount:
        store = AuthTokenStore(self.state, self.log)
        acct = MiAccount(
            self.session,
            self.account,
            self.password,
            store,
            otp_callback=self.otp.wait_code if allow_otp else None,
        )
        device_id = self.state.data.get("deviceId") or ""
        if device_id:
            acct.token = {"deviceId": device_id}
        return acct

    async def ensure_sid(self, sid: str) -> str:
        """返回 sid 的最终状态字符串。"""
        self.state.load()
        if self.state.cooldown_active():
            return "cooldown"
        if self.state.sid_status(sid) == STATUS_OK:
            flat = self.state.to_miservice_token()
            if flat.get(sid):
                return STATUS_OK
        if self.state.sid_status(sid) == STATUS_NEEDS_VERIFICATION:
            # 终态：自动路径不重试、不碰网络，避免再次请求 OTP/SMS。
            return STATUS_NEEDS_VERIFICATION

        async with self._lock:
            acct = self._new_account()
            try:
                ok = await asyncio.wait_for(acct.login(sid), timeout=LOGIN_TIMEOUT_SEC)
            except asyncio.TimeoutError:
                self.state.set_sid_error(sid, "timeout")
                return STATUS_ERROR
            except Exception as e:
                msg = str(e)
                self.log.warning(f"[LOGIN] {sid} 登录异常: {msg[:200]}")
                if self._looks_like_rate_limit(msg):
                    self.state.set_cooldown(RATE_LIMIT_COOLDOWN_SEC)
                    return "cooldown"
                if self._looks_like_needs_verification(msg):
                    self.state.set_sid_needs_verification(sid, "")
                    return STATUS_NEEDS_VERIFICATION
                self.state.set_sid_error(sid, msg[:200])
                return STATUS_ERROR

            if ok:
                flat = acct.token or {}
                self.state.sync_from_miservice_token(flat)
                return STATUS_OK

            # miservice 的 login() 内部吞掉异常并返回 False，
            # 未传 otp_callback 时 _login_error 即 OTP 必需提示，自动路径在此收敛为终态。
            err = _err_text(acct)
            if self._looks_like_needs_verification(err):
                self.state.set_sid_needs_verification(sid, "")
                self.log.warning(
                    f"[LOGIN] {sid} 需要二次验证，自动登录不发送短信，进入终态"
                )
                return STATUS_NEEDS_VERIFICATION
            self.state.set_sid_error(sid, err)
            return STATUS_ERROR

    def _looks_like_rate_limit(self, msg: str) -> bool:
        return "20024" in msg or "70022" in msg or "用户行为被限制" in msg

    def _looks_like_needs_verification(self, msg: str) -> bool:
        return "OTP verification required" in msg

    def _set_verify_outcome(self, gen: int, sid: str, state: str, message: str) -> None:
        """记录本轮验证的终局；只有最新一轮有资格写。"""
        if gen != self._verify_gen:
            return
        self._verify_state = {"sid": sid, "state": state, "message": message}

    async def open_verification(self, sid: str) -> dict:
        if self.state.cooldown_active():
            return {"state": "cooldown", "sid": sid}
        # 上一轮可能还在等码（最长 VERIFY_WAIT_SEC）：必须先收掉。
        # 否则它会一直等到超时，然后把「验证失败」写进本轮已经成功的结果。
        if self._verify_task is not None and not self._verify_task.done():
            self._verify_task.cancel()
            self._verify_task = None
        self._verify_sid = sid
        self.otp.reset()
        # 每一轮都是全新的事件：Event 只 set 不 clear 的话，第二次
        # open_verification 第 0 圈就误判「已结束」直接返回 failed，
        # 「重新发送」将永远失败，用户没有任何重试入口。
        self._verify_done.clear()
        self._verify_gen += 1
        gen = self._verify_gen
        self._verify_state = {"sid": sid, "state": "pending", "message": ""}

        async def _run():
            acct = self._new_account(allow_otp=True)
            try:
                ok = await acct.login(sid)
                if ok:
                    self.state.sync_from_miservice_token(acct.token or {})
                    self._set_verify_outcome(gen, sid, "ok", "")
                else:
                    err = _err_text(acct)
                    self.state.set_sid_error(sid, err)
                    self.log.warning(f"[LOGIN] {sid} 验证失败: {err[:200]}")
                    self._set_verify_outcome(
                        gen, sid, "failed", _verify_failure_text(err)
                    )
            except asyncio.CancelledError:
                raise  # 已被新一轮取代：不写任何状态，交给新一轮
            except Exception as e:
                msg = str(e)
                if self._looks_like_rate_limit(msg):
                    self.state.set_cooldown(RATE_LIMIT_COOLDOWN_SEC)
                    self._set_verify_outcome(
                        gen, sid, "failed", "请求过于频繁，请稍后再试"
                    )
                else:
                    self.state.set_sid_error(sid, msg[:200])
                    self.log.warning(f"[LOGIN] {sid} 验证异常: {msg[:200]}")
                    self._set_verify_outcome(
                        gen, sid, "failed", _verify_failure_text(msg)
                    )
            finally:
                if gen == self._verify_gen:
                    self._verify_done.set()

        self._verify_task = asyncio.create_task(_run())
        for _ in range(80):
            if (
                self.otp.method
                or self._verify_done.is_set()
                or self.state.cooldown_active()
            ):
                break
            await asyncio.sleep(0.1)
        if self.state.cooldown_active():
            return {"state": "cooldown", "sid": sid}
        if self._verify_done.is_set():
            status = self.state.sid_status(sid)
            if status == STATUS_OK:
                return {"state": "ok", "sid": sid}
            return {
                "state": "failed",
                "sid": sid,
                "message": self._verify_state.get("message")
                or "验证发起失败，请稍后重试",
            }
        return {"state": "pending", "sid": sid, "method": self.otp.method or "Phone"}

    def submit_code(self, sid: str, code: str) -> bool:
        return self.otp.submit(code)

    def status(self) -> dict:
        self.state.load()
        sids = {sid: self.state.sid_status(sid) for sid in (SID_MICOAPI, SID_XIAOMIIO)}
        if sids.get(SID_MICOAPI) == STATUS_OK:
            overall = "authenticated"
            if sids.get(SID_XIAOMIIO) != STATUS_OK:
                overall = "degraded"
        elif (
            sids.get(SID_XIAOMIIO) == STATUS_NEEDS_VERIFICATION
            or sids.get(SID_MICOAPI) == STATUS_NEEDS_VERIFICATION
        ):
            overall = "needs_verification"
        elif self.state.cooldown_active():
            overall = "cooldown"
        else:
            overall = "unauthenticated"
        return {
            "state": overall,
            "account": self.state.data.get("account", ""),
            "sids": sids,
            "cooldownUntil": self.state.data.get("cooldownUntil", 0),
            # 本轮验证的终局。sids[sid] 会被自动登录路径（60s 周期）重新写成
            # needs_verification，靠它判断「刚刚那次提交是不是失败了」并不可靠。
            "verify": dict(self._verify_state),
        }
