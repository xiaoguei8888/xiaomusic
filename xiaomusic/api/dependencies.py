"""依赖注入和认证相关功能

运行时状态只有 FastAPI 的 app.state（ADR-0002）；这里不再有模块级的伪全局
（历史实现用延迟代理模拟 config / log / xiaomusic 三个全局对象）。
需要配置与日志的函数一律从 request.app.state（或 scope 上的 app.state）显式取用。
"""

import hashlib
import logging
import secrets
import time  # 用于生成 7 天免密 Cookie 的过期时间（exp）
from typing import (
    TYPE_CHECKING,
    Annotated,
)

import jwt  # 用于生成和验证 JWT Token
from fastapi import (
    Depends,
    HTTPException,
    Request,
    Response,  # 引入 Response 用于写入 Cookie
    status,
)
from fastapi.security import (
    HTTPBasic,
    HTTPBasicCredentials,
)
from fastapi.staticfiles import StaticFiles

if TYPE_CHECKING:
    from xiaomusic.config import Config
    from xiaomusic.xiaomusic import XiaoMusic

# 关闭基础认证的自动抛错，让我们接管验证流程
security = HTTPBasic(auto_error=False)

_log = logging.getLogger("xiaomusic")


def _app_state():
    """延迟获取 FastAPI app.state，避免与 app.py 的循环导入。"""
    from xiaomusic.api.app import app

    return app.state


def _state_config(state) -> "Config":
    """从 app.state 取配置（唯一状态源），未初始化时报错。"""
    config = getattr(state, "config", None)
    if config is None:
        raise RuntimeError("config not initialized. Call HttpInit() first.")
    return config


def _state_log(state) -> logging.Logger:
    """从 app.state 取日志（唯一状态源），未初始化时报错。"""
    log = getattr(state, "log", None)
    if log is None:
        raise RuntimeError("log not initialized. Call HttpInit() first.")
    return log


def initialize_state(xiaomusic_instance: "XiaoMusic") -> None:
    """把运行时实例挂到 FastAPI app.state（运行期唯一状态源）。"""
    state = _app_state()
    state.xiaomusic = xiaomusic_instance
    state.config = xiaomusic_instance.config
    state.log = xiaomusic_instance.log


def is_initialized() -> bool:
    """检查运行时实例是否已就绪。"""
    return getattr(_app_state(), "xiaomusic", None) is not None


def get_xiaomusic(request: Request) -> "XiaoMusic":
    """FastAPI 依赖：取当前 XiaoMusic 实例。"""
    xiaomusic_instance = getattr(request.app.state, "xiaomusic", None)
    if xiaomusic_instance is None:
        raise RuntimeError("xiaomusic not initialized. Call HttpInit() first.")
    return xiaomusic_instance


# 增加了 request 和 response 参数以操作 Cookie，并将 credentials 设为 Optional
def verification(
    request: Request,
    response: Response,
    credentials: Annotated[HTTPBasicCredentials | None, Depends(security)],
):
    """HTTP Basic 认证"""
    config = _state_config(request.app.state)

    # ========================================================
    # 7天免密模块 开始 (API拦截层)
    # ========================================================
    if config.disable_httpauth:
        return True

    session_secret = hashlib.sha256(config.httpauth_password.encode()).hexdigest()
    cookie_name = "xiaomusic_auth_session"

    token = request.cookies.get(cookie_name)
    if token:
        try:
            jwt.decode(token, session_secret, algorithms=["HS256"])
            return True
        except Exception:
            pass

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    # ========================================================

    current_username_bytes = credentials.username.encode("utf8")
    correct_username_bytes = config.httpauth_username.encode("utf8")
    is_correct_username = secrets.compare_digest(
        current_username_bytes, correct_username_bytes
    )
    current_password_bytes = credentials.password.encode("utf8")
    correct_password_bytes = config.httpauth_password.encode("utf8")
    is_correct_password = secrets.compare_digest(
        current_password_bytes, correct_password_bytes
    )
    if not (is_correct_username and is_correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )

    # ========================================================
    # 验证成功后，在此处派发持久化 Cookie
    # ========================================================
    expire_time = time.time() + 60 * 60 * 24 * 7
    payload = {"sub": credentials.username, "exp": expire_time}
    new_token = jwt.encode(payload, session_secret, algorithm="HS256")
    response.set_cookie(
        key=cookie_name,
        value=new_token,
        max_age=60 * 60 * 24 * 7,
        httponly=True,
        samesite="lax",
    )
    # ========================================================
    return True


def no_verification():
    """无认证模式"""
    return True


def access_key_verification(
    file_path: str, key: str, code: str, config: "Config"
) -> bool:
    """访问密钥验证（config 由调用方通过 DI 注入）"""
    if config.disable_httpauth:
        return True

    _log.debug(f"访问限制接收端[{file_path}, {key}, {code}]")
    if key is not None:
        current_key_bytes = key.encode("utf8")
        correct_key_bytes = (
            config.httpauth_username + config.httpauth_password
        ).encode("utf8")
        is_correct_key = secrets.compare_digest(correct_key_bytes, current_key_bytes)
        if is_correct_key:
            return True

    if code is not None:
        current_code_bytes = code.encode("utf8")
        correct_code_bytes = (
            hashlib.sha256(
                (
                    file_path + config.httpauth_username + config.httpauth_password
                ).encode("utf-8")
            )
            .hexdigest()
            .encode("utf-8")
        )
        is_correct_code = secrets.compare_digest(correct_code_bytes, current_code_bytes)
        if is_correct_code:
            return True

    return False


class AuthStaticFiles(StaticFiles):
    """需要认证的静态文件服务"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

    async def __call__(self, scope, receive, send) -> None:
        request = Request(scope, receive)
        # 系统提示音，不走任何校验，直接允许访问(修复启用安全验证后，无法播放系统提示音的问题)
        if request.url.path.endswith(
            ("/xiaomusic_ok.mp3", "/xiaomusic_error.mp3", "/silence.mp3", "/search.mp3")
        ):
            await super().__call__(scope, receive, send)
            return
        config = _state_config(request.app.state)
        if not config.disable_httpauth:
            # ========================================================
            # 7天免密模块 开始 (网页静态文件拦截层)
            # ========================================================
            session_secret = hashlib.sha256(
                config.httpauth_password.encode()
            ).hexdigest()
            cookie_name = "xiaomusic_auth_session"
            token = request.cookies.get(cookie_name)
            is_authed = False

            if token:
                try:
                    jwt.decode(token, session_secret, algorithms=["HS256"])
                    is_authed = True
                except Exception:
                    pass

            if not is_authed:
                credentials = await security(request)
                if not credentials:
                    response = Response(
                        status_code=401, headers={"WWW-Authenticate": "Basic"}
                    )
                    await response(scope, receive, send)
                    return

                current_username_bytes = credentials.username.encode("utf8")
                correct_username_bytes = config.httpauth_username.encode("utf8")
                is_correct_username = secrets.compare_digest(
                    current_username_bytes, correct_username_bytes
                )
                current_password_bytes = credentials.password.encode("utf8")
                correct_password_bytes = config.httpauth_password.encode("utf8")
                is_correct_password = secrets.compare_digest(
                    current_password_bytes, correct_password_bytes
                )

                if not (is_correct_username and is_correct_password):
                    response = Response(
                        status_code=401, headers={"WWW-Authenticate": "Basic"}
                    )
                    await response(scope, receive, send)
                    return
            # ========================================================
            # 原有的 assert verification 被上面的拦截取代，避免重复弹窗
            pass
        await super().__call__(scope, receive, send)


def reset_http_server(app):
    """重置 HTTP 服务器配置"""
    config = _state_config(app.state)
    _state_log(app.state).info(f"disable_httpauth:{config.disable_httpauth}")
    if config.disable_httpauth:
        app.dependency_overrides[verification] = no_verification
    else:
        app.dependency_overrides = {}
