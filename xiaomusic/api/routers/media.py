"""媒体文件路由（本地音乐/封面/上传）"""

import logging
import os
import shutil
from typing import TYPE_CHECKING

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import (
    FileResponse,
    Response,
)

from xiaomusic.api.dependencies import (
    access_key_verification,
    get_xiaomusic,
)
from xiaomusic.utils.file_utils import chmoddir

if TYPE_CHECKING:
    from xiaomusic.xiaomusic import XiaoMusic

log = logging.getLogger("xiaomusic")

router = APIRouter()


@router.post("/uploadmusic")
async def upload_music(
    playlist: str = Form(...),
    file: UploadFile = File(...),
    xiaomusic: "XiaoMusic" = Depends(get_xiaomusic),
):
    """上传音乐文件到当前播放列表对应的目录"""
    try:
        # 选择目标目录：优先尝试由播放列表中已有歌曲推断目录
        dest_dir = xiaomusic.config.music_path
        # 如果播放列表中存在歌曲，从其中任意一首推断目录
        musics = xiaomusic.music_list.get(playlist, [])
        if musics and len(musics) > 0:
            first = musics[0]
            filepath = xiaomusic.music_library.all_music.get(first, "")
            if filepath:
                dest_dir = os.path.dirname(filepath)

        # 确保目录存在
        if not os.path.exists(dest_dir):
            os.makedirs(dest_dir, exist_ok=True)

        # 保存文件，避免路径穿越
        filename = os.path.basename(file.filename)
        if filename == "":
            raise HTTPException(status_code=400, detail="Invalid filename")

        dest_path = os.path.join(dest_dir, filename)
        # 避免覆盖已有文件，简单地添加序号后缀
        base, ext = os.path.splitext(filename)
        counter = 1
        while os.path.exists(dest_path):
            filename = f"{base}_{counter}{ext}"
            dest_path = os.path.join(dest_dir, filename)
            counter += 1

        with open(dest_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # 修复权限并刷新列表索引
        try:
            chmoddir(dest_dir)
        except Exception:
            pass

        # 重新生成音乐列表索引
        try:
            xiaomusic.music_library.gen_all_music_list()
        except Exception:
            pass

        return {"ret": "OK", "filename": filename}
    except HTTPException:
        raise
    except Exception as e:
        log.exception(f"upload music failed: {e}")
        raise HTTPException(status_code=500, detail="Upload failed") from e


@router.get("/music/{file_path:path}")
async def music_file(
    request: Request,
    file_path: str,
    key: str = "",
    code: str = "",
    xiaomusic: "XiaoMusic" = Depends(get_xiaomusic),
):
    """音乐文件访问（仅本地音乐目录）"""
    if not access_key_verification(f"/music/{file_path}", key, code, xiaomusic.config):
        raise HTTPException(status_code=404, detail="File not found")

    absolute_path = os.path.abspath(xiaomusic.config.music_path)
    absolute_file_path = os.path.normpath(os.path.join(absolute_path, file_path))
    if not absolute_file_path.startswith(absolute_path + os.sep):
        raise HTTPException(status_code=404, detail="File not found")
    if not os.path.exists(absolute_file_path):
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(absolute_file_path)


@router.options("/music/{file_path:path}")
async def music_options():
    """音乐文件 OPTIONS"""
    headers = {
        "Accept-Ranges": "bytes",
    }
    return Response(headers=headers)


@router.get("/picture/{file_path:path}")
async def get_picture(
    request: Request,
    file_path: str,
    key: str = "",
    code: str = "",
    xiaomusic: "XiaoMusic" = Depends(get_xiaomusic),
):
    """图片文件访问"""
    if not access_key_verification(
        f"/picture/{file_path}", key, code, xiaomusic.config
    ):
        raise HTTPException(status_code=404, detail="File not found")

    absolute_path = os.path.abspath(xiaomusic.config.picture_cache_path)
    absolute_file_path = os.path.normpath(os.path.join(absolute_path, file_path))
    if not absolute_file_path.startswith(absolute_path + os.sep):
        raise HTTPException(status_code=404, detail="File not found")
    if not os.path.exists(absolute_file_path):
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(absolute_file_path)
