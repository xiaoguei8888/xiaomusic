"""L3 services：XiaoMusic 的命令目标方法与单层转发。

docs/architecture.md 把 services 层定义为「用例编排」（门面 xiaomusic.py ·
command_handler · conversation）。M1 之前，门面把「装配 + 生命周期 + 命令实现 +
单层转发」全塞进一个类；这里把后两者拆成 Mixin，由门面继承：

    class XiaoMusic(CommandTargets): ...

分层理由：
- 门面只保留装配、生命周期与跨模块编排，命令实现集中一处，
  后续可以平滑演化为独立 Service 类（而不是继续堆在 god-facade 上）；
- commands.COMMAND_NAMES 里的 18 条命令仍通过继承成为 XiaoMusic 的类属性，
  test/test_registry.py 的 getattr(XiaoMusic, name) 校验不受影响；
- 本模块只使用门面注入的组件（config / log / device_manager / music_library /
  auth_manager），不 import xiaomusic.xiaomusic，避免循环依赖。

这里保留的「极薄包装」是本层与 L2 domain 的边界：参数形态（did/arg1、中文数字、
竖线分隔）属于口令层语义，留在 services；真正的播放/曲库动作全部在 domain。
"""

import os
import re

from xiaomusic.const import (
    PLAY_TYPE_ALL,
    PLAY_TYPE_ONE,
    PLAY_TYPE_RND,
    PLAY_TYPE_SEQ,
    PLAY_TYPE_SIN,
)
from xiaomusic.utils.text_utils import chinese_to_number


class CommandTargets:
    """命令目标方法 + 按 did 的单层转发（由 XiaoMusic 继承）。"""

    # ==================== 播放模式 ====================
    async def set_play_type_one(self, did="", **kwargs):
        await self.set_play_type(did, PLAY_TYPE_ONE)

    async def set_play_type_all(self, did="", **kwargs):
        await self.set_play_type(did, PLAY_TYPE_ALL)

    async def set_play_type_rnd(self, did="", **kwargs):
        await self.set_play_type(did, PLAY_TYPE_RND)

    async def set_play_type_sin(self, did="", **kwargs):
        await self.set_play_type(did, PLAY_TYPE_SIN)

    async def set_play_type_seq(self, did="", **kwargs):
        await self.set_play_type(did, PLAY_TYPE_SEQ)

    async def set_play_type(self, did="", play_type=PLAY_TYPE_RND, dotts=True):
        await self.device_manager.devices[did].set_play_type(play_type, dotts)

    # ==================== 播放传输 ====================
    async def play(self, did="", arg1="", **kwargs):
        parts = arg1.split("|")
        search_key = parts[0]
        name = parts[1] if len(parts) > 1 else search_key
        if not name:
            name = search_key

        # 语音播放会根据歌曲匹配更新当前播放列表
        return await self.do_play(did, name, search_key)

    # 网页面板搜索播放
    async def do_play(self, did, name, search_key=""):
        return await self.device_manager.devices[did].play(name, search_key)

    async def playlocal(self, did="", arg1="", **kwargs):
        return await self.device_manager.devices[did].playlocal(arg1)

    async def play_next(self, did="", **kwargs):
        return await self.device_manager.devices[did].play_next()

    async def play_prev(self, did="", **kwargs):
        return await self.device_manager.devices[did].play_prev()

    async def stop(self, did="", arg1="", **kwargs):
        return await self.device_manager.devices[did].stop(arg1=arg1)

    async def pause(self, did="", **kwargs):
        """暂停（保留断点，可断点续播）"""
        return await self.device_manager.devices[did].pause()

    async def resume(self, did="", music_name="", list_name="", **kwargs):
        """从暂停处继续播放（music_name 是面板当前选中的歌）"""
        return await self.device_manager.devices[did].resume(
            music_name=music_name, list_name=list_name
        )

    async def stop_after_minute(self, did="", arg1=0, **kwargs):
        try:
            # 尝试阿拉伯数字转换中文数字
            minute = int(arg1)
        except (KeyError, ValueError):
            # 如果阿拉伯数字转换失败，尝试中文数字
            minute = chinese_to_number(str(arg1))
        return await self.device_manager.devices[did].stop_after_minute(minute)

    # 播放一个 url
    async def play_url(self, did="", arg1="", **kwargs):
        self.log.info(f"手动推送链接：{arg1}")
        url = arg1
        return await self.device_manager.devices[did].group_player_play(url)

    async def reset_timer_when_answer(self, answer_length, did):
        await self.device_manager.devices[did].reset_timer_when_answer(answer_length)

    async def check_replay(self, did):
        return await self.device_manager.devices[did].check_replay()

    # ==================== 播放列表 ====================
    # 口令:播放歌单
    async def play_music_list(self, did="", arg1="", **kwargs):
        parts = arg1.split("|")
        list_name = parts[0]

        music_name = ""
        if len(parts) > 1:
            music_name = parts[1]
        return await self.do_play_music_list(did, list_name, music_name)

    async def do_play_music_list(self, did, list_name, music_name=""):
        # 查找并获取真实的音乐列表名称
        list_name = self._find_real_music_list_name(list_name)
        # 检查音乐列表是否存在，如果不存在则进行语音提示并返回
        if list_name not in self.music_library.music_list:
            await self.do_tts(did, f"播放列表{list_name}不存在")
            return

        # 调用设备播放音乐列表的方法
        await self.device_manager.devices[did].play_music_list(list_name, music_name)

    # 口令:播放列表第
    async def play_music_list_index(self, did="", arg1="", **kwargs):
        patternarg = r"^([零一二三四五六七八九十百千万亿]+)个(.*)"
        # 匹配参数
        matcharg = re.match(patternarg, arg1)
        if not matcharg:
            return await self.play_music_list(did, arg1)

        chinese_index = matcharg.groups()[0]
        list_name = matcharg.groups()[1]
        list_name = self._find_real_music_list_name(list_name)
        if list_name not in self.music_library.music_list:
            await self.do_tts(did, f"播放列表{list_name}不存在")
            return

        index = chinese_to_number(chinese_index)
        play_list = self.music_library.music_list[list_name]
        if 0 <= index - 1 < len(play_list):
            music_name = play_list[index - 1]
            self.log.info(f"即将播放 ${arg1} 里的第 ${index} 个: ${music_name}")
            await self.device_manager.devices[did].play_music_list(
                list_name, music_name
            )
            return
        await self.do_tts(did, f"播放列表{list_name}中找不到第${index}个")

    def _find_real_music_list_name(self, list_name):
        """模糊搜索播放列表名称（委托给 music_library）"""
        return self.music_library.find_real_music_list_name(list_name)

    # 口令:选择第几个
    async def select_index(self, did="", arg1="", **kwargs):
        patternarg = r"^第?([零一二三四五六七八九十百千万亿]+)[个首条集]$"
        matcharg = re.match(patternarg, arg1)
        if not matcharg:
            return

        chinese_index = matcharg.groups()[0]
        index = chinese_to_number(chinese_index)

        device = self.device_manager.devices.get(did)
        if not device:
            self.log.warning(f"设备 did:{did} 不存在")
            return

        await device.handle_selection(index)

    # 更新每个设备的歌单
    def update_all_playlist(self):
        """更新每个设备的歌单"""
        for device in self.device_manager.devices.values():
            device.update_playlist()

    # 口令:刷新列表
    async def gen_music_list(self, **kwargs):
        self.music_library.gen_all_music_list()
        self.update_all_playlist()
        self.log.info("gen_music_list ok")

    # 口令:删除歌曲
    async def cmd_del_music(self, did="", arg1="", **kwargs):
        if not self.config.enable_cmd_del_music:
            await self.do_tts(did, "语音删除歌曲功能未开启")
            return
        self.log.info(f"cmd_del_music {arg1}")
        name = arg1
        if len(name) == 0:
            name = self.playingmusic(did)
        await self.del_music(name)

    async def del_music(self, name):
        filename = self.music_library.get_filename(name)
        if filename == "":
            self.log.info(f"${name} not exist")
            return
        try:
            os.remove(filename)
            self.log.info(f"del ${filename} success")
        except OSError:
            self.log.error(f"del ${filename} failed")
        # 重新生成音乐列表
        self.music_library.gen_all_music_list()
        self.update_all_playlist()

    # 口令:加入收藏,收藏歌曲
    async def add_to_favorites(self, did="", arg1="", **kwargs):
        name = arg1 if arg1 else self.playingmusic(did)
        self.log.info(f"add_to_favorites {name}")
        if not name:
            self.log.warning("当前没有在播放歌曲，添加歌曲到收藏列表失败")
            return

        self.music_library.play_list_add_music("收藏", [name])

    # 口令:取消收藏
    async def del_from_favorites(self, did="", arg1="", **kwargs):
        name = arg1 if arg1 else self.playingmusic(did)
        self.log.info(f"del_from_favorites {name}")
        if not name:
            self.log.warning("当前没有在播放歌曲，从收藏列表中移除失败")
            return

        self.music_library.play_list_del_music("收藏", [name])

    # ==================== 设备状态 / 音量 ====================
    # 获取音量
    async def get_volume(self, did="", **kwargs):
        return await self.device_manager.devices[did].get_volume()

    # 获取完整播放状态
    async def get_player_status(self, did="", **kwargs):
        return await self.device_manager.devices[did].get_player_status()

    # 设置音量
    async def set_volume(self, did="", arg1=0, **kwargs):
        if did not in self.device_manager.devices:
            self.log.info(f"设备 did:{did} 不存在, 不能设置音量")
            return
        volume = int(arg1)
        return await self.device_manager.devices[did].set_volume(volume)

    # 获取当前的播放列表
    def get_cur_play_list(self, did):
        if did not in self.device_manager.devices:
            return ""
        return self.device_manager.devices[did].get_cur_play_list()

    # 正在播放中的音乐
    def playingmusic(self, did):
        if did not in self.device_manager.devices:
            return ""
        cur_music = self.device_manager.devices[did].get_cur_music()
        self.log.debug(f"playingmusic. cur_music:{cur_music}")
        return cur_music

    def get_offset_duration(self, did):
        if did not in self.device_manager.devices:
            return 0, 0
        return self.device_manager.devices[did].get_offset_duration()

    # 当前是否正在播放歌曲
    def isplaying(self, did):
        if did not in self.device_manager.devices:
            return False
        return self.device_manager.devices[did].isplaying()

    # 播放状态（云端权威 + 本地意图兜底）
    def get_display_state(self, did):
        if did not in self.device_manager.devices:
            return False, None
        return self.device_manager.devices[did].get_display_state()

    def start_cloud_polling(self, did):
        if did in self.device_manager.devices:
            self.device_manager.devices[did].start_cloud_polling()

    def stop_cloud_polling(self, did):
        if did in self.device_manager.devices:
            self.device_manager.devices[did].stop_cloud_polling()

    # ==================== 配置 / 认证单层转发 ====================
    # 获取当前配置
    def getconfig(self):
        """获取当前配置（委托给 config_manager）"""
        return self.config_manager.get_config()

    # 把当前配置落地
    def save_cur_config(self):
        """把当前配置落地（委托给 config_manager）"""
        self.config_manager.save_cur_config(self.device_manager.devices)

    def get_cur_did(self):
        return self.auth_manager._cur_did
