"""登录路由"""

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends

from xiaomusic.api.dependencies import get_xiaomusic, verification

if TYPE_CHECKING:
    from xiaomusic.xiaomusic import XiaoMusic

router = APIRouter(dependencies=[Depends(verification)])


@router.get("/api/login/status")
async def login_status(xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)):
    """查询当前登录状态"""
    auth = xiaomusic.auth_manager
    flow = auth._ensure_flow()
    status = flow.status()
    devices = auth.config.devices or {}
    status["logged_in"] = bool(devices)
    status["device_count"] = len(devices)
    return status


@router.post("/api/login/start")
async def login_start(
    payload: dict, xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)
):
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
async def login_verify_submit(
    payload: dict, xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)
):
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
async def login_verify_check(xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)):
    auth = xiaomusic.auth_manager
    flow = auth._ensure_flow()
    status = flow.status()
    if status["sids"].get("micoapi") == "ok":
        auth._bind_services()
        await auth.device_manager.update_device_info(auth)
    return status
