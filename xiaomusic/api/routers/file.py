import os
import shutil
from urllib.parse import urlparse

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
    RedirectResponse,
    Response,
)

from xiaomusic.api.dependencies import (
    access_key_verification,
    config,
    log,
    verification,
    xiaomusic,
)
from xiaomusic.utils.file_utils import (
    chmoddir,
    clean_temp_dir,
)
from xiaomusic.utils.music_utils import convert_file_to_mp3, is_mp3, remove_id3_tags
from xiaomusic.utils.system_utils import try_add_access_control_param

router = APIRouter()


@router.post("/api/file/cleantempdir")
async def cleantempdir(Verifcation=Depends(verification)):
    await clean_temp_dir(xiaomusic.config)
    log.info("clean_temp_dir ok")
    return {"ret": "OK"}


@router.post("/uploadmusic")
async def upload_music(playlist: str = Form(...), file: UploadFile = File(...)):
    """上传音乐文件到当前播放列表对应的目录"""
    try:
        # 选择目标目录：优先尝试由播放列表中已有歌曲推断目录
        dest_dir = config.music_path
        # 特殊歌单映射
        if playlist == "下载":
            dest_dir = config.download_path
        elif playlist == "其他":
            dest_dir = config.music_path
        else:
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


def safe_redirect(url):
    """安全重定向"""
    url = try_add_access_control_param(config, url)
    url = url.replace("\\", "")
    if not urlparse(url).netloc and not urlparse(url).scheme:
        log.debug(f"redirect to {url}")
        return RedirectResponse(url=url)
    return None


@router.get("/music/{file_path:path}")
async def music_file(request: Request, file_path: str, key: str = "", code: str = ""):
    """音乐文件访问"""
    if not access_key_verification(f"/music/{file_path}", key, code):
        raise HTTPException(status_code=404, detail="File not found")

    # temp/ 前缀表示文件在 temp_path 中
    if file_path.startswith("temp/"):
        temp_file_name = file_path[5:]
        if config.temp_path.startswith("/"):
            temp_base = config.temp_path
        else:
            temp_base = os.path.abspath(config.temp_path)
        absolute_file_path = os.path.normpath(os.path.join(temp_base, temp_file_name))
        if not absolute_file_path.startswith(temp_base + os.sep):
            raise HTTPException(status_code=404, detail="File not found")
        if not os.path.exists(absolute_file_path):
            raise HTTPException(status_code=404, detail="File not found")
    else:
        absolute_path = os.path.abspath(config.music_path)
        absolute_file_path = os.path.normpath(os.path.join(absolute_path, file_path))
        if not absolute_file_path.startswith(absolute_path + os.sep):
            raise HTTPException(status_code=404, detail="File not found")
        if not os.path.exists(absolute_file_path):
            raise HTTPException(status_code=404, detail="File not found")

    # 移除MP3 ID3 v2标签和填充
    if config.remove_id3tag and is_mp3(file_path):
        log.info(f"remove_id3tag:{config.remove_id3tag}, is_mp3:True ")
        temp_mp3_file = remove_id3_tags(absolute_file_path, config)
        if temp_mp3_file:
            mp3_name = os.path.basename(temp_mp3_file)
            redirect = safe_redirect(f"/music/temp/{mp3_name}")
            if redirect:
                return redirect
        else:
            log.info(f"No ID3 tag remove needed: {absolute_file_path}")

    if config.convert_to_mp3 and not is_mp3(file_path):
        temp_mp3_file = convert_file_to_mp3(absolute_file_path, config)
        if temp_mp3_file:
            mp3_name = os.path.basename(temp_mp3_file)
            redirect = safe_redirect(f"/music/temp/{mp3_name}")
            if redirect:
                return redirect
        else:
            log.warning(f"Failed to convert file to MP3 format: {absolute_file_path}")

    return FileResponse(absolute_file_path)


@router.options("/music/{file_path:path}")
async def music_options():
    """音乐文件 OPTIONS"""
    headers = {
        "Accept-Ranges": "bytes",
    }
    return Response(headers=headers)


@router.get("/picture/{file_path:path}")
async def get_picture(request: Request, file_path: str, key: str = "", code: str = ""):
    """图片文件访问"""
    if not access_key_verification(f"/picture/{file_path}", key, code):
        raise HTTPException(status_code=404, detail="File not found")

    absolute_path = os.path.abspath(config.picture_cache_path)
    absolute_file_path = os.path.normpath(os.path.join(absolute_path, file_path))
    if not absolute_file_path.startswith(absolute_path + os.sep):
        raise HTTPException(status_code=404, detail="File not found")
    if not os.path.exists(absolute_file_path):
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(absolute_file_path)
