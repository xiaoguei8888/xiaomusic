"""小爱账号扫码登录

通过小米的 longPolling/loginUrl 流程获取二维码，用户用米家 App 扫码后，
拿到账号级的 passToken + userId，再交给 AuthManager 换取 micoapi/xiaomiio
的 serviceToken。

设计要点：
- 只依赖 aiohttp（已是项目依赖）和 qrcode（用于本地生成二维码图片）。
- 不使用米家开放 API 的加密逻辑，因此不需要 pycryptodome / tzlocal。
- 二维码在服务端生成为 PNG data URI，前端直接 <img src> 即可，无需外网。
"""

import base64
import io
import json
import time
from urllib.parse import parse_qsl, urlencode, urlparse

import aiohttp
import qrcode

SERVICE_LOGIN_URL = "https://account.xiaomi.com/pass/serviceLogin"
LOGIN_URL = "https://account.xiaomi.com/longPolling/loginUrl"
DEFAULT_SID = "micoapi"
QR_TIMEOUT_SEC = 120


class QRLoginError(Exception):
    """扫码登录过程中的错误"""


def _parse_json_body(raw: bytes) -> dict:
    """小米接口返回体带有 &&&START&&& 前缀，需要先去掉。"""
    text = raw.decode("utf-8", errors="ignore")
    if "&&&START&&&" in text:
        text = text.split("&&&START&&&", 1)[1]
    return json.loads(text)


def make_qr_data_uri(content: str) -> str:
    """把登录链接生成为 PNG data URI，便于前端直接展示。"""
    img = qrcode.make(content)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


class QRLoginSession:
    """一次扫码登录会话的状态容器。"""

    def __init__(self, log, sid: str = DEFAULT_SID, timeout: int = QR_TIMEOUT_SEC):
        self.log = log
        self.sid = sid
        self.timeout = timeout
        # idle -> pending -> success / expired / error
        self.state = "idle"
        self.message = ""
        self.qr_data_uri = ""
        self.result: dict | None = None

    async def start(self, session: aiohttp.ClientSession) -> str:
        """向小米申请二维码，返回用于长轮询的 lp 地址。"""
        # Step 1: serviceLogin 获取 qs / _sign / callback
        url = f"{SERVICE_LOGIN_URL}?sid={self.sid}&_json=true"
        async with session.get(url, ssl=False) as resp:
            data = _parse_json_body(await resp.read())

        if not data.get("_sign") or not data.get("location"):
            raise QRLoginError(data.get("description") or "获取登录参数失败")

        params = dict(parse_qsl(urlparse(data["location"]).query))
        params.update(
            {
                "theme": "",
                "bizDeviceType": "",
                "_hasLogo": "false",
                "_qrsize": "240",
                "_dc": str(int(time.time() * 1000)),
            }
        )

        # Step 2: 用登录参数换取二维码链接
        async with session.get(f"{LOGIN_URL}?{urlencode(params)}", ssl=False) as resp:
            login_data = _parse_json_body(await resp.read())

        login_url = login_data.get("loginUrl")
        lp = login_data.get("lp")
        if not login_url or not lp:
            raise QRLoginError(login_data.get("description") or "获取二维码失败")

        # 优先使用小米官方二维码图片地址，失败则本地生成
        self.qr_data_uri = ""
        qr_image_url = login_data.get("qr")
        if qr_image_url:
            self.qr_data_uri = qr_image_url
        try:
            if not self.qr_data_uri.startswith("data:"):
                self.qr_data_uri = make_qr_data_uri(login_url)
        except Exception as e:  # 本地生成失败时退回官方图片地址
            self.log.warning(f"[QR] 本地生成二维码失败: {e}")
            self.qr_data_uri = qr_image_url or ""
        if not self.qr_data_uri:
            raise QRLoginError("二维码生成失败")

        return lp

    async def wait(self, session: aiohttp.ClientSession, lp: str) -> dict:
        """长轮询等待用户扫码，成功后返回 passToken / userId。"""
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        try:
            async with session.get(lp, ssl=False, timeout=timeout) as resp:
                data = _parse_json_body(await resp.read())
        except TimeoutError as err:
            raise QRLoginError("等待扫码超时，请重试") from err

        pass_token = data.get("passToken")
        user_id = data.get("userId")
        if not pass_token or not user_id:
            raise QRLoginError(data.get("description") or "扫码登录失败")

        # 完成回调，让小米正常建立会话
        location = data.get("location")
        if location:
            try:
                async with session.get(location, ssl=False) as resp:
                    await resp.read()
            except Exception as e:
                self.log.warning(f"[QR] 回调调用失败（可忽略）: {e}")

        self.result = {
            "passToken": pass_token,
            "userId": str(user_id),
            "cUserId": data.get("cUserId", ""),
            "ssecurity": data.get("ssecurity", ""),
        }
        return self.result
