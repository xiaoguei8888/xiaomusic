"""音乐库管理模块

负责音乐库的管理、播放列表操作、音乐搜索和标签管理。
"""

import asyncio
import copy
import json
import os
import time
import urllib.parse
from collections import OrderedDict
from dataclasses import asdict

from xiaomusic.const import SUPPORT_MUSIC_TYPE
from xiaomusic.events import CONFIG_CHANGED
from xiaomusic.utils.file_utils import (
    not_in_dirs,
    traverse_music_directory,
)
from xiaomusic.utils.music_utils import (
    Metadata,
    extract_audio_metadata,
    get_local_music_duration,
    save_picture_by_base64,
    set_music_tag_to_file,
)
from xiaomusic.utils.system_utils import try_add_access_control_param
from xiaomusic.utils.text_utils import custom_sort_key, find_best_match, fuzzyfinder



class MusicLibrary:
    """音乐库管理类

    负责管理本地音乐库，包括：
    - 音乐列表生成和管理
    - 播放列表的增删改查
    - 音乐搜索和模糊匹配
    - 音乐标签的读取和更新
    """

    def __init__(
        self,
        config,
        log,
        event_bus=None,
    ):
        """初始化音乐库

        Args:
            config: 配置对象
            log: 日志对象
            event_bus: 事件总线对象（可选）
        """
        self.config = config
        self.log = log
        self.event_bus = event_bus

        # 音乐库数据
        self.all_music = {}  # 所有音乐 {name: filepath}
        self.music_list = {}  # 播放列表 {list_name: [music_names]}
        self.default_music_list_names = []  # 非自定义歌单名称列表
        self.custom_play_list = None  # 自定义播放列表缓存

        # 搜索索引
        self._extra_index_search = {}  # 额外搜索索引 {filepath: name}

        # 标签管理
        self.all_music_tags = {}  # 音乐标签缓存
        self._tag_generation_task = False  # 标签生成任务标志


    def gen_all_music_list(self):
        """生成所有音乐列表

        扫描音乐目录，生成本地音乐列表和播放列表。
        """
        self.all_music = {}
        all_music_by_dir = {}

        # 扫描本地音乐目录
        exclude_dirs_set = self.config.get_exclude_dirs_set()
        local_musics = traverse_music_directory(
            self.config.music_path,
            depth=self.config.music_path_depth,
            exclude_dirs=exclude_dirs_set,
            support_extension=SUPPORT_MUSIC_TYPE,
        )

        for dir_name, files in local_musics.items():
            if len(files) == 0:
                continue

            # 处理目录名称
            if dir_name == os.path.basename(self.config.music_path):
                dir_name = "其他"


            if dir_name not in all_music_by_dir:
                all_music_by_dir[dir_name] = {}

            for file in files:
                # 歌曲名字相同会覆盖
                filename = os.path.basename(file)
                (name, _) = os.path.splitext(filename)
                self.all_music[name] = file
                all_music_by_dir[dir_name][name] = True
                self.log.debug(f"gen_all_music_list {name}:{dir_name}:{file}")

        # 初始化播放列表（使用 OrderedDict 保持顺序）
        self.music_list = OrderedDict(
            {
                "所有歌曲": [],
                "全部": [],  # 包含所有本地歌曲
                "其他": [],  # 主目录下的
                "最近新增": [],  # 按文件时间排序
            }
        )


        # 最近新增
        self.music_list["最近新增"] = sorted(
            self.all_music.keys(),
            key=lambda x: os.path.getmtime(self.all_music[x]),
            reverse=True,
        )[: self.config.recently_added_playlist_len]

        # 全部，所有歌曲
        self.music_list["全部"] = list(self.all_music.keys())
        self.music_list["所有歌曲"] = list(self.all_music.keys())


        # 文件夹歌单
        for dir_name, musics in all_music_by_dir.items():
            self.music_list[dir_name] = list(musics.keys())

        # 歌单排序
        for play_list in self.music_list.values():
            play_list.sort(key=custom_sort_key)


        # 非自定义歌单
        self.default_music_list_names = list(self.music_list.keys())

        # 刷新自定义歌单
        self.refresh_custom_play_list()

        # 重建索引
        self._extra_index_search = {}
        for name, filepath in self.all_music.items():
            self._extra_index_search[filepath] = name


        # all_music 更新，重建 tag（仅在事件循环启动后才会执行）
        self.try_gen_all_music_tag()


    def refresh_custom_play_list(self):
        """刷新自定义歌单"""
        try:
            # 删除旧的自定义歌单
            for k in list(self.music_list.keys()):
                if k not in self.default_music_list_names:
                    del self.music_list[k]

            # 合并新的自定义歌单
            custom_play_list = self.get_custom_play_list()
            custom_play_list, changed = self._normalize_custom_playlist_conflicts(
                custom_play_list
            )
            if changed:
                self.custom_play_list = custom_play_list
                self.config.custom_play_list_json = json.dumps(
                    custom_play_list, ensure_ascii=False
                )

            for k, v in custom_play_list.items():
                self.music_list[k] = list(v)
        except Exception as e:
            self.log.exception(f"Execption {e}")

    def _is_reserved_playlist_name(self, name):
        """判断是否与系统/目录歌单冲突（自定义歌单不可占用）"""
        return name in self.default_music_list_names

    def _build_custom_conflict_name(self, base_name, existed_names):
        """为冲突的自定义歌单生成可用的新名称"""
        suffix = "(自定义)"
        candidate = f"{base_name}{suffix}"
        if candidate not in existed_names:
            return candidate

        index = 2
        while True:
            candidate = f"{base_name}{suffix}{index}"
            if candidate not in existed_names:
                return candidate
            index += 1

    def _normalize_custom_playlist_conflicts(self, custom_play_list):
        """清理历史同名冲突：目录/系统歌单名被自定义占用时自动改名"""
        normalized = {}
        changed = False

        reserved_names = set(self.default_music_list_names)
        occupied_names = set(reserved_names)

        for name, musics in custom_play_list.items():
            final_name = name
            if final_name in reserved_names or final_name in occupied_names:
                final_name = self._build_custom_conflict_name(name, occupied_names)
                changed = True
                self.log.info(
                    "自定义歌单名与系统/目录歌单冲突，已自动改名: %s -> %s",
                    name,
                    final_name,
                )

            occupied_names.add(final_name)
            normalized[final_name] = list(musics)

        return normalized, changed

    def get_custom_play_list(self):
        """获取自定义播放列表

        Returns:
            dict: 自定义播放列表字典
        """
        if self.custom_play_list is None:
            self.custom_play_list = {}
            if self.config.custom_play_list_json:
                self.custom_play_list = json.loads(self.config.custom_play_list_json)
        return self.custom_play_list

    def save_custom_play_list(self):
        """保存自定义播放列表"""
        custom_play_list = self.get_custom_play_list()
        self.refresh_custom_play_list()
        self.config.custom_play_list_json = json.dumps(
            custom_play_list, ensure_ascii=False
        )
        # 发布配置变更事件
        if self.event_bus:
            self.event_bus.publish(CONFIG_CHANGED)

    # ==================== 播放列表管理 ====================

    def play_list_add(self, name):
        """新增歌单

        Args:
            name: 歌单名称

        Returns:
            bool: 是否成功
        """
        custom_play_list = self.get_custom_play_list()
        if self._is_reserved_playlist_name(name):
            self.log.info(f"歌单名字与系统/目录歌单冲突 {name}")
            return False
        if name in custom_play_list:
            return False
        custom_play_list[name] = []
        self.save_custom_play_list()
        return True

    def play_list_del(self, name):
        """移除歌单

        Args:
            name: 歌单名称

        Returns:
            bool: 是否成功
        """
        custom_play_list = self.get_custom_play_list()
        if name not in custom_play_list:
            return False
        custom_play_list.pop(name)
        self.save_custom_play_list()
        return True

    def play_list_update_name(self, oldname, newname):
        """修改歌单名字

        Args:
            oldname: 旧歌单名称
            newname: 新歌单名称

        Returns:
            bool: 是否成功
        """
        custom_play_list = self.get_custom_play_list()
        if oldname not in custom_play_list:
            self.log.info(f"旧歌单名字不存在 {oldname}")
            return False
        if self._is_reserved_playlist_name(newname):
            self.log.info(f"新歌单名字与系统/目录歌单冲突 {newname}")
            return False
        if newname in custom_play_list:
            self.log.info(f"新歌单名字已存在 {newname}")
            return False

        play_list = custom_play_list[oldname]
        custom_play_list.pop(oldname)
        custom_play_list[newname] = play_list
        self.save_custom_play_list()
        return True

    def get_play_list_names(self):
        """获取所有自定义歌单名称

        Returns:
            list: 歌单名称列表
        """
        custom_play_list = self.get_custom_play_list()
        return list(custom_play_list.keys())

    def play_list_musics(self, name):
        """获取歌单中所有歌曲

        Args:
            name: 歌单名称

        Returns:
            tuple: (状态消息, 歌曲列表)
        """
        custom_play_list = self.get_custom_play_list()
        if name not in custom_play_list:
            return "歌单不存在", []
        play_list = custom_play_list[name]
        return "OK", play_list

    def play_list_update_music(self, name, music_list):
        """歌单更新歌曲（覆盖）

        Args:
            name: 歌单名称
            music_list: 歌曲列表

        Returns:
            bool: 是否成功
        """
        custom_play_list = self.get_custom_play_list()
        if name not in custom_play_list:
            # 歌单不存在则新建
            if not self.play_list_add(name):
                return False

        play_list = []
        for music_name in music_list:
            if (music_name in self.all_music) and (music_name not in play_list):
                play_list.append(music_name)

        # 直接覆盖
        custom_play_list[name] = play_list
        self.save_custom_play_list()
        return True


    def _resolve_play_list(self, name, create_if_missing=False):
        """获取歌单列表引用，同时返回是否需要持久化自定义歌单

        系统/目录歌单直接返回 music_list 中的引用（无需持久化）；
        自定义歌单返回 custom_play_list 中的引用（需要持久化）。

        Args:
            name: 歌单名称
            create_if_missing: 自定义歌单不存在时是否自动新建

        Returns:
            tuple: (play_list, need_save) 或 (None, False) 表示失败
        """
        custom_play_list = self.get_custom_play_list()

        # 系统/目录歌单：直接操作 music_list，无需持久化
        if name in self.music_list and name not in custom_play_list:
            return self.music_list[name], False

        # 自定义歌单不存在时按需新建
        if name not in custom_play_list:
            if not create_if_missing or not self.play_list_add(name):
                return None, False

        return custom_play_list[name], True

    def play_list_add_music(self, name, music_list):
        """歌单新增歌曲

        Args:
            name: 歌单名称
            music_list: 歌曲列表

        Returns:
            bool: 是否成功
        """
        play_list, need_save = self._resolve_play_list(name, create_if_missing=True)
        if play_list is None:
            return False

        for music_name in music_list:
            if music_name in self.all_music and music_name not in play_list:
                play_list.append(music_name)

        if need_save:
            self.save_custom_play_list()
        return True

    def play_list_del_music(self, name, music_list):
        """歌单移除歌曲

        Args:
            name: 歌单名称
            music_list: 歌曲列表

        Returns:
            bool: 是否成功
        """
        play_list, need_save = self._resolve_play_list(name)
        if play_list is None:
            return False

        for music_name in music_list:
            if music_name in play_list:
                play_list.remove(music_name)

        if need_save:
            self.save_custom_play_list()
        return True

    # ==================== 音乐搜索 ====================

    def find_real_music_name(self, name, n):
        """模糊搜索音乐名称

        Args:
            name: 搜索关键词
            n: 返回结果数量

        Returns:
            list: 匹配的音乐名称列表
        """
        if not self.config.enable_fuzzy_match:
            self.log.debug("没开启模糊匹配")
            return []

        all_music_list = list(self.all_music.keys())
        real_names = find_best_match(
            name,
            all_music_list,
            cutoff=self.config.fuzzy_match_cutoff,
            n=n,
            extra_search_index=self._extra_index_search,
        )
        if not real_names:
            self.log.info(f"没找到歌曲【{name}】")
            return []
        self.log.info(f"根据【{name}】找到歌曲【{real_names}】")
        if name in real_names:
            return [name]

        # 音乐不在查找结果同时n大于1, 模糊匹配模式，扩大范围再找，最后按文件名自然排序
        if n > 1:
            real_names = find_best_match(
                name,
                all_music_list,
                cutoff=self.config.fuzzy_match_cutoff,
                n=n * 2,
                extra_search_index=self._extra_index_search,
            )
            real_names.sort(key=custom_sort_key)
        if not real_names:
            self.log.info(f"没找到歌曲【{name}】")
            return []
        return real_names[:n]

    def find_real_music_list_name(self, list_name):
        """模糊搜索播放列表名称

        Args:
            list_name: 播放列表名称

        Returns:
            str: 匹配的播放列表名称
        """
        if not self.config.enable_fuzzy_match:
            self.log.debug("没开启模糊匹配")
            return list_name

        # 模糊搜一个播放列表（只需要一个，不需要 extra index）
        real_name = find_best_match(
            list_name,
            self.music_list,
            cutoff=self.config.fuzzy_match_cutoff,
            n=1,
        )[0]

        if real_name:
            self.log.info(f"根据【{list_name}】找到播放列表【{real_name}】")
            list_name = real_name
        else:
            self.log.info(f"没找到播放列表【{list_name}】")

        return list_name

    def searchmusic(self, name):
        """搜索音乐

        Args:
            name: 搜索关键词

        Returns:
            list: 搜索结果列表
        """
        all_music_list = list(self.all_music.keys())
        search_list = fuzzyfinder(name, all_music_list, self._extra_index_search)
        self.log.debug(f"searchmusic. name:{name} search_list:{search_list}")
        return search_list

    # ==================== 音乐信息 ====================

    def get_filename(self, name):
        """获取音乐文件路径

        Args:
            name: 音乐名称

        Returns:
            str: 文件路径，不存在返回空字符串
        """
        if name not in self.all_music:
            self.log.info(f"get_filename not in. name:{name}")
            return ""

        filename = self.all_music[name]
        self.log.info(f"try get_filename. filename:{filename}")

        if os.path.exists(filename):
            return filename
        return ""

    def is_music_exist(self, name):
        """判断本地音乐是否存在

        Args:
            name: 音乐名称

        Returns:
            bool: 是否存在
        """
        if name not in self.all_music:
            return False
        return bool(self.get_filename(name))



    # ==================== 标签管理 ====================

    async def get_music_tags(self, name):
        """获取音乐标签信息

        Args:
            name: 音乐名称

        Returns:
            dict: 标签信息字典
        """
        tags = copy.copy(self.all_music_tags.get(name, asdict(Metadata())))
        picture = tags["picture"]

        if picture:
            if picture.startswith(self.config.picture_cache_path):
                picture = picture[len(self.config.picture_cache_path) :]
            picture = picture.replace("\\", "/")
            if picture.startswith("/"):
                picture = picture[1:]
            encoded_name = urllib.parse.quote(picture)
            tags["picture"] = try_add_access_control_param(
                self.config,
                f"{self.config.hostname}:{self.config.public_port}/picture/{encoded_name}",
            )

        return tags

    def set_music_tag(self, name, info):
        """修改标签信息

        Args:
            name: 音乐名称
            info: 标签信息对象

        Returns:
            str: 操作结果消息
        """
        if self._tag_generation_task:
            self.log.info("tag 更新中，请等待")
            return "Tag generation task running"

        tags = copy.copy(self.all_music_tags.get(name, asdict(Metadata())))
        tags["title"] = info.title
        tags["artist"] = info.artist
        tags["album"] = info.album
        tags["year"] = info.year
        tags["genre"] = info.genre
        tags["lyrics"] = info.lyrics

        file_path = self.all_music[name]
        if info.picture:
            tags["picture"] = save_picture_by_base64(
                info.picture, self.config.picture_cache_path, file_path
            )

        if self.config.enable_save_tag:
            set_music_tag_to_file(file_path, Metadata(tags))

        self.all_music_tags[name] = tags
        self.try_save_tag_cache()
        return "OK"


    async def get_music_duration(self, name: str, playlist_name: str = None) -> float:
        """获取歌曲时长

        先查标签缓存，未命中则读取本地文件时长并写回缓存。

        Args:
            name: 歌曲名称
            playlist_name: 兼容旧调用点，本地播放不使用

        Returns:
            float: 歌曲时长（秒），失败返回 0
        """
        if name not in self.all_music:
            self.log.warning(f"歌曲 {name} 不存在")
            return 0

        # 先检查缓存中是否有时长信息
        if name in self.all_music_tags:
            duration = self.all_music_tags[name].get("duration", 0)
            if duration > 0:
                self.log.debug(f"从缓存读取本地音乐 {name} 时长: {duration} 秒")
                return duration

        duration = 0
        try:
            filename = self.all_music[name]
            if os.path.exists(filename):
                duration = await get_local_music_duration(filename, self.config)
                self.log.info(f"本地音乐 {name} 时长: {duration} 秒")
            else:
                self.log.warning(f"本地音乐文件 {filename} 不存在")

            if duration > 0:
                if name not in self.all_music_tags:
                    self.all_music_tags[name] = asdict(Metadata())
                self.all_music_tags[name]["duration"] = duration
                self.try_save_tag_cache()
                self.log.info(f"已缓存本地音乐 {name} 时长: {duration} 秒")

        except Exception as e:
            self.log.exception(f"获取本地音乐 {name} 时长失败: {e}")

        return duration


    def refresh_music_tag(self):
        """刷新音乐标签（给前端调用）"""
        if not self.ensure_single_thread_for_tag():
            return

        filename = self.config.tag_cache_path
        if filename is not None:
            # 清空 cache
            with open(filename, "w", encoding="utf-8") as f:
                json.dump({}, f, ensure_ascii=False, indent=2)
            self.log.info("刷新：已清空 tag cache")
        else:
            self.log.info("刷新：tag cache 未启用")

        # TODO: 优化性能？
        # TODO 如何安全的清空 picture_cache_path
        self.all_music_tags = {}  # 需要清空内存残留
        self.try_gen_all_music_tag()
        self.log.info("刷新：已启动重建 tag cache")

    def try_load_from_tag_cache(self):
        """从缓存加载标签

        Returns:
            dict: 标签缓存字典
        """
        filename = self.config.tag_cache_path
        tag_cache = {}

        try:
            if filename is not None:
                if os.path.exists(filename):
                    with open(filename, encoding="utf-8") as f:
                        tag_cache = json.load(f)
                    self.log.info(f"已从【{filename}】加载 tag cache")
                else:
                    self.log.info(f"【{filename}】tag cache 已启用，但文件不存在")
            else:
                self.log.info("加载：tag cache 未启用")
        except Exception as e:
            self.log.exception(f"Execption {e}")

        return tag_cache

    def try_save_tag_cache(self):
        """保存标签缓存"""
        filename = self.config.tag_cache_path
        if filename is not None:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(self.all_music_tags, f, ensure_ascii=False, indent=2)
            self.log.info(f"保存：tag cache 已保存到【{filename}】")
        else:
            self.log.info("保存：tag cache 未启用")

    def ensure_single_thread_for_tag(self):
        """确保标签生成任务单线程执行

        Returns:
            bool: 是否可以执行新任务
        """
        if self._tag_generation_task:
            self.log.info("tag 更新中，请等待")
        return not self._tag_generation_task

    def try_gen_all_music_tag(self, only_items=None):
        """尝试生成所有音乐标签

        Args:
            only_items: 仅更新指定的音乐项，None表示更新全部
        """
        if self.ensure_single_thread_for_tag():
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                # 没有运行中的事件循环，跳过
                self.log.info("协程时间循环未启动")
                return
            asyncio.ensure_future(self._gen_all_music_tag(only_items))
            self.log.info("启动后台构建 tag cache")

    async def _gen_all_music_tag(self, only_items=None):
        """生成所有音乐标签（异步）

        Args:
            only_items: 仅更新指定的音乐项，None表示更新全部
        """
        self._tag_generation_task = True
        if only_items is None:
            only_items = self.all_music  # 默认更新全部

        all_music_tags = self.try_load_from_tag_cache()
        all_music_tags.update(self.all_music_tags)  # 保证最新

        ignore_tag_absolute_dirs = self.config.get_ignore_tag_dirs()
        self.log.info(f"ignore_tag_absolute_dirs: {ignore_tag_absolute_dirs}")

        for name, file_or_url in only_items.items():
            start = time.perf_counter()
            if name not in all_music_tags:
                try:
                    if os.path.exists(file_or_url) and not_in_dirs(
                        file_or_url, ignore_tag_absolute_dirs
                    ):
                        all_music_tags[name] = extract_audio_metadata(
                            file_or_url, self.config.picture_cache_path
                        )
                    else:
                        self.log.info(f"{name} {file_or_url} 无法更新 tag")
                except BaseException as e:
                    self.log.exception(f"{e} {file_or_url} error {type(file_or_url)}!")

            # 获取并缓存歌曲时长（仅本地音乐）
            if name in all_music_tags and "duration" not in all_music_tags[name]:
                try:
                    duration = await self.get_music_duration(name)
                    if duration > 0:
                        all_music_tags[name]["duration"] = duration
                except Exception as e:
                    self.log.warning(f"获取歌曲 {name} 时长失败: {e}")

            if (time.perf_counter() - start) < 1:
                await asyncio.sleep(0.001)
            else:
                # 处理一首歌超过1秒，则等1秒，解决挂载网盘卡死的问题
                await asyncio.sleep(1)

        # 全部更新结束后，一次性赋值
        self.all_music_tags = all_music_tags
        # 刷新 tag cache
        self.try_save_tag_cache()
        self._tag_generation_task = False
        self.log.info("tag 更新完成")

    # ==================== 辅助方法 ====================

    def get_music_list(self):
        """获取所有播放列表

        Returns:
            dict: 播放列表字典
        """
        return self.music_list

    def get_all_music(self):
        """获取所有音乐

        Returns:
            dict: 所有音乐字典
        """
        return self.all_music


    # ==================== URL处理方法 ====================

    # 接收 playlist_name
    async def get_music_url(self, name, playlist_name=None):
        """获取音乐播放地址（仅本地文件）

        Args:
            name: 歌曲名称
            playlist_name: 兼容旧调用点，本地播放不使用

        Returns:
            str: 播放地址，音乐不存在时返回空字符串
        """
        self.log.info(f"get_music_url name:{name}")
        return self._get_local_music_url(name)


    def _get_local_music_url(self, name):
        """获取本地音乐播放地址

        Args:
            name: 歌曲名称

        Returns:
            str: 本地音乐播放URL
        """
        filename = self.get_filename(name)
        self.log.info(
            f"_get_local_music_url local music. name:{name}, filename:{filename}"
        )
        if not filename:
            return ""
        return self._get_file_url(filename)

    def _get_file_url(self, filepath):
        """根据文件路径生成可访问的URL

        Args:
            filepath: 文件的完整路径

        Returns:
            str: 文件访问URL
        """
        filename = filepath

        # 处理文件路径
        if filename.startswith(self.config.music_path):
            filename = filename[len(self.config.music_path) :]
        filename = filename.replace("\\", "/")
        if filename.startswith("/"):
            filename = filename[1:]

        self.log.info(f"_get_file_url filepath:{filepath}, filename:{filename}")

        # 构造URL
        encoded_name = urllib.parse.quote(filename)
        url = f"{self.config.hostname}:{self.config.public_port}/music/{encoded_name}"
        return try_add_access_control_param(self.config, url)


