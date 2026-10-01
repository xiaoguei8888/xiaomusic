"""认证管理模块

本模块负责小米账号认证与会话管理，包括：
- 小米账号登录
- Cookie管理
- 会话维护
- 设备ID更新
"""

import asyncio
import json
import os
import time

from aiohttp import ClientSession
from miservice import MiAccount, MiIOService, MiNAService

from xiaomusic.auth_state import STATUS_OK, AuthState, AuthTokenStore
from xiaomusic.config import Device
from xiaomusic.utils.system_utils import (
    get_random,
)

LOGIN_COOLDOWN_SEC = 30
INIT_LOCK_TIMEOUT_SEC = 60
TOKEN_REFRESH_INTERVAL_SEC = 12 * 3600


class AuthManager:
    """认证管理器"""

    def __init__(self, config, log, device_manager):
        self.config = config
        self.log = log
        self.mi_token_home = os.path.join(self.config.conf_path, ".mi.token")

        self._init_lock = asyncio.Lock()
        self._last_login_time = 0
        self._last_login_ok = False
        self._consecutive_failures = 0

        self.mina_service = None
        self.miio_service = None
        self.login_acount = None
        self.login_password = None
        self.cookie_jar = None

        self._cur_did = None
        self._state = AuthState(
            os.path.join(self.config.conf_path, "auth.json"), self.log
        )
        self._state.load()
        self.device_id = (
            self._state.data.get("deviceId") or self._load_or_create_device_id()
        )
        self._state.data["deviceId"] = self.device_id
        if self.config.account and not self._state.data.get("account"):
            self._state.data["account"] = self.config.account
            self._state.save()
        self._flow = None
        self.mi_session = ClientSession()
        self.device_manager = device_manager

    def _ensure_flow(self):
        from .login_flow import LoginFlow

        if self._flow is None:
            self._flow = LoginFlow(
                self._state,
                self.config.account,
                self.config.password,
                self.mi_session,
                self.log,
            )
        return self._flow

    def _load_or_create_device_id(self) -> str:
        for path in (
            os.path.join(self.config.conf_path, "auth.json"),
            os.path.join(self.config.conf_path, ".device_id"),
        ):
            try:
                if not os.path.isfile(path):
                    continue
                if path.endswith(".device_id"):
                    with open(path, encoding="utf-8") as f:
                        dev = f.read().strip()
                else:
                    with open(path, encoding="utf-8") as f:
                        dev = json.loads(f.read()).get("deviceId", "")
                if dev:
                    return dev
            except Exception:
                continue
        dev = get_random(16).upper()
        try:
            with open(
                os.path.join(self.config.conf_path, ".device_id"), "w", encoding="utf-8"
            ) as f:
                f.write(dev)
            os.chmod(os.path.join(self.config.conf_path, ".device_id"), 0o600)
        except Exception:
            pass
        return dev

    async def init_all_data(self, force_login=False):
        try:
            await asyncio.wait_for(
                self._init_all_data_with_lock(force_login),
                timeout=INIT_LOCK_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            self.log.warning("init_all_data 超时，可能被其他调用持有锁")

    async def refresh_token(self):
        if self.mina_service is None:
            return
        async with self._init_lock:
            try:
                flow = self._ensure_flow()
                result = await flow.ensure_sid("micoapi")
                if result != STATUS_OK:
                    self.log.warning(f"[AUTH-REFRESH] micoapi 刷新失败: {result}")
                    return
                self._bind_services()
                self._last_login_ok = True
                self._last_login_time = time.time()
                await self.device_manager.update_device_info(self)
                self.log.info("[AUTH-REFRESH] token 刷新成功")
            except Exception as e:
                self.log.warning(f"[AUTH-REFRESH] token 刷新异常: {e}")

    async def _init_all_data_with_lock(self, force_login=False):
        async with self._init_lock:
            await self._init_all_data_impl(force_login)

    async def _init_all_data_impl(self, force_login=False):
        self.mi_token_home = os.path.join(self.config.conf_path, ".mi.token")
        self.log.info(
            f"[AUTH] init_all_data 开始, "
            f"mina_service={'None' if self.mina_service is None else '已创建'}, "
            f"login_acount={self.login_acount}, "
            f"config.account={self.config.account}, "
            f"config.password={'***' if self.config.password else '(空)'}, "
            f"auth.json存在={os.path.isfile(os.path.join(self.config.conf_path, 'auth.json'))}, "
            f".mi.token存在={os.path.isfile(self.mi_token_home)}, "
            f"force_login={force_login}"
        )
        if force_login:
            is_need_login = True
            self.log.info("[AUTH] force_login=True，强制重新登录")
        else:
            is_need_login = await self.need_login()
        is_can_login = await self.can_login()
        self.log.info(f"[AUTH] need_login={is_need_login}, can_login={is_can_login}")
        if is_need_login and is_can_login:
            self.log.info("[AUTH] 需要登录，开始执行 login_miboy")
            login_ok = await self.login_miboy()
            if not login_ok:
                self.log.warning(
                    "[AUTH] 登录失败，降级：用配置中的设备初始化（播放走 URL 直推不依赖 micoapi）"
                )
        else:
            self.log.info(
                f"[AUTH] 无需登录 need_login:{is_need_login} can_login:{is_can_login}"
            )
        await self.device_manager.update_device_info(self)

    async def can_login(self):
        if self.config.account and self.config.password:
            return True
        if self._state.data.get("passToken"):
            return True
        self.log.warning("没有账号密码 且无已保存 passToken，无法登录")
        return False

    async def need_login(self):
        if self.mina_service is None:
            self.log.info("[AUTH-NEED] mina_service 为 None，需要登录")
            return True
        if self.login_acount != self.config.account:
            self.log.info(
                "[AUTH-NEED] 账号变更，需要登录: "
                f"old={self.login_acount} new={self.config.account}"
            )
            return True
        if self.login_password != self.config.password:
            self.log.info("[AUTH-NEED] 密码变更，需要登录")
            return True

        elapsed = time.time() - self._last_login_time
        if self._last_login_ok and elapsed < LOGIN_COOLDOWN_SEC:
            self.log.debug(
                f"[AUTH-NEED] 冷却期内({elapsed:.0f}s/{LOGIN_COOLDOWN_SEC}s)，跳过"
            )
            return False

        self.log.debug("[AUTH-NEED] 检查 device_list() 是否可用...")
        try:
            result = await self.mina_service.device_list()
            self.log.debug(f"[AUTH-NEED] device_list() 成功，返回 {len(result)} 个设备")
        except Exception as e:
            error_str = str(e)
            is_70016 = "70016" in error_str or "登录验证失败" in error_str
            if is_70016:
                self.log.warning(
                    f"[AUTH-NEED] device_list() 返回 70016(登录验证失败): {e}"
                )
            else:
                self.log.warning(f"[AUTH-NEED] device_list() 异常: {e}")
            if self._last_login_ok and elapsed < LOGIN_COOLDOWN_SEC * 2:
                self.log.warning(
                    "[AUTH-NEED] 最近登录成功但API调用失败，"
                    "可能是临时网络问题，暂不重新登录"
                )
                return False
            return True
        return False

    async def login_miboy(self):
        self.log.info(
            f"[AUTH-LOGIN] 开始登录, account={self.config.account or '(空/扫码登录)'}"
        )
        if self._state.cooldown_active():
            self.log.warning("[AUTH-LOGIN] 冷却中，跳过本次登录")
            return self._services_ready()

        flow = self._ensure_flow()
        try:
            micoapi = await flow.ensure_sid("micoapi")
            self.log.info(f"[AUTH-LOGIN] micoapi 状态: {micoapi}")
            if micoapi == STATUS_OK:
                self._consecutive_failures = 0
                self._bind_services()
                self.login_acount = self.config.account
                self.login_password = self.config.password
                self._last_login_ok = True
                self._last_login_time = time.time()
                self.log.info(f"[AUTH-LOGIN] 登录完成. account={self.login_acount}")
                # xiaomiio 仅用于设备列表，后台异步获取，避免阻塞启动/播放
                asyncio.create_task(self._login_xiaomiio_async())
                return True

            self._consecutive_failures += 1
            self.mina_service = None
            self.miio_service = None
            self._last_login_ok = False
            self._last_login_time = time.time()
            self.log.warning(
                f"[AUTH-LOGIN] micoapi 未就绪 ({micoapi})，"
                "可在设置页用短信验证码重新登录"
            )
            return False
        except Exception as e:
            self._consecutive_failures += 1
            self.mina_service = None
            self.miio_service = None
            self._last_login_ok = False
            self._last_login_time = time.time()
            self.log.warning(f"[AUTH-LOGIN] 异常: {e}")
            return False

    def _services_ready(self) -> bool:
        return self.mina_service is not None and self._last_login_ok

    async def _login_xiaomiio_async(self):
        try:
            flow = self._ensure_flow()
            status = await flow.ensure_sid("xiaomiio")
            self.log.info(f"[AUTH-LOGIN] xiaomiio 后台登录状态: {status}")
            if status == STATUS_OK:
                await self.device_manager.update_device_info(self)
        except Exception as e:
            self.log.warning(f"[AUTH-LOGIN] xiaomiio 后台登录异常: {e}")

    def _bind_services(self):
        store = AuthTokenStore(self._state, self.log)
        acct = MiAccount(
            self.mi_session,
            self.config.account,
            self.config.password,
            store,
            otp_callback=self._flow.otp.wait_code if self._flow else None,
        )
        acct.token = self._state.to_miservice_token()
        if not acct.token.get("deviceId"):
            acct.token["deviceId"] = self.device_id
        self.mina_service = MiNAService(acct)
        self.miio_service = MiIOService(acct)
        self._patch_account(acct)

    def _patch_account(self, mi_account):
        original_mi_request = mi_account.mi_request
        auth_manager = self

        async def patched_mi_request(sid, url, data, headers, relogin=True):
            try:
                return await original_mi_request(sid, url, data, headers, relogin)
            except Exception as exc:
                if not relogin:
                    raise
                auth_manager.log.warning(
                    f"[PATCH-mi_request] mi_request 失败: {exc}, "
                    "清理 session 并重新加载 token 后重试"
                )
                # MiAccount 内部属性是 _session（不是 session）；
                # 旧代码在这里抛 AttributeError，把「可恢复的登录失败」变成永久失败，
                # 连带 player_pause/player_stop 全部失效（表现为「暂停不管用」）。
                session = getattr(mi_account, "_session", None) or getattr(
                    mi_account, "session", None
                )
                cookie_jar = getattr(session, "cookie_jar", None)
                if cookie_jar is not None:
                    cookie_jar.clear()
                auth_manager._state.load()
                mi_account.token = auth_manager._state.to_miservice_token()
                if not mi_account.token.get("deviceId"):
                    mi_account.token["deviceId"] = auth_manager.device_id
                return await original_mi_request(sid, url, data, headers, relogin)

        mi_account.mi_request = patched_mi_request

    async def try_update_device_id(self):
        try:
            mi_dids = self.config.mi_did.split(",")
            hardware_data = await self.mina_service.device_list()
            devices = {}
            for h in hardware_data:
                device_id = h.get("deviceID", "")
                hardware = h.get("hardware", "")
                did = h.get("miotDID", "")
                name = h.get("alias", "")
                if not name:
                    name = h.get("name", "未知名字")
                if device_id and hardware and did:
                    if not mi_dids or not mi_dids[0] or (did in mi_dids):
                        device = self.config.devices.get(did, Device())
                        device.did = did
                        self._cur_did = did
                        device.device_id = device_id
                        device.hardware = hardware
                        device.name = name
                        devices[did] = device
            self.config.devices = devices
            self.log.info(f"[AUTH] 选中的设备: {devices}")
            return devices
        except Exception as e:
            self.log.warning(f"[AUTH] try_update_device_id 失败: {e}")
            return {}
