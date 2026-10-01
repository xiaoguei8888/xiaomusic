"""音乐管理路由"""

import json
import logging
from typing import TYPE_CHECKING

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
)

from xiaomusic.api.dependencies import (
    get_xiaomusic,
    verification,
)
from xiaomusic.api.models import (
    DidPlayMusic,
    MusicInfoObj,
    MusicInfosQuery,
    MusicItem,
)

if TYPE_CHECKING:
    from xiaomusic.xiaomusic import XiaoMusic

log = logging.getLogger("xiaomusic")

router = APIRouter(dependencies=[Depends(verification)])


@router.get("/searchmusic")
def searchmusic(name: str = "", xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)):
    """搜索音乐"""
    return xiaomusic.music_library.searchmusic(name)


@router.get("/playingmusic")
def playingmusic(did: str = "", xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)):
    """当前播放音乐"""
    if not xiaomusic.did_exist(did):
        return {"ret": "Did not exist"}

    is_playing = xiaomusic.isplaying(did)
    cur_music = xiaomusic.playingmusic(did)
    cur_playlist = xiaomusic.get_cur_play_list(did)
    # 播放进度
    offset, duration = xiaomusic.get_offset_duration(did)
    return {
        "ret": "OK",
        "is_playing": is_playing,
        "cur_music": cur_music,
        "cur_playlist": cur_playlist,
        "offset": offset,
        "duration": duration,
    }


@router.get("/musiclist")
async def musiclist(xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)):
    """音乐列表"""
    return xiaomusic.music_library.get_music_list()


@router.get("/musicinfo")
async def musicinfo(
    name: str, musictag: bool = False, xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)
):
    """音乐信息"""
    url = await xiaomusic.music_library.get_music_url(name)
    info = {
        "ret": "OK",
        "name": name,
        "url": url,
    }
    if musictag:
        info["tags"] = await xiaomusic.music_library.get_music_tags(name)
    return info


@router.get("/musicinfos")
async def musicinfos(
    name: list[str] = Query(None),
    musictag: bool = False,
    xiaomusic: "XiaoMusic" = Depends(get_xiaomusic),
):
    """批量音乐信息"""
    ret = []
    for music_name in name:
        url = await xiaomusic.music_library.get_music_url(music_name)
        info = {
            "name": music_name,
            "url": url,
        }
        if musictag:
            info["tags"] = await xiaomusic.music_library.get_music_tags(music_name)
        ret.append(info)
    return ret


@router.post("/musicinfos")
async def musicinfos_post(
    data: MusicInfosQuery, xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)
):
    """批量音乐信息（POST，避免 URL 过长）"""
    ret = []
    for music_name in data.name:
        url = await xiaomusic.music_library.get_music_url(music_name)
        info = {
            "name": music_name,
            "url": url,
        }
        if data.musictag:
            info["tags"] = await xiaomusic.music_library.get_music_tags(music_name)
        ret.append(info)
    return ret


@router.post("/setmusictag")
async def setmusictag(
    info: MusicInfoObj, xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)
):
    """设置音乐标签"""
    ret = xiaomusic.music_library.set_music_tag(info.musicname, info)
    return {"ret": ret}


@router.post("/delmusic")
async def delmusic(data: MusicItem, xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)):
    """删除音乐"""
    log.info(data)
    await xiaomusic.del_music(data.name)
    return "success"


@router.post("/playmusic")
async def playmusic(
    data: DidPlayMusic, xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)
):
    """播放音乐"""
    did = data.did
    musicname = data.musicname
    searchkey = data.searchkey
    if not xiaomusic.did_exist(did):
        return {"ret": "Did not exist"}

    log.info(f"playmusic {did} musicname:{musicname} searchkey:{searchkey}")
    await xiaomusic.do_play(did, musicname, searchkey)
    return {"ret": "OK"}


@router.post("/refreshmusictag")
async def refreshmusictag(
    Verifcation=Depends(verification),
    xiaomusic: "XiaoMusic" = Depends(get_xiaomusic),
):
    """刷新音乐标签"""
    xiaomusic.music_library.refresh_music_tag()
    return {
        "ret": "OK",
    }


@router.post("/debug_play_by_music_url")
async def debug_play_by_music_url(
    request: Request,
    Verifcation=Depends(verification),
    xiaomusic: "XiaoMusic" = Depends(get_xiaomusic),
):
    """调试播放音乐URL"""
    try:
        data = await request.body()
        data_dict = json.loads(data.decode("utf-8"))
        log.info(f"data:{data_dict}")
        return await xiaomusic.debug_play_by_music_url(arg1=data_dict)
    except json.JSONDecodeError as err:
        raise HTTPException(status_code=400, detail="Invalid JSON") from err


@router.post("/api/music/refreshlist")
async def refreshlist(
    Verifcation=Depends(verification),
    xiaomusic: "XiaoMusic" = Depends(get_xiaomusic),
):
    """刷新歌曲列表"""
    await xiaomusic.gen_music_list()
    return {
        "ret": "OK",
    }
