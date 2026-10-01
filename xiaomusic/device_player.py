"""设备播放控制模块

负责单个设备的播放控制、TTS处理等功能。
"""

import asyncio
import copy
import json
import random
import time
from typing import TYPE_CHECKING

from miservice import miio_command

from xiaomusic.config import Device

if TYPE_CHECKING:
    from xiaomusic.xiaomusic import XiaoMusic
from xiaomusic.const import (
    NEED_USE_PLAY_MUSIC_API,
    PLAY_TYPE_ALL,
    PLAY_TYPE_ONE,
    PLAY_TYPE_RND,
    PLAY_TYPE_SEQ,
    PLAY_TYPE_SIN,
    TTS_COMMAND,
)
from xiaomusic.device_state import DeviceStateStore
from xiaomusic.events import DEVICE_CONFIG_CHANGED
from xiaomusic.utils.text_utils import (
    custom_sort_key,
    list2str,
    parse_ordinal_suffix,
)


def _parse_player_status(playing_info) -> dict:
    """解析 player_get_status 的响应。

    音箱返回的字段在顶层（status/volume/loop_type/play_song_detail 等），
    部分固件/接口版本会把同样的内容再塞进 data.info 的 JSON 字符串里。
    统一为"顶层优先、info 兜底"，避免像旧实现那样只读 info 而拿到 0。
    """
    if not isinstance(playing_info, dict):
        return {"volume": 0, "status": 0}
    info = dict(playing_info)
    nested = playing_info.get("data", {})
    if isinstance(nested, dict) and nested.get("info"):
        try:
            nested_info = json.loads(nested["info"])
        except (TypeError, ValueError):
            nested_info = None
        if isinstance(nested_info, dict):
            merged = dict(nested_info)
            merged.update({k: v for k, v in playing_info.items() if k != "data"})
            info = merged
    return info


def _extract_volume(playing_info) -> int:
    """从 player_get_status 响应里取音量（顶层优先）。"""
    try:
        return int(_parse_player_status(playing_info).get("volume", 0) or 0)
    except (TypeError, ValueError):
        return 0


class XiaoMusicDevice:
    """设备播放控制类

    负责单个小爱设备的播放控制，包括：
    - 播放控制（播放、暂停、上一首、下一首）
    - 播放列表管理
    - TTS（文字转语音）
    - 定时器管理
    - 设备状态管理
    """

    def __init__(self, xiaomusic: "XiaoMusic", device: Device, group_name: str):
        """初始化设备播放控制器

        Args:
            xiaomusic: XiaoMusic 主类实例
            device: 设备配置对象
            group_name: 设备组名
        """
        self.group_name = group_name
        self.device = device
        self.config = xiaomusic.config
        self.device_id = device.device_id
        self.log = xiaomusic.log
        self.xiaomusic = xiaomusic
        self.auth_manager = xiaomusic.auth_manager
        self.ffmpeg_location = self.config.ffmpeg_location
        self.event_bus = getattr(xiaomusic, "event_bus", None)

        self._next_timer = None
        self.state = DeviceStateStore(device.did, self.event_bus, device)
        # 播放进度
        self._start_time = 0
        self._duration = 0
        self._paused_time = 0
        self._play_failed_cnt = 0

        # 云端播放状态快照（权威来源，与上方本地意图 is_playing 区分）
        self._cloud_snapshot = None
        self._cloud_snapshot_at = 0.0
        self._poll_task = None
        self._ws_subscribers = 0
        self._local_play_at = 0.0
        # 用户暂停：断点保留在音箱侧（云端 status=2、位置冻结），可断点续播
        self._paused = False
        self._paused_remain = 0.0
        self._local_pause_at = 0.0

        self._play_list = []

        # 关机定时器
        self._stop_timer = None
        self._last_cmd = None
        self._pending_selection = None
        self._pending_selection_count = 0
        self.update_playlist()

        # TTS 播放定时器
        self._tts_timer = None
        # 用于预缓存下一首的定时器
        self._prefetch_timer = None

    @property
    def did(self):
        """获取设备DID"""
        return self.device.did

    @property
    def hardware(self):
        """获取设备硬件型号"""
        return self.device.hardware

    @property
    def is_playing(self):
        """本地播放意图（唯一源在 DeviceStateStore）"""
        return self.state.is_playing

    @is_playing.setter
    def is_playing(self, value):
        self.state.set_playing(value)

    def get_cur_music(self):
        """获取当前播放的音乐名称"""
        return self.state.cur_music

    def get_offset_duration(self):
        """获取播放偏移量和总时长（秒）

        优先用云端快照并在两次轮询之间做线性插值；无快照时回退到本地秒表
        （仅本机播放 / 云端不可用时才会走到回退分支）。
        """
        snap = self._cloud_snapshot
        if snap and snap.get("_ok"):
            duration = snap.get("duration", 0)
            if snap.get("status") == 1:
                elapsed = time.time() - self._cloud_snapshot_at
                offset = snap.get("position", 0) + elapsed
                if duration > 0:
                    offset = min(offset, duration)
                return max(0.0, offset), duration
            if snap.get("status") == 2:
                # 暂停：云端位置已冻结，进度条停在断点，不能跳回 0
                offset = snap.get("position", 0)
                if duration > 0:
                    offset = min(offset, duration)
                return max(0.0, offset), duration
            return 0, duration

        duration = self._duration
        if not self.is_playing:
            return 0, duration
        offset = time.time() - self._start_time - self._paused_time
        return offset, duration

    async def get_cloud_status(self):
        """从云端拉取一次播放状态，归一化为秒并缓存快照"""
        if self.auth_manager.mina_service is None:
            return None
        try:
            raw = await self.auth_manager.mina_service.player_get_status(self.device_id)
        except Exception as e:
            self.log.warning(f"get_cloud_status 请求失败: {e}")
            return None

        # 以「原始响应」判定有效性：空响应 / 纯 code 响应不能伪造成「已停止」，
        # 否则 UI 会拿到一个 _ok=True 的假快照（真机曾出现 status 恒为 0）。
        # 注意不能用 _parse_player_status 的结果判断——它为 None 兜底成 {volume:0,status:0}。
        raw_fields = raw if isinstance(raw, dict) else {}
        if not {"status", "volume", "track_list"} & set(raw_fields):
            self.log.warning(f"get_cloud_status 云端返回异常: {raw!r}")
            return None
        info = _parse_player_status(raw)

        detail = info.get("play_song_detail") or {}
        snapshot = {
            "_ok": True,
            "status": info.get("status", 0),
            "volume": info.get("volume", 0),
            "loop_type": info.get("loop_type"),
            "position": round(detail.get("position", 0) / 1000.0, 1),
            "duration": round(detail.get("duration", 0) / 1000.0, 1),
            "audio_id": detail.get("audio_id"),
            "track_list": info.get("track_list") or [],
        }
        self._cloud_snapshot = snapshot
        self._cloud_snapshot_at = time.time()
        return snapshot

    async def _poll_cloud_status(self):
        """WS 有订阅者时轮询云端快照；连续失败按指数退避，避免风控期刷屏"""
        interval = 3.0
        try:
            while self._ws_subscribers > 0:
                ok = await self.get_cloud_status()
                if ok:
                    interval = 3.0
                else:
                    interval = min(interval * 2, 60.0)
                    self.log.debug(f"_poll_cloud_status 失败，退避 {interval:.0f}s")
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.log.warning(f"_poll_cloud_status 异常退出: {e}")

    def start_cloud_polling(self):
        """WS 连接数 +1；首个订阅者启动轮询任务"""
        self._ws_subscribers += 1
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._poll_cloud_status())

    def stop_cloud_polling(self):
        """WS 连接数 -1；最后一个订阅者离开时停止轮询"""
        self._ws_subscribers = max(0, self._ws_subscribers - 1)
        if self._ws_subscribers == 0 and self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            self._poll_task = None

    # 本地播放/暂停动作后等待云端确认的最长时间（云端 status 有几秒延迟）
    INTENT_CONFIRM_SEC = 10

    def get_display_state(self):
        """供 UI 使用的状态：最近一次本地动作优先，直到云端确认

        云端 status 有几秒延迟：点暂停后云端还报 status=1，点续播后云端还报
        status=2（快照每 3 秒才刷新一次）。这段时间以本地意图为准，否则按钮
        点完 UI 会闪回旧状态，用户会以为没点上而重复点、把歌重头播。
        超过 INTENT_CONFIRM_SEC 云端仍未确认时，以云端为准。
        """
        snap = self._cloud_snapshot
        if not (snap and snap.get("_ok")):
            return self.is_playing, None
        cloud_playing = snap.get("status") == 1

        if self._local_play_at > self._local_pause_at:
            if (
                cloud_playing
                or (time.time() - self._local_play_at) < self.INTENT_CONFIRM_SEC
            ):
                return True, snap
            return False, snap

        if self._local_pause_at > self._local_play_at:
            if (
                not cloud_playing
                or (time.time() - self._local_pause_at) < self.INTENT_CONFIRM_SEC
            ):
                return False, snap
            return True, snap

        return cloud_playing, snap

    def isplaying(self):
        """UI 显示的播放状态（云端优先）；内部逻辑仍用 self.is_playing"""
        return self.get_display_state()[0]

    async def play_music(self, name):
        """播放音乐（外部接口）"""
        return await self._playmusic(name)

    def update_playlist(self, force_reshuffle=False):
        """
        初始化或更新播放列表。

        【核心架构特点】：
        1. 状态保持 (Stateful Shuffle)：随机模式下，生成一次乱序列表后永久保持，避免反复洗牌导致预缓存断链和歌曲无限循环。
        2. 洗牌置顶 (Pin-to-Top)：当发生全量重洗时，将当前正在播放的歌曲强行“钉”在列表最顶端(index 0)，完美闭环预缓存机制。
        3. 增量更新 (Incremental Update)：歌单发生变化（如自动追加了歌手新歌）时，不打乱原有播放顺序，仅将新歌洗牌后追加到队尾。

        Args:
            force_reshuffle (bool): 是否强制彻底重新洗牌（用于切换模式、切换歌单、或一轮播放触底时）
        """
        # 1. 兜底保护：如果没有重置 list 且当前歌单在系统里不存在，默认切到"全部"
        if self.device.cur_playlist not in self.xiaomusic.music_library.music_list:
            self.device.cur_playlist = "全部"

        list_name = self.device.cur_playlist
        # 获取大管家（Library）里最新鲜的歌单数据
        latest_list = self.xiaomusic.music_library.music_list[list_name]

        # ==========================================
        # 随机播放模式 (PLAY_TYPE_RND) 的调度
        # ==========================================
        if self.device.play_type == PLAY_TYPE_RND:
            # 判断是否需要【全量重洗牌】的三个条件：
            # A. 外部明确要求强洗 (force_reshuffle=True)
            # B. 当前播放列表是空的 (系统刚启动)
            # C. 当前播放列表和最新的歌单毫无交集 (说明用户切了全新的歌单)
            if (
                force_reshuffle
                or not self._play_list
                or not set(self._play_list).intersection(set(latest_list))
            ):
                self._play_list = copy.copy(latest_list)
                random.shuffle(self._play_list)

                # 2：洗牌置顶 (Pin-to-Top)
                # 防止洗牌后当前歌曲位置丢失，导致下一首乱跳和预缓存错位
                cur_music = self.get_cur_music()
                if cur_music and cur_music in self._play_list:
                    self._play_list.remove(cur_music)
                    self._play_list.insert(0, cur_music)

                self.log.info(f"彻底重新洗牌 {list_name}，并将当前歌曲置顶")

            # 【增量更新牌库】
            else:
                # 3：增量更新 (Incremental Update)
                old_list = self._play_list
                # A. 剔除云端已经被删除的歌，保留依然存在的歌（绝对不改变它们的相对顺序！）
                self._play_list = [s for s in old_list if s in latest_list]

                # B. 找出最新歌单里多出来的新歌（比如刷新列表后新增进来的）
                new_songs = [s for s in latest_list if s not in old_list]
                if new_songs:
                    # 把新来的歌单独洗乱，然后悄悄垫在牌堆的最底下
                    random.shuffle(new_songs)
                    self._play_list.extend(new_songs)
                    self.log.info(
                        f"歌单有更新，保持原顺序并追加了 {len(new_songs)} 首新歌"
                    )

        # ==========================================
        # 顺序/循环模式的处理
        # ==========================================
        else:
            self._play_list = copy.copy(latest_list)

            # 本地目录歌单，列表都是纯字符串时执行本地特定的字母自然排序
            if len(self._play_list) > 0:
                has_non_str_item = any(
                    not isinstance(item, str) for item in self._play_list
                )
                if not has_non_str_item:
                    self._play_list.sort(key=custom_sort_key)
            self.log.info(f"顺序模式更新，不打乱 {list_name}")

    async def play(self, name="", search_key=""):
        """播放歌曲（外部接口）"""
        self._last_cmd = "play"
        return await self._play(name=name, search_key=search_key)

    async def _play_internal(self, name="", search_key=""):
        """播放歌曲的内部统一实现

        Args:
            name: 歌曲名称
            search_key: 搜索关键词
        """
        # 清除旧的待选择状态
        if self._pending_selection:
            self.log.info(f"清除旧的待选择状态，重新搜索: {name}")
            self._pending_selection = None
            self._pending_selection_count = 0

        # 初始检查逻辑
        if not search_key and not name:
            if self.check_play_next():
                await self._play_next()
                return
            else:
                name = self.get_cur_music()

        self.log.info(f"play_internal. search_key:{search_key} name:{name}")

        if not name:
            self.log.info(f"没有歌曲播放了 name:{name} search_key:{search_key}")
            return

        max_results = self.config.fuzzy_match_max_results
        auto_index = None

        parsed_name, parsed_index = parse_ordinal_suffix(name)
        if parsed_index is not None:
            full_names = self.xiaomusic.music_library.find_real_music_name(
                name, n=max_results
            )
            if full_names:
                self.log.info(
                    f"完整名称'{name}'有{len(full_names)}条匹配，优先使用完整名称搜索"
                )
                names = full_names
            else:
                self.log.info(
                    f"完整名称'{name}'无匹配，使用'{parsed_name}'搜索并自动选择第{parsed_index}个"
                )
                name = parsed_name
                search_key = parsed_name
                auto_index = parsed_index
                names = self.xiaomusic.music_library.find_real_music_name(
                    name, n=max_results
                )
        else:
            names = self.xiaomusic.music_library.find_real_music_name(
                name, n=max_results
            )

        self.log.info(
            f"play_internal. 搜索关键词:{name} 匹配数量:{len(names)} auto_index:{auto_index}"
        )
        if len(names) > 1:
            for idx, music_name in enumerate(names, 1):
                self.log.info(f"  第{idx}个: {music_name}")

        if len(names) > 1:
            if auto_index is not None and 1 <= auto_index <= len(names):
                self._pending_selection = names
                self._pending_selection_count = len(names)
                self.log.info(f"自动选择第{auto_index}个: {names[auto_index - 1]}")
                await self.handle_selection(auto_index)
                return

            if not self.config.enable_multi_result_selection:
                action = self.config.multi_result_action
                if action == "first":
                    selected_index = 1
                else:
                    selected_index = random.randint(1, len(names))
                selected_name = names[selected_index - 1]
                self.log.info(
                    f"多结果选择已关闭，按'{action}'处理，选择第{selected_index}个: {selected_name}"
                )
                self._pending_selection = names
                self._pending_selection_count = len(names)
                await self._playmusic(selected_name)
                return

            self._pending_selection = names
            self._pending_selection_count = len(names)
            selection_text = (
                f"共找到{len(names)}条匹配记录，请重新呼叫小爱同学并告诉她第几个"
            )
            self.log.info(selection_text)
            await self.xiaomusic.do_tts(self.did, selection_text)
            return

        if not names:
            self.log.info(f"本地不存在歌曲{name}")
            await self.do_tts(f"本地不存在歌曲{name}")
            return

        name = names[0]
        if name not in self._play_list:
            # 根据当前歌曲匹配歌曲列表
            self.device.cur_playlist = self.find_cur_playlist(name)
            self.update_playlist()

        self.log.debug(
            f"当前播放列表为：{list2str(self._play_list, self.config.verbose)}"
        )
        # 本地存在歌曲，直接播放
        await self._playmusic(name)

    async def _play(self, name="", search_key=""):
        """播放歌曲（内部实现）"""
        return await self._play_internal(name=name, search_key=search_key)

    async def play_next(self):
        """播放下一首（外部接口）"""
        return await self._play_next()

    async def _play_next(self):
        """播放下一首（内部实现）"""
        self.log.info("开始播放下一首")
        name = self.get_cur_music()
        if (
            self.device.play_type == PLAY_TYPE_ALL
            or self.device.play_type == PLAY_TYPE_RND
            or self.device.play_type == PLAY_TYPE_SEQ
            or name == ""
            or (
                (name not in self._play_list) and self.device.play_type != PLAY_TYPE_ONE
            )
        ):
            name = self.get_next_music()
            self.log.info(f"get_next_music {name}")
        self.log.info(f"_play_next. name:{name}, cur_music:{self.get_cur_music()}")
        if name == "":
            self.log.info("本地没有歌曲")
            return
        await self._play(name)

    async def play_prev(self):
        """播放上一首（外部接口）"""
        return await self._play_prev()

    async def _play_prev(self):
        """播放上一首（内部实现）"""
        self.log.info("开始播放上一首")
        name = self.get_cur_music()
        if (
            self.device.play_type == PLAY_TYPE_ALL
            or self.device.play_type == PLAY_TYPE_RND
            or self.device.play_type == PLAY_TYPE_SEQ
            or name == ""
            or (name not in self._play_list)
        ):
            name = self.get_prev_music()
        self.log.info(f"_play_prev. name:{name}, cur_music:{self.get_cur_music()}")
        if name == "":
            await self.do_tts("本地没有歌曲")
            return
        await self._play(name)

    async def playlocal(self, name=""):
        """播放本地歌曲"""
        self._last_cmd = "playlocal"
        return await self._play_internal(name=name, search_key="")

    async def prefetch_next_song(self, sleep_sec):
        """延时后台预加载（缓存）下一首歌曲"""
        if self._prefetch_timer:
            self._prefetch_timer.cancel()

        async def _do_prefetch():
            try:
                await asyncio.sleep(sleep_sec)

                # 拿下一首歌的名字
                next_music = self.get_next_music()
                if not next_music:
                    return
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self.log.error(f"预加载下一首歌曲失败: {e}")

        self._prefetch_timer = asyncio.create_task(_do_prefetch())

    async def _playmusic(self, name):
        """播放音乐的核心实现"""
        # 取消组内所有的下一首歌曲的定时器
        await self.cancel_group_next_timer()

        # 新歌 = 新会话，之前的暂停断点作废
        self._paused = False
        self.is_playing = True
        self.state.set_track(name)
        self.device.playlist2music[self.device.cur_playlist] = name
        self._local_play_at = time.time()
        cur_playlist = self.device.cur_playlist
        self.log.info(f"cur_music {self.get_cur_music()}")

        # 获取该歌单下的播放 URL
        url = await self.xiaomusic.music_library.get_music_url(name, cur_playlist)

        # 1. 本地文件缺失（url 为空）时直接切歌
        if not url:
            self._play_failed_cnt = getattr(self, "_play_failed_cnt", 0) + 1
            self.log.warning(
                f"【{name}】命中了死链墓碑标记，立刻拦截跳过！连续失败次数: {self._play_failed_cnt}"
            )

            if self._play_failed_cnt >= 5:
                self.log.error("连续获取歌曲失败达到5次，触发系统第一层熔断保护！")
                self._play_failed_cnt = 0
                await self.xiaomusic.handle_fatal_error(
                    self.did, "连续多次获取歌曲失败，已为您停止播放。"
                )
            else:
                await self.set_next_music_timeout(0.5)
            return

        # 2. 真正安全的下发播放阶段

        # 4. 真正安全的下发播放阶段
        await self.group_force_stop_xiaoai()
        self.log.info(f"发送指令给小爱，开始播放: {url}")

        results = await self.group_player_play(url, name)
        if all(ele is None for ele in results):
            self._play_failed_cnt = getattr(self, "_play_failed_cnt", 0) + 1
            self.log.info(f"播放指令发送失败. 连续失败次数: {self._play_failed_cnt}")
            await asyncio.sleep(1)
            if (
                self.is_playing
                and self._last_cmd != "stop"
                and self._play_failed_cnt < 5
            ):
                await self._play_next()
            return

        self.log.info(f"【{name}】已经开始播放了")

        # 记录歌曲开始播放的时间
        self._start_time = time.time()
        self._paused_time = 0

        # 获取音频时长
        sec = await self.xiaomusic.music_library.get_music_duration(name, cur_playlist)
        self._duration = sec

        # 3. 时长质检阶段：资源无效时自动跳过
        if sec <= 0.1:
            self._play_failed_cnt = getattr(self, "_play_failed_cnt", 0) + 1
            self.log.warning(
                f"【{name}】资源无效(获取时长为 {sec})，触发自动跳过。连续失败次数: {self._play_failed_cnt}"
            )

            if self._play_failed_cnt >= 5:
                self.log.error("连续获取歌曲失败达到 5 次，触发第一层终极熔断保护！")
                self._play_failed_cnt = 0
                asyncio.ensure_future(
                    self.xiaomusic.handle_fatal_error(
                        self.did, "连续多次获取歌曲失败，已为您停止播放。"
                    )
                )
            else:
                await self.set_next_music_timeout(0.5)
            return

        # 只有发送指令成功 -> 质检出时长正常，才允许重置清零！
        self._play_failed_cnt = 0

        # 计算获取时长的执行耗时
        duration_execution_time = time.time() - self._start_time
        self.log.info(f"获取音乐时长耗时: {duration_execution_time:.3f} 秒")
        # 调整定时器时长，减去获取音乐时长的执行时间
        adjusted_sec = sec + self.config.delay_sec - duration_execution_time
        # 确保调整后的时长不会过小，最小保留0.1秒
        adjusted_sec = max(adjusted_sec, 0.1)
        self.log.info(
            f"原始歌曲时长: {sec:.3f} 秒, 调整后定时器时长: {adjusted_sec:.3f} 秒"
        )
        await self.set_next_music_timeout(adjusted_sec)
        # 发布设备配置变更事件
        if self.event_bus:
            self.event_bus.publish(DEVICE_CONFIG_CHANGED)

        # --- 🌟 新增：触发预缓存下一首 🌟 ---
        # 如果当前歌曲大于 2 秒，则在播放 20 秒后悄悄预取下一首歌
        if sec > 20:
            await self.prefetch_next_song(20)

    async def do_tts(self, value):
        """执行TTS（文字转语音）"""
        self.log.info(f"try do_tts value:{value}")
        if not value:
            self.log.info("do_tts no value")
            return

        # await self.group_force_stop_xiaoai()
        await self.text_to_speech(value)

        # 最大等8秒
        sec = min(8, int(len(value) / 3))
        await asyncio.sleep(sec)
        self.log.info(f"do_tts ok. cur_music:{self.get_cur_music()}")
        await self.check_replay()

    async def force_stop_xiaoai(self, device_id):
        """强制停止小爱播放

        只发 player_pause 是不够的：L17A 等机型 player_pause 返回 True 但
        播放状态不变，必须补一条 player_stop。因此这里 pause 与 stop 都发，
        并记录云端状态，便于日后排查"命令成功但没停下来"的情况。
        """
        try:
            ret = await self.auth_manager.mina_service.player_pause(device_id)
            self.log.info(
                f"force_stop_xiaoai player_pause device_id:{device_id} ret:{ret}"
            )
            await self.stop_if_xiaoai_is_playing(device_id, force=True)
        except Exception as e:
            self.log.warning(f"Execption {e}")

    async def get_if_xiaoai_is_playing(self):
        """检查小爱是否正在播放"""
        playing_info = await self.auth_manager.mina_service.player_get_status(
            self.device_id
        )
        self.log.info(playing_info)
        # 音箱把 status 放在顶层（详见 _parse_player_status）；旧实现只读 data.info
        # 导致恒为 -1，播放中也被判为未播放，player_stop 永远不会发出。
        is_playing = _parse_player_status(playing_info).get("status") == 1
        return is_playing

    async def stop_if_xiaoai_is_playing(self, device_id, force=False):
        """如果小爱正在播放则停止

        force=True 时无条件发送 player_stop（用于用户显式"停止/暂停"）：
        云端 status 有延迟或机型不播报状态时，靠它兜底。
        """
        is_playing = await self.get_if_xiaoai_is_playing()
        if force or is_playing or self.config.enable_force_stop:
            # stop it
            ret = await self.auth_manager.mina_service.player_stop(device_id)
            self.log.info(
                f"stop_if_xiaoai_is_playing player_stop device_id:{device_id} "
                f"is_playing:{is_playing} force:{force} ret:{ret}"
            )

    async def check_replay(self):
        """检查是否需要继续播放被打断的歌曲"""
        if self.is_playing:
            if not self.config.continue_play:
                # 重新播放歌曲
                self.log.info("现在重新播放歌曲")
                await self._play()
            else:
                self.log.info(
                    f"继续播放歌曲. self.config.continue_play:{self.config.continue_play}"
                )
        else:
            self.log.info(f"不会继续播放歌曲. isplaying:{self.is_playing}")

    def _pick_index(self, index, direction, play_list_len):
        """按播放模式算出候选下标；无候选返回 None。"""
        if play_list_len == 1:
            return index  # 当只有一首歌曲时保持当前索引不变
        if direction == "next":
            new_index = index + 1
            if self.device.play_type == PLAY_TYPE_SEQ and new_index >= play_list_len:
                self.log.info("顺序播放结束")
                return None
            if new_index >= play_list_len:
                if self.device.play_type == PLAY_TYPE_RND:
                    self.log.info("当前随机列表已播放一轮，触发重新洗牌！")
                    self.update_playlist(force_reshuffle=True)
                    # 洗完牌后，当前歌曲被强行置顶在了 0，下一首必定是 1
                    return 1
                return 0
            return new_index
        if direction == "prev":
            new_index = index - 1
            return play_list_len - 1 if new_index < 0 else new_index
        self.log.error("无效的方向参数")
        return None

    def get_music(self, direction="next"):
        """获取下一首或上一首音乐。

        历史上这里是"发现文件不存在就 pop 掉再递归"，但递归开头的
        update_playlist() 会把 pop 掉的曲目重新加回歌单，导致文件缺失时
        递归不收敛（RecursionError）。改为在本地副本上迭代扫描：
        - 候选文件不存在则跳过并记录，不修改 self._play_list；
        - 扫完一轮仍无可用曲目则返回空串，由调用方决定后续行为。
        """
        self.update_playlist()
        play_list_len = len(self._play_list)
        if play_list_len == 0:
            self.log.warning("当前播放列表没有歌曲")
            return ""

        index = 0
        try:
            index = self._play_list.index(self.get_cur_music())
        except ValueError:
            pass

        candidates = self._play_list[:]
        skipped = set()
        attempts = 0
        while candidates and attempts <= len(candidates) + 1:
            attempts += 1
            new_index = self._pick_index(index, direction, len(candidates))
            if new_index is None:
                break
            # 随机播放到底会触发洗牌（_pick_index 内部调用 update_playlist），
            # 此时候选列表要与新的 self._play_list 同步，否则会取到洗牌前的旧下标。
            # 注意：只做一次同步，且同步后重新计算下标；不能每轮无条件同步，
            # 否则 pop 掉的缺失曲目会被 self._play_list 重新带回来（死循环）。
            if self._play_list != candidates and not skipped:
                candidates = self._play_list[:]
                if not candidates:
                    break
                new_index = min(new_index, len(candidates) - 1)
            name = candidates[new_index]
            if self.xiaomusic.music_library.is_music_exist(name):
                if skipped:
                    self.log.info(f"跳过不存在的歌曲: {sorted(skipped)}")
                return name
            skipped.add(name)
            self.log.info(f"skip not exist music: {name}")
            candidates.pop(new_index)
            if not candidates:
                break
            # 从被删位置继续按同方向找，保持"下一首"的语义
            index = new_index - 1 if direction == "next" else 0
            if index < 0:
                index = len(candidates) - 1

        if skipped:
            self.log.warning(f"播放列表内没有可用歌曲，已跳过: {sorted(skipped)}")
        return ""

    def get_next_music(self):
        """获取下一首音乐"""
        return self.get_music(direction="next")

    def get_prev_music(self):
        """获取上一首音乐"""
        return self.get_music(direction="prev")

    def check_play_next(self):
        """判断是否需要播放下一首歌曲"""
        # 当前歌曲不在当前播放列表
        if self.get_cur_music() not in self._play_list:
            self.log.info(f"当前歌曲 {self.get_cur_music()} 不在当前播放列表")
            return True

        # 当前没我在播放的歌曲
        if self.get_cur_music() == "":
            self.log.info("当前没我在播放的歌曲")
            return True
        else:
            # 当前播放的歌曲不存在了
            if not self.xiaomusic.music_library.is_music_exist(self.get_cur_music()):
                self.log.info(f"当前播放的歌曲 {self.get_cur_music()} 不存在了")
                return True
        return False

    async def text_to_speech(self, value):
        """文字转语音"""
        try:
            # 有 tts command 优先使用 tts command 说话
            if self.hardware in TTS_COMMAND:
                tts_cmd = TTS_COMMAND[self.hardware]
                self.log.info("Call MiIOService tts.")
                value = value.replace(" ", ",")  # 不能有空格
                await miio_command(
                    self.auth_manager.miio_service,
                    self.did,
                    f"{tts_cmd} {value}",
                )
            else:
                self.log.debug("Call MiNAService tts.")
                await self.auth_manager.mina_service.text_to_speech(
                    self.device_id, value
                )
        except Exception as e:
            self.log.exception(f"Execption {e}")

    async def group_player_play(self, url, name=""):
        """同一组设备播放"""
        device_id_list = self.xiaomusic.device_manager.get_group_device_id_list(
            self.group_name
        )
        tasks = [
            self.play_one_url(device_id, url, name) for device_id in device_id_list
        ]
        results = await asyncio.gather(*tasks)
        self.log.info(f"group_player_play {url} {device_id_list} {results}")
        return results

    async def play_one_url(self, device_id, url, name):
        """在单个设备上播放URL"""
        ret = None
        try:
            audio_id = await self._get_audio_id(name)
            if self.config.continue_play:
                ret = await self.auth_manager.mina_service.play_by_music_url(
                    device_id, url, _type=1, audio_id=audio_id
                )
                self.log.info(
                    f"play_one_url continue_play device_id:{device_id} ret:{ret} url:{url} audio_id:{audio_id}"
                )
            elif self.hardware in NEED_USE_PLAY_MUSIC_API:
                ret = await self.auth_manager.mina_service.play_by_music_url(
                    device_id, url, audio_id=audio_id
                )
                self.log.info(
                    f"play_one_url play_by_music_url device_id:{device_id} ret:{ret} url:{url} audio_id:{audio_id}"
                )
            else:
                ret = await self.auth_manager.mina_service.play_by_url(device_id, url)
                self.log.info(
                    f"play_one_url play_by_url device_id:{device_id} ret:{ret} url:{url}"
                )
        except Exception as e:
            self.log.exception(f"Execption {e}")
        return ret

    async def _get_audio_id(self, name):
        """获取音频ID"""
        audio_id = "1582971365183456177"
        if not self.config.continue_play:
            return str(audio_id)

        # 如果 name 为空（如播放 TTS 时），坚决不请求小米接口，会导致小米账号报错。
        name = name.strip() if name else ""
        if not name:
            self.log.debug(
                "歌名为空(可能是TTS播报)，直接使用默认 audio_id，跳过小米接口查询。"
            )
            return str(audio_id)
        # 修复结束

        try:
            params = {
                "query": name,
                "queryType": 1,
                "offset": 0,
                "count": 6,
                "timestamp": int(time.time_ns() / 1000),
            }
            response = await self.auth_manager.mina_service.mina_request(
                "/music/search", params
            )
            song_list = response.get("data", {}).get("songList", [])

            if song_list:
                # 先默认拿匹配到的第一首的id垫底（容错兜底）
                audio_id = song_list[0].get("audioID")
                # 把传进来的 "歌名-歌手" 拆开
                target_song = name
                target_artist = ""
                if "-" in name:
                    parts = name.split("-", 1)
                    target_song = parts[0].strip()
                    target_artist = parts[1].strip()
                # 歌手如果有多个只取第一个去匹配
                first_artist = target_artist
                if first_artist:
                    for sep in [";", "；", ",", "，", "&", "、", "/"]:
                        first_artist = first_artist.replace(sep, "|")
                    first_artist = first_artist.split("|")[0].strip()
                # 歌名完全相等，歌手 in 包含
                for song in song_list:
                    s_name = song.get("name", "")
                    s_artist = song.get("artist", {}).get("name", "")
                    if target_song.lower() == s_name.lower():
                        if not first_artist or first_artist.lower() in s_artist.lower():
                            audio_id = song.get("audioID")
                            break

            self.log.debug(f"_get_audio_id. name: {name} 最终使用的 songId:{audio_id}")

        except Exception as e:
            self.log.error(f"_get_audio_id 获取失败: {e}")

        return str(audio_id)

    async def reset_timer_when_answer(self, answer_length):
        """重置计时器（当小爱回答时）"""
        if not (self.is_playing and self.config.continue_play):
            return
        pause_time = answer_length / 5 + 1
        offset, duration = self.get_offset_duration()
        self._paused_time += pause_time
        new_time = duration - offset + pause_time
        await self.set_next_music_timeout(new_time)
        self.log.info(
            f"reset_timer 延长定时器. answer_length:{answer_length} pause_time:{pause_time}"
        )

    async def set_next_music_timeout(self, sec):
        """设置下一首歌曲的播放定时器"""
        await self.cancel_next_timer()

        async def _do_next():
            await asyncio.sleep(sec)
            try:
                self.log.info(f"定时器时间到了 did: {self.did}")
                current_timer = self._next_timer
                if current_timer:
                    # 取消任务（防止任务被重复触发，即使sleep已结束）
                    current_timer.cancel()
                    try:
                        await current_timer  # 等待任务取消完成，避免警告
                    except asyncio.CancelledError:
                        pass
                    # 再置空引用
                    self._next_timer = None
                    if self.device.play_type == PLAY_TYPE_SIN:
                        self.log.info(f"单曲播放不继续播放下一首 did: {self.did}")
                        await self.stop(arg1="notts")
                    else:
                        await self._play_next()
                else:
                    self.log.info(f"定时器时间到了但是不见了 did: {self.did}")
                    await self.stop(arg1="notts")

            except Exception as e:
                self.log.error(f"Execption {e}")

        self._next_timer = asyncio.create_task(_do_next())
        self.log.info(f"{sec} 秒后将会播放下一首歌曲 did: {self.did}")

    async def set_volume(self, volume: int):
        """设置音量"""
        self.log.info(f"set_volume.  did: {self.did} volume: {volume}")
        try:
            await self.auth_manager.mina_service.player_set_volume(
                self.device_id, volume
            )
        except Exception as e:
            self.log.exception(f"Execption {e}")

    async def get_volume(self):
        """获取音量"""
        volume = 0
        try:
            playing_info = await self.auth_manager.mina_service.player_get_status(
                self.device_id
            )
            self.log.info(f"get_volume. playing_info:{playing_info}")
            volume = _extract_volume(playing_info)
        except Exception as e:
            self.log.warning(f"Execption {e}")
        volume = int(volume)
        self.log.info("get_volume. volume:%d", volume)
        return volume

    async def get_player_status(self):
        """获取完整播放状态"""
        try:
            playing_info = await self.auth_manager.mina_service.player_get_status(
                self.device_id
            )
            self.log.info(f"get_player_status. playing_info:{playing_info}")
            return _parse_player_status(playing_info)
        except Exception as e:
            self.log.warning(f"Execption {e}")
        return {"volume": 0, "status": 0}

    async def set_play_type(self, play_type, dotts=True):
        """设置播放类型"""
        self.device.play_type = play_type
        # 发布设备配置变更事件
        if self.event_bus:
            self.event_bus.publish(DEVICE_CONFIG_CHANGED)
        await self._sync_cloud_loop(play_type)
        if dotts:
            tts = self.config.get_play_type_tts(play_type)
            await self.do_tts(tts)
        self.update_playlist()
        # 切换模式，强制重新洗牌
        self.update_playlist(force_reshuffle=True)

    async def _sync_cloud_loop(self, play_type):
        """把应用内 5 种播放模式映射到音箱的 loop_type

        云端仅有 0=单曲、1=列表 两种循环状态，无法表达全部 5 种模式，
        因此只把「单曲循环」映射为 0，其余一律为 1（列表），
        应用内的模式语义仍以 device.play_type 为准。
        """
        if self.auth_manager.mina_service is None:
            return
        loop_type = 0 if play_type == PLAY_TYPE_ONE else 1
        try:
            await self.auth_manager.mina_service.player_set_loop(
                self.device_id, loop_type
            )
        except Exception as e:
            self.log.warning(f"_sync_cloud_loop 失败: {e}")

    async def play_music_list(self, list_name, music_name):
        """播放指定播放列表"""
        self._last_cmd = "play_music_list"
        self.device.cur_playlist = list_name
        # 切换歌单，强制重新洗牌
        self.update_playlist(force_reshuffle=True)
        if not music_name:
            music_name = self.device.playlist2music.get(list_name, "")
        self.log.info(f"开始播放列表{list_name} {music_name}")
        await self._play(music_name)

    async def stop(self, arg1=""):
        """停止播放"""
        self._last_cmd = "stop"
        # 停止会结束播放会话，暂停断点随之作废（续播只能从头开始）
        self._paused = False
        self.is_playing = False
        if arg1 != "notts":
            await self.do_tts(self.config.stop_tts_msg)
            await asyncio.sleep(3)  # 等它说完
        # 取消组内所有的下一首歌曲的定时器
        await self.cancel_group_next_timer()
        await self.group_force_stop_xiaoai()
        self.log.info("stop now")

    async def pause(self):
        """暂停播放（保留断点，可断点续播）

        与 stop 的区别：stop 结束会话，再播放只能从头开始；player_pause 会让
        云端 status=2、播放位置冻结在断点，resume 时 player_play 从断点继续。
        暂停期间必须取消下一首定时器，否则到点会自己播下一首。
        """
        if self.auth_manager.mina_service is None:
            return False
        offset, duration = self.get_offset_duration()
        if duration <= 0:
            duration = self._duration
        self._paused_remain = max(duration - offset, 0.1)
        try:
            ret = await self.auth_manager.mina_service.player_pause(self.device_id)
        except Exception as e:
            self.log.warning(f"pause 失败: {e}")
            return False
        self.log.info(
            f"pause device_id:{self.device_id} ret:{ret} "
            f"剩余:{self._paused_remain:.1f}s"
        )
        if not ret:
            return False
        self._paused = True
        self.is_playing = False
        self._local_pause_at = time.time()
        await self.cancel_next_timer()
        return True

    async def resume(self, music_name="", list_name=""):
        """从暂停处继续播放

        没有暂停中的会话（例如已停止），或用户在暂停后点了别的歌时，
        退回「从头播放」——播放按钮因此永远有反馈。
        """
        # 网页面板传来的「当前选中歌曲」：与断点歌曲不同 = 用户在暂停后点了别的歌
        requested = bool(music_name) and music_name != self.get_cur_music()
        if requested:
            self._paused = False
        if self._paused and self.auth_manager.mina_service is not None:
            try:
                ret = await self.auth_manager.mina_service.player_play(self.device_id)
            except Exception as e:
                self.log.warning(f"resume 失败: {e}")
                ret = False
            self.log.info(f"resume device_id:{self.device_id} ret:{ret}")
            if ret:
                self._paused = False
                self.is_playing = True
                self._local_play_at = time.time()
                # 本地秒表兜底（云端快照不可用时用它算进度）
                self._start_time = time.time()
                self._paused_time = 0
                if self._paused_remain > 0:
                    await self.set_next_music_timeout(self._paused_remain)
                return True
            self._paused = False

        name = music_name or self.get_cur_music()
        if not name:
            return False
        await self.play_music_list(list_name or self.device.cur_playlist, name)
        return True

    async def group_force_stop_xiaoai(self):
        """强制停止组内所有设备"""
        device_id_list = self.xiaomusic.device_manager.get_group_device_id_list(
            self.group_name
        )
        self.log.info(f"group_force_stop_xiaoai {self.group_name} {device_id_list}")
        tasks = [self.force_stop_xiaoai(device_id) for device_id in device_id_list]
        results = await asyncio.gather(*tasks)
        self.log.info(f"group_force_stop_xiaoai {device_id_list} {results}")
        return results

    async def stop_after_minute(self, minute: int):
        """定时关机"""
        if self._stop_timer:
            self._stop_timer.cancel()
            self._stop_timer = None
            self.log.info("关机定时器已取消")

        async def _do_stop():
            await asyncio.sleep(minute * 60)
            try:
                await self.stop(arg1="notts")
            except Exception as e:
                self.log.exception(f"Execption {e}")

        self._stop_timer = asyncio.create_task(_do_stop())
        await self.do_tts(f"收到,{minute}分钟后将关机")

    async def cancel_next_timer(self):
        """取消下一首定时器"""
        self.log.info(f"cancel_next_timer did: {self.did}")
        if self._next_timer:
            self._next_timer.cancel()
            try:
                await self._next_timer
            except asyncio.CancelledError:
                pass
            self.log.info(f"下一曲定时器已取消 did: {self.did}")
            self._next_timer = None
        else:
            self.log.info(f"下一曲定时器不见了 did: {self.did}")

    async def cancel_group_next_timer(self):
        """取消组内所有设备的下一首定时器"""
        devices = self.xiaomusic.device_manager.get_group_devices(self.group_name)
        self.log.info(f"cancel_group_next_timer {devices}")
        for device in devices.values():
            await device.cancel_next_timer()

    def get_cur_play_list(self):
        """获取当前播放列表名称"""
        return self.device.cur_playlist

    def cancel_all_timer(self):
        """清空所有定时器"""
        self.log.info("in cancel_all_timer")
        if self._next_timer:
            self._next_timer.cancel()
            self._next_timer = None
            self.log.info("cancel_all_timer _next_timer.cancel")

        if self._stop_timer:
            self._stop_timer.cancel()
            self._stop_timer = None
            self.log.info("cancel_all_timer _stop_timer.cancel")

        if self._tts_timer:
            self._tts_timer.cancel()
            self._tts_timer = None
            self.log.info("cancel_all_timer _tts_timer.cancel")

        if self._prefetch_timer:
            self._prefetch_timer.cancel()
            self._prefetch_timer = None
            self.log.info("cancel_all_timer _prefetch_timer.cancel")

    @classmethod
    def dict_clear(cls, d):
        """清空设备字典并取消所有定时器"""
        for key in list(d):
            val = d.pop(key)
            val.cancel_all_timer()

    def find_cur_playlist(self, name):
        """根据当前歌曲匹配歌曲列表

        匹配顺序：
        1. 收藏
        2. 最近新增
        3. 排除（全部,所有歌曲）
        4. 所有歌曲
        5. 全部
        """
        music_list = self.xiaomusic.music_library.music_list
        if name in music_list.get("收藏", []):
            return "收藏"
        if name in music_list.get("最近新增", []):
            return "最近新增"
        for list_name, play_list in music_list.items():
            if (list_name not in ["全部", "所有歌曲"]) and (name in play_list):
                return list_name
        if name in music_list.get("所有歌曲", []):
            return "所有歌曲"
        return "全部"

    async def handle_selection(self, index):
        """处理用户选择第几个歌曲

        Args:
            index: 用户选择的序号（从1开始）
        """
        if (
            not self._pending_selection
            or index < 1
            or index > len(self._pending_selection)
        ):
            await self.xiaomusic.do_tts(self.did, "选择无效")
            return

        selected_name = self._pending_selection[index - 1]
        self.log.info(f"用户选择了第{index}个: {selected_name}")
        # 保持待选择状态不变，支持用户继续选择其他歌曲
        await self._playmusic(selected_name)
