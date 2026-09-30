"""WebSocket 相关功能"""

import asyncio
import json
import secrets
import time

import jwt
from fastapi import (
    APIRouter,
    Depends,
    WebSocket,
    WebSocketDisconnect,
)

from xiaomusic.api.dependencies import (
    verification,
)
from xiaomusic.events import PLAYER_STATE_CHANGED

router = APIRouter()

# JWT 配置
# 使用固定的 secret 避免重启后 token 失效
# 在生产环境中应该从环境变量或配置文件读取
JWT_SECRET = secrets.token_urlsafe(32)
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_SECONDS = 60 * 5  # 5 分钟有效期（足够前端连接和重连）


@router.get("/generate_ws_token")
def generate_ws_token(
    did: str = "",
    _: bool = Depends(verification),  # 复用 HTTP Basic 验证
):
    # 允许空 did，用于全局监控
    payload = {
        "did": did,
        "exp": time.time() + JWT_EXPIRE_SECONDS,
        "iat": time.time(),
    }

    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    return {
        "token": token,
        "expire_in": JWT_EXPIRE_SECONDS,
    }


@router.websocket("/ws/playingmusic")
async def ws_playingmusic(websocket: WebSocket):
    """WebSocket 播放状态推送"""
    # 运行时实例只从 app.state 取（ADR-0002），不再用模块级伪全局
    xiaomusic = getattr(websocket.app.state, "xiaomusic", None)
    if xiaomusic is None:
        await websocket.close(code=1008, reason="Not initialized")
        return

    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=1008, reason="Missing token")
        return

    did = ""
    on_player_state = None
    try:
        # 解码 JWT（自动校验签名 + 是否过期）
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        did = payload.get("did", "")

        # 允许空 did（用于全局监控），但需要检查设备是否存在
        if did and not xiaomusic.did_exist(did):
            await websocket.close(code=1003, reason="Did not exist")
            return

        await websocket.accept()
        if did:
            xiaomusic.start_cloud_polling(did)

        target_did = did
        wakeup = asyncio.Event()

        def on_player_state(did="", **kwargs):
            if did and did != target_did:
                return
            wakeup.set()

        xiaomusic.event_bus.subscribe(PLAYER_STATE_CHANGED, on_player_state)

        # 开始推送状态（只读服务端快照，不在循环里请求云端）
        while True:
            is_playing, snap = xiaomusic.get_display_state(did)
            cur_music = xiaomusic.playingmusic(did)
            cur_playlist = xiaomusic.get_cur_play_list(did)
            offset, duration = xiaomusic.get_offset_duration(did)

            await websocket.send_text(
                json.dumps(
                    {
                        "ret": "OK",
                        "is_playing": is_playing,
                        "cur_music": cur_music,
                        "cur_playlist": cur_playlist,
                        "offset": offset,
                        "duration": duration,
                        "status": snap.get("status") if snap else None,
                        "loop_type": snap.get("loop_type") if snap else None,
                        "volume": snap.get("volume") if snap else None,
                        "source": "cloud" if snap else "local",
                    }
                )
            )
            try:
                await asyncio.wait_for(wakeup.wait(), timeout=1)
            except asyncio.TimeoutError:
                pass
            wakeup.clear()

    except jwt.ExpiredSignatureError:
        await websocket.close(code=1008, reason="Token expired")
    except jwt.InvalidTokenError:
        await websocket.close(code=1008, reason="Invalid token")
    except WebSocketDisconnect:
        print(f"WebSocket disconnected: {did}")
    except Exception as e:
        print(f"Error: {e}")
        await websocket.close()
    finally:
        if on_player_state is not None:
            xiaomusic.event_bus.unsubscribe(PLAYER_STATE_CHANGED, on_player_state)
        if did:
            xiaomusic.stop_cloud_polling(did)
