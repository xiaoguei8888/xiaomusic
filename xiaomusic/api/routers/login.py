"""扫码登录路由"""

import asyncio

import aiohttp
from fastapi import APIRouter, Depends

from xiaomusic.api.dependencies import log, verification, xiaomusic
from xiaomusic.qrcode_login import QRLoginError, QRLoginSession

router = APIRouter(dependencies=[Depends(verification)])

_session: QRLoginSession | None = None


@router.get("/api/login/qrcode")
async def get_qrcode():
    """生成扫码登录二维码"""
    global _session
    session = QRLoginSession(log)
    try:
        async with aiohttp.ClientSession() as client:
            lp = await session.start(client)
    except QRLoginError as e:
        return {"success": False, "message": str(e)}
    except Exception as e:
        log.exception("get_qrcode failed: %s", e)
        return {"success": False, "message": f"获取二维码失败: {e}"}

    _session = session
    session.state = "pending"
    asyncio.create_task(_wait_login(session, lp))
    return {
        "success": True,
        "qrcode": session.qr_data_uri,
        "expires_in": session.timeout,
    }


async def _wait_login(session: QRLoginSession, lp: str):
    try:
        async with aiohttp.ClientSession() as client:
            result = await session.wait(client, lp)
        await xiaomusic.auth_manager.apply_qr_login(
            result["passToken"], result["userId"]
        )
        session.state = "success"
        session.message = "登录成功"
    except QRLoginError as e:
        session.state = "expired" if "超时" in str(e) else "error"
        session.message = str(e)
    except Exception as e:
        log.exception("qr login failed: %s", e)
        session.state = "error"
        session.message = str(e)


@router.get("/api/login/qrcode/status")
async def qrcode_status():
    """查询扫码登录状态"""
    if _session is None:
        return {"state": "idle", "message": ""}
    return {"state": _session.state, "message": _session.message}


@router.get("/api/login/status")
async def login_status():
    """查询当前登录状态"""
    auth = xiaomusic.auth_manager
    flow = auth._ensure_flow()
    status = flow.status()
    devices = auth.config.devices or {}
    status["logged_in"] = bool(devices)
    status["device_count"] = len(devices)
    return status


@router.post("/api/login/start")
async def login_start(payload: dict):
    """用账号密码启动登录；password 为空则用已保存的凭据。"""
    auth = xiaomusic.auth_manager
    account = payload.get("account") or auth.config.account
    password = payload.get("password") or auth.config.password
    if not account or not password:
        return {"success": False, "message": "缺少账号或密码"}

    auth.config.account = account
    auth.config.password = password
    auth._flow = None
    flow = auth._ensure_flow()

    micoapi = await flow.ensure_sid("micoapi")
    result = flow.status()
    result["success"] = micoapi == "ok"
    if micoapi == "ok":
        await flow.ensure_sid("xiaomiio")
        auth._bind_services()
    elif micoapi == "cooldown":
        result["message"] = "请求过于频繁，请稍后再试"
    else:
        started = await flow.open_verification("micoapi")
        result.update(started)
        result["success"] = started.get("state") == "ok"
    result["state"] = flow.status()["state"]
    return result


@router.post("/api/login/verify/submit")
async def login_verify_submit(payload: dict):
    """提交短信/邮箱验证码。"""
    auth = xiaomusic.auth_manager
    flow = auth._ensure_flow()
    sid = payload.get("sid", "micoapi")
    code = (payload.get("code") or "").strip()
    if not code:
        return {"success": False, "message": "验证码为空"}
    ok = flow.submit_code(sid, code)
    return {"success": ok, "message": "" if ok else "当前没有等待中的验证"}


@router.post("/api/login/verify/check")
async def login_verify_check():
    auth = xiaomusic.auth_manager
    flow = auth._ensure_flow()
    status = flow.status()
    if status["sids"].get("micoapi") == "ok":
        auth._bind_services()
        await auth.device_manager.update_device_info(auth)
    return status
