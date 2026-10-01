"""设备控制路由"""

import asyncio
import logging
import urllib.parse
from typing import TYPE_CHECKING

from fastapi import (
    APIRouter,
    Depends,
)

from xiaomusic.api.dependencies import (
    get_xiaomusic,
    verification,
)
from xiaomusic.api.models import (
    Did,
    DidCmd,
    DidPlayMusicList,
    DidVolume,
)

if TYPE_CHECKING:
    from xiaomusic.xiaomusic import XiaoMusic

log = logging.getLogger("xiaomusic")

router = APIRouter(dependencies=[Depends(verification)])


@router.get("/device_list")
async def device_list(xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)):
    """获取设备列表"""
    devices = await xiaomusic.getalldevices()
    return {"devices": devices}


@router.get("/getvolume")
async def getvolume(
    did: str = "", xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)
):
    """获取音量"""
    if not xiaomusic.did_exist(did):
        return {"volume": 0}

    volume = await xiaomusic.get_volume(did=did)
    return {"volume": volume}


@router.get("/getplayerstatus")
async def getplayerstatus(
    did: str = "", xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)
):
    """获取完整播放状态

    返回小米音箱的完整播放状态，包括：
    - status: 播放状态 (0=停止, 1=播放)
    - volume: 音量
    - play_song_detail: 播放详情
        - position: 当前播放位置（毫秒）
        - duration: 总时长（毫秒）
    """
    if not xiaomusic.did_exist(did):
        return {"status": 0, "volume": 0}

    return await xiaomusic.get_player_status(did=did)


@router.post("/setvolume")
async def setvolume(
    data: DidVolume, xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)
):
    """设置音量"""
    did = data.did
    volume = data.volume
    if not xiaomusic.did_exist(did):
        return {"ret": "Did not exist"}

    log.info(f"set_volume {did} {volume}")
    await xiaomusic.set_volume(did=did, arg1=volume)
    return {"ret": "OK", "volume": volume}


@router.post("/cmd")
async def do_cmd(data: DidCmd, xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)):
    """执行命令"""
    did = data.did
    cmd = data.cmd
    log.info(f"docmd. did:{did} cmd:{cmd}")
    if not xiaomusic.did_exist(did):
        return {"ret": "Did not exist"}

    if len(cmd) > 0:
        try:
            await xiaomusic.cancel_all_tasks()
            task = asyncio.create_task(xiaomusic.do_check_cmd(did=did, query=cmd))
            xiaomusic.append_running_task(task)
        except Exception as e:
            log.warning(f"Execption {e}")
        return {"ret": "OK"}
    return {"ret": "Unknow cmd"}


@router.get("/cmdstatus")
async def cmd_status(xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)):
    """命令状态"""
    finish = await xiaomusic.is_task_finish()
    if finish:
        return {"ret": "OK", "status": "finish"}
    return {"ret": "OK", "status": "running"}


@router.get("/playurl")
async def playurl(
    did: str, url: str, xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)
):
    """播放 URL"""
    if not xiaomusic.did_exist(did):
        return {"ret": "Did not exist"}
    decoded_url = urllib.parse.unquote(url)
    log.info(f"playurl did: {did} url: {decoded_url}")
    return await xiaomusic.play_url(did=did, arg1=decoded_url)


@router.get("/playtts")
async def playtts(
    did: str, text: str, xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)
):
    """播放 TTS"""
    if not xiaomusic.did_exist(did):
        return {"ret": "Did not exist"}

    log.info(f"tts {did} {text}")
    await xiaomusic.do_tts(did=did, value=text)
    return {"ret": "OK"}


@router.post("/device/stop")
async def stop(data: Did, xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)):
    """关机"""
    did = data.did
    log.info(f"stop did:{did}")
    if not xiaomusic.did_exist(did):
        return {"ret": "Did not exist"}

    try:
        await xiaomusic.stop(did, "notts")
    except Exception as e:
        log.warning(f"Execption {e}")
    return {"ret": "OK"}


@router.post("/device/pause")
async def pause(data: Did, xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)):
    """暂停播放（保留断点，可断点续播）"""
    did = data.did
    log.info(f"pause did:{did}")
    if not xiaomusic.did_exist(did):
        return {"ret": "Did not exist"}

    try:
        ok = await xiaomusic.pause(did)
    except Exception as e:
        log.warning(f"Execption {e}")
        ok = False
    return {"ret": "OK", "paused": bool(ok)}


@router.post("/device/resume")
async def resume(
    data: DidPlayMusicList, xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)
):
    """从暂停处继续播放

    musicname 是面板当前选中的歌：与断点歌曲不同就按点播从头播放，
    没有暂停会话时同样退回从头播放。
    """
    did = data.did
    log.info(f"resume did:{did} listname:{data.listname} musicname:{data.musicname}")
    if not xiaomusic.did_exist(did):
        return {"ret": "Did not exist"}

    try:
        ok = await xiaomusic.resume(
            did, music_name=data.musicname, list_name=data.listname
        )
    except Exception as e:
        log.warning(f"Execption {e}")
        ok = False
    return {"ret": "OK", "resumed": bool(ok)}
