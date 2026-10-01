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

    # 注意：logged_in 必须反映「账号是否真的可用」，不能只看配置里有没有设备。
    # 旧实现用 bool(config.devices)，于是只要 conf/setting.json 填过 did，
    # 即使 xiaomiio 未验证、云端 401、设备列表为空，也会报「已登录」，
    # 与设备选择处「没找到小爱音箱」直接自相矛盾。
    micoapi_ok = status["sids"].get("micoapi") == "ok"
    xiaomiio_ok = status["sids"].get("xiaomiio") == "ok"
    status["logged_in"] = micoapi_ok
    status["device_list_available"] = xiaomiio_ok

    # 配置里登记过的设备数（离线可用），与云端真实拉取到的设备数是两回事，
    # 分开暴露避免再被当成「已登录且有 N 个设备」的证据。
    configured = auth.config.devices or {}
    status["configured_device_count"] = len(configured)
    status["device_count"] = len(auth.device_manager.devices) or len(configured)
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

    # 需要等待用户输入验证码时，state 必须是 pending，
    # 否则前端不会弹出验证码输入框（曾因末尾无条件覆盖 state 而丢失）。
    pending = None

    if micoapi == "cooldown":
        result["message"] = "请求过于频繁，请稍后再试"
    elif micoapi != "ok":
        started = await flow.open_verification("micoapi")
        result.update(started)
        result["success"] = started.get("state") == "ok"
        if started.get("state") == "pending":
            pending = started
    else:
        # micoapi 正常。注意 xiaomiio 是独立 sid：它未验证时设备列表会 401，
        # 而 ensure_sid 对 needs_verification 是终态、绝不重试（防风控），
        # 所以这里必须显式走 open_verification，否则用户永远没有入口完成验证。
        xiaomiio = await flow.ensure_sid("xiaomiio")
        auth._bind_services()
        if xiaomiio == "needs_verification":
            started = await flow.open_verification("xiaomiio")
            if started.get("state") == "pending":
                pending = started

    if pending is not None:
        result.update(pending)
        result["state"] = "pending"
    else:
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


@router.post("/api/login/verify/open")
async def login_verify_open(
    payload: dict, xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)
):
    """显式发起某个 sid 的二次验证（会下发短信）。

    只有用户主动点击才会走到这里；自动路径（ensure_sid）始终不请求 OTP/SMS。
    """
    auth = xiaomusic.auth_manager
    flow = auth._ensure_flow()
    sid = payload.get("sid") or "xiaomiio"
    if sid not in ("micoapi", "xiaomiio"):
        return {"success": False, "state": "failed", "message": f"未知 sid: {sid}"}

    started = await flow.open_verification(sid)
    state = started.get("state")
    if state == "pending":
        message = "验证码已发送，请查收"
    elif state == "ok":
        message = "验证已完成"
    elif state == "cooldown":
        message = "请求过于频繁，请稍后再试"
    else:
        message = "验证发起失败，请稍后重试"
    started["success"] = state in ("pending", "ok")
    started["message"] = message
    return started


@router.post("/api/login/verify/check")
async def login_verify_check(xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)):
    auth = xiaomusic.auth_manager
    flow = auth._ensure_flow()
    status = flow.status()
    # 两个 sid 都可能刚完成验证；只要有一个变 ok 就重建服务并刷新设备列表，
    # 否则用户验证成功后设备列表仍然是空的（正是「没找到小爱音箱」的成因）。
    if "ok" in status["sids"].values():
        auth._bind_services()
        if status["sids"].get("xiaomiio") == "ok":
            await auth.device_manager.update_device_info(auth)
    return status
