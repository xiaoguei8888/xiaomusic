"""播放列表路由"""

from typing import TYPE_CHECKING

from fastapi import (
    APIRouter,
    Depends,
)

from xiaomusic.api.dependencies import (
    get_xiaomusic,
    log,
    verification,
)
from xiaomusic.api.models import (
    DidPlayMusicList,
    PlayListMusicObj,
    PlayListObj,
    PlayListUpdateObj,
)

if TYPE_CHECKING:
    from xiaomusic.xiaomusic import XiaoMusic

router = APIRouter(dependencies=[Depends(verification)])


@router.get("/curplaylist")
async def curplaylist(
    did: str = "", xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)
):
    """当前播放列表"""
    if not xiaomusic.did_exist(did):
        return ""
    return xiaomusic.get_cur_play_list(did)


@router.post("/playmusiclist")
async def playmusiclist(
    data: DidPlayMusicList, xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)
):
    """播放音乐列表"""
    did = data.did
    listname = data.listname
    musicname = data.musicname
    if not xiaomusic.did_exist(did):
        return {"ret": "Did not exist"}

    log.info(f"playmusiclist {did} listname:{listname} musicname:{musicname}")
    await xiaomusic.do_play_music_list(did, listname, musicname)
    return {"ret": "OK"}


@router.post("/playlistadd")
async def playlistadd(
    data: PlayListObj, xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)
):
    """新增歌单"""
    ret = xiaomusic.music_library.play_list_add(data.name)
    if ret:
        return {"ret": "OK"}
    return {"ret": "Add failed, may be already exist."}


@router.post("/playlistdel")
async def playlistdel(
    data: PlayListObj, xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)
):
    """移除歌单"""
    ret = xiaomusic.music_library.play_list_del(data.name)
    if ret:
        return {"ret": "OK"}
    return {"ret": "Del failed, may be not exist."}


@router.post("/playlistupdatename")
async def playlistupdatename(
    data: PlayListUpdateObj, xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)
):
    """修改歌单名字"""
    ret = xiaomusic.music_library.play_list_update_name(data.oldname, data.newname)
    if ret:
        return {"ret": "OK"}
    return {"ret": "Update failed, may be not exist."}


@router.get("/playlistnames")
async def getplaylistnames(xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)):
    """获取所有自定义歌单"""
    names = xiaomusic.music_library.get_play_list_names()
    log.info(f"names {names}")
    return {
        "ret": "OK",
        "names": names,
    }


@router.post("/playlistaddmusic")
async def playlistaddmusic(
    data: PlayListMusicObj, xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)
):
    """歌单新增歌曲"""
    ret = xiaomusic.music_library.play_list_add_music(data.name, data.music_list)
    if ret:
        return {"ret": "OK"}
    return {"ret": "Add failed, may be playlist not exist."}


@router.post("/playlistdelmusic")
async def playlistdelmusic(
    data: PlayListMusicObj, xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)
):
    """歌单移除歌曲"""
    ret = xiaomusic.music_library.play_list_del_music(data.name, data.music_list)
    if ret:
        return {"ret": "OK"}
    return {"ret": "Del failed, may be playlist not exist."}


@router.post("/playlistupdatemusic")
async def playlistupdatemusic(
    data: PlayListMusicObj, xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)
):
    """歌单更新歌曲"""
    ret = xiaomusic.music_library.play_list_update_music(data.name, data.music_list)
    if ret:
        return {"ret": "OK"}
    return {"ret": "Del failed, may be playlist not exist."}


@router.get("/playlistmusics")
async def getplaylist(
    name: str, xiaomusic: "XiaoMusic" = Depends(get_xiaomusic)
):
    """获取歌单中所有歌曲"""
    ret, musics = xiaomusic.music_library.play_list_musics(name)
    return {
        "ret": "OK",
        "musics": musics,
    }
