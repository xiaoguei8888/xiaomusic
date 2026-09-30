#!/usr/bin/env python3
"""L3 services 门面：装配组件、生命周期与跨模块编排。

命令目标方法与单层转发已下沉到 xiaomusic/services/command_targets.py
（对齐 docs/architecture.md 的分层：门面只保留装配 + 编排），XiaoMusic
通过继承复用；commands.COMMAND_NAMES 的 18 条命令仍是本类上可 callable 的属性。
"""

import asyncio
import logging
import os
from logging.handlers import RotatingFileHandler

from xiaomusic import __version__
from xiaomusic.auth import AuthManager
from xiaomusic.command_handler import CommandHandler
from xiaomusic.config import Config
from xiaomusic.config_manager import ConfigManager
from xiaomusic.conversation import ConversationPoller
from xiaomusic.device_manager import DeviceManager
from xiaomusic.events import CONFIG_CHANGED, DEVICE_CONFIG_CHANGED, EventBus
from xiaomusic.music_library import MusicLibrary
from xiaomusic.services.command_targets import CommandTargets
from xiaomusic.utils.system_utils import (
    deepcopy_data_no_sensitive_info,
    try_add_access_control_param,
)


class XiaoMusic(CommandTargets):
    def __init__(self, config: Config):
        self.config = config

        # 初始化事件总线
        self.event_bus = EventBus()

        # 初始化认证管理器（延迟初始化部分属性）
        self.auth_manager = None

        # 初始化设备管理器（延迟初始化）
        self.device_manager = None

        self.running_task = []

        # 音乐库管理器（延迟初始化，在配置准备好之后）
        self.music_library = None

        # 命令处理器（延迟初始化，在配置准备好之后）
        self.command_handler = None

        # 配置管理器（延迟初始化）
        self.config_manager = None

        # 初始化配置
        self.init_config()

        # 初始化对话轮询器（延迟初始化，在配置和服务准备好之后）
        self.conversation_poller = None

        # 初始化日志
        self.setup_logger()

        # 初始化配置管理器（在日志准备好之后）
        self.config_manager = ConfigManager(
            config=self.config,
            log=self.log,
        )

        # 尝试从设置里加载配置
        config_data = self.config_manager.try_init_setting()
        if config_data:
            self.update_config_from_setting(config_data)

        # 初始化音乐库管理器（在配置准备好之后）
        self.music_library = MusicLibrary(
            config=self.config,
            log=self.log,
            event_bus=self.event_bus,
        )

        # 启动时重新生成一次播放列表
        self.music_library.gen_all_music_list()

        # 初始化设备管理器（在配置准备好之后）
        self.device_manager = DeviceManager(
            config=self.config,
            log=self.log,
            xiaomusic=self,
        )

        # 初始化认证管理器（在配置和设备管理器准备好之后）
        self.auth_manager = AuthManager(
            config=self.config,
            log=self.log,
            device_manager=self.device_manager,
        )

        # 初始化对话轮询器（在 device_id_did 准备好之后）
        self.conversation_poller = ConversationPoller(
            config=self.config,
            log=self.log,
            auth_manager=self.auth_manager,
            device_manager=self.device_manager,
        )

        # 初始化命令处理器（在所有依赖准备好之后）
        self.command_handler = CommandHandler(
            config=self.config,
            log=self.log,
            xiaomusic_instance=self,
        )

        # 订阅配置变更事件
        self.event_bus.subscribe(CONFIG_CHANGED, self.save_cur_config)
        self.event_bus.subscribe(DEVICE_CONFIG_CHANGED, self.save_cur_config)

        debug_config = deepcopy_data_no_sensitive_info(self.config)
        self.log.info(f"Startup OK. {debug_config}")

        if self.config.conf_path == self.config.music_path:
            self.log.warning("配置文件目录和音乐目录建议设置为不同的目录")

    def init_config(self):
        if not os.path.exists(self.config.music_path):
            os.makedirs(self.config.music_path)

        self.continue_play = self.config.continue_play

    def setup_logger(self):
        log_format = f"%(asctime)s [{__version__}] [%(levelname)s] %(filename)s:%(lineno)d: %(message)s"
        date_format = "[%Y-%m-%d %H:%M:%S]"
        formatter = logging.Formatter(fmt=log_format, datefmt=date_format)

        self.log = logging.getLogger("xiaomusic")
        self.log.handlers.clear()  # 清除已有的 handlers
        self.log.setLevel(logging.DEBUG if self.config.verbose else logging.INFO)

        # 文件日志处理器
        log_file = self.config.log_file
        log_path = os.path.dirname(log_file)
        if log_path and not os.path.exists(log_path):
            os.makedirs(log_path)
        if os.path.exists(log_file):
            try:
                os.remove(log_file)
            except Exception as e:
                print(f"无法删除旧日志文件: {log_file} {e}")

        file_handler = RotatingFileHandler(
            self.config.log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=1,
            encoding="utf-8",
        )
        file_handler.stream.flush()
        file_handler.setFormatter(formatter)
        self.log.addHandler(file_handler)

        # 控制台日志处理器
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        self.log.addHandler(console_handler)

    # ==================== 生命周期 ====================
    async def auto_refresh_token_task(self):
        while True:
            await asyncio.sleep(self.config.token_refresh_sec)
            try:
                await self.auth_manager.refresh_token()
            except Exception as e:
                self.log.warning(f"auto_refresh_token_task failed: {e}")

    async def run_forever(self):
        self.log.info("run_forever start")
        self.music_library.try_gen_all_music_tag()  # 事件循环开始后调用一次
        if self.config.token_refresh_sec > 0:
            self.token_refresh_task = asyncio.create_task(
                self.auto_refresh_token_task()
            )
        await self.auth_manager.init_all_data()
        # 启动对话循环，传递回调函数
        await self.conversation_poller.run_conversation_loop(
            self.do_check_cmd, self.reset_timer_when_answer
        )

    # 重新初始化
    async def reinit(self):
        for handler in self.log.handlers:
            handler.close()
        self.setup_logger()
        await self.auth_manager.init_all_data()
        self.music_library.gen_all_music_list()
        self.update_all_playlist()

        debug_config = deepcopy_data_no_sensitive_info(self.config)
        self.log.info(f"reinit success. data:{debug_config}")

    # 保存配置并重新启动
    async def saveconfig(self, data):
        """保存配置并重新启动"""
        # 更新配置
        self.update_config_from_setting(data)
        # 配置文件落地
        self.save_cur_config()
        # 重新初始化
        await self.reinit()

    def update_config_from_setting(self, data):
        """从设置更新配置"""
        # 委托给 config_manager 更新配置
        self.config_manager.update_config(data)

        # 重新初始化配置相关的属性
        self.init_config()

        debug_config = deepcopy_data_no_sensitive_info(self.config)
        self.log.info(f"update_config_from_setting ok. data:{debug_config}")

        joined_keywords = "/".join(self.config.key_match_order)
        self.log.info(f"语音控制已启动, 用【{joined_keywords}】开头来控制")
        self.log.debug(f"key_word_dict: {self.config.key_word_dict}")

    # ==================== 命令入口 / 任务监管 ====================
    # 匹配命令
    async def do_check_cmd(self, did="", query="", ctrl_panel=True, **kwargs):
        """检查并执行命令（委托给 command_handler）"""
        return await self.command_handler.do_check_cmd(did, query, ctrl_panel, **kwargs)

    def append_running_task(self, task):
        self.running_task.append(task)

    async def cancel_all_tasks(self):
        if len(self.running_task) == 0:
            self.log.info("cancel_all_tasks no task")
            return
        for task in self.running_task:
            self.log.info(f"cancel_all_tasks {task}")
            task.cancel()
        await asyncio.gather(*self.running_task, return_exceptions=True)
        self.running_task = []

    async def is_task_finish(self):
        if len(self.running_task) == 0:
            return True
        task = self.running_task[0]
        if task and task.done():
            return True
        return False

    def did_exist(self, did):
        # device_manager.devices 登录后才填充，未登录时回退到配置文件里的设备
        return did in self.device_manager.devices or did in (self.config.devices or {})

    # ==================== 跨模块编排 ====================
    # 获取所有设备
    async def getalldevices(self, **kwargs):
        device_list = []
        try:
            if self.auth_manager.mina_service is None:
                self.log.warning("[DEVICE] mina_service 为空，尝试强制恢复登录")
                await self.auth_manager.init_all_data(force_login=True)
                if self.auth_manager.mina_service is None:
                    self.log.warning("[DEVICE] 恢复登录后 mina_service 仍为空")
                    return device_list
            device_list = await self.auth_manager.mina_service.device_list()
        except Exception as e:
            self.log.warning(f"[DEVICE] getalldevices 异常: {e}")
            self.log.info("[DEVICE] 强制重新登录后重试获取设备列表")
            await self.auth_manager.init_all_data(force_login=True)
            if self.auth_manager.mina_service is not None:
                try:
                    device_list = await self.auth_manager.mina_service.device_list()
                    self.log.info(
                        f"[DEVICE] 恢复成功，获取到 {len(device_list)} 个设备"
                    )
                except Exception as e2:
                    self.log.warning(f"[DEVICE] 恢复后重试仍然失败: {e2}")
        return device_list

    async def debug_play_by_music_url(self, arg1=None):
        if arg1 is None:
            arg1 = {}
        data = arg1
        device_id = self.config.get_one_device_id()
        self.log.info(f"debug_play_by_music_url: {data} {device_id}")
        return await self.auth_manager.mina_service.ubus_request(
            device_id,
            "player_play_music",
            "mediaplayer",
            data,
        )

    async def do_tts(self, did, value):
        return await self.device_manager.devices[did].do_tts(value)

    async def handle_fatal_error(self, did, tts_msg="小music发生错误，请重试。"):
        """全局异常报错处理：支持 TTS 或 xiaomusic_error.mp3 音效"""
        # 1. 如果开启了 TTS，优先使用语音交互
        if getattr(self.config, "edge_tts_voice", "disable") != "disable":
            self.log.info(f"触发全局 TTS 报错: {tts_msg}")
            await self.do_tts(did, tts_msg)
            return

        # 2. 如果关闭了 TTS，触发 error.mp3 报错音效
        self.log.info("TTS 已关闭，触发全局 xiaomusic_error.mp3 报错音效")
        error_url = try_add_access_control_param(
            self.config,
            f"{self.config.hostname}:{self.config.public_port}/static/xiaomusic_error.mp3",
        )
        # 播放报错音效
        await self.play_url(did, error_url)
        # 在第 3 秒时提前下发 stop，把硬件断流的咔声藏在静音里
        await asyncio.sleep(3)
        # 停止
        await self.stop(did, "notts")
