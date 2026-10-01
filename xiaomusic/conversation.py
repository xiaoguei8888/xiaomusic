"""对话记录拉取模块

本模块负责从小爱音箱拉取对话记录，包括：
- 轮询最新对话记录
- 从小爱API获取对话
- 通过Mina服务获取对话
- 解析和验证对话记录
"""

import asyncio
import json
import time

from aiohttp import ClientSession, ClientTimeout

from xiaomusic.const import COOKIE_TEMPLATE, GET_ASK_BY_MINA, LATEST_ASK_API

REINIT_COOLDOWN_SEC = 60

# 轮询失败退避（秒）：2 → 4 → … → 60 封顶
BACKOFF_START_SEC = 2
BACKOFF_MAX_SEC = 60
# 进入降级提示前允许的连续失败次数
FAILURE_WARN_THRESHOLD = 3


class ConversationPoller:
    """对话记录轮询器

    负责定期从小爱音箱拉取最新的对话记录，支持两种方式：
    1. 通过小爱API直接获取（LATEST_ASK_API）
    2. 通过Mina服务获取（适用于特定硬件）
    """

    def __init__(
        self,
        config,
        log,
        auth_manager,
        device_manager,
    ):
        """初始化对话轮询器

        Args:
            config: 配置对象
            log: 日志对象
            auth_manager: 认证管理器实例
            device_manager: 设备管理器实例
        """
        self.config = config
        self.log = log
        self.auth_manager = auth_manager
        self.device_manager = device_manager
        self.last_timestamp = {}  # key为 did. timestamp last call mi speaker

        self.last_record = None

        self._last_reinit_time = 0

        self.polling_event = asyncio.Event()
        self.new_record_event = asyncio.Event()

        # 失败退避与日志去重
        self._consecutive_failures = 0
        self._backoff_sec = BACKOFF_START_SEC
        self._last_error = ""

    async def run_conversation_loop(self, do_check_cmd_callback, reset_timer_callback):
        """运行对话循环

        持续运行的主循环，负责：
        1. 启动对话轮询任务
        2. 等待新对话记录
        3. 调用回调处理对话命令

        Args:
            do_check_cmd_callback: 处理命令的回调函数 async def(did, query, ctrl_panel)
            reset_timer_callback: 重置计时器的回调函数 async def(answer_length, did)
        """
        # 启动轮询任务
        async with ClientSession() as session:
            task = asyncio.create_task(self.poll_latest_ask(session))
            assert task is not None  # to keep the reference to task, do not remove this

            try:
                while True:
                    self.polling_event.set()
                    await self.new_record_event.wait()
                    self.new_record_event.clear()
                    new_record = self.last_record
                    self.polling_event.clear()  # stop polling when processing the question

                    query = new_record.get("query", "").strip()
                    did = new_record.get("did", "").strip()
                    await do_check_cmd_callback(did, query, False)

                    answer = new_record.get("answer")
                    answers = new_record.get("answers", [{}])
                    if answers:
                        answer = answers[0].get("tts", {}).get("text", "").strip()
                        await reset_timer_callback(len(answer), did)
                        self.log.debug(f"query:{query} did:{did} answer:{answer}")
            except asyncio.CancelledError:
                self.log.info("Conversation loop cancelled, cleaning up...")
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                raise

    async def poll_latest_ask(self, session):
        """轮询最新对话记录

        持续运行的协程，定期从所有设备拉取最新对话记录。
        根据配置的拉取间隔和硬件类型选择合适的获取方式。

        Args:
            session: aiohttp客户端会话
        """
        try:
            while True:
                if not self.config.enable_pull_ask:
                    self.log.debug("Listening new message disabled")
                    await asyncio.sleep(5)
                    continue

                self.log.debug(
                    f"Listening new message, timestamp: {self.last_timestamp}"
                )
                # 动态获取最新的 cookie_jar
                if self.auth_manager.cookie_jar is not None:
                    session._cookie_jar = self.auth_manager.cookie_jar

                # 拉取所有音箱的对话记录
                tasks = []
                for device_id in self.device_manager.device_id_did:
                    # 首次用当前时间初始化
                    did = self.device_manager.get_did(device_id)
                    if did not in self.last_timestamp:
                        self.last_timestamp[did] = int(time.time() * 1000)

                    hardware = self.device_manager.get_hardward(device_id)
                    if (hardware in GET_ASK_BY_MINA) or self.config.get_ask_by_mina:
                        tasks.append(self.get_latest_ask_by_mina(device_id))
                    else:
                        tasks.append(
                            self.get_latest_ask_from_xiaoai(session, device_id)
                        )
                await asyncio.gather(*tasks)

                start = time.perf_counter()
                await self.polling_event.wait()
                if self._consecutive_failures:
                    # 失败后退避，避免把日志刷爆
                    await asyncio.sleep(self._backoff_sec)
                elif self.config.pull_ask_sec <= 1:
                    if (d := time.perf_counter() - start) < 1:
                        await asyncio.sleep(1 - d)
                else:
                    sleep_sec = 0
                    while True:
                        await asyncio.sleep(1)
                        sleep_sec = sleep_sec + 1
                        if sleep_sec >= self.config.pull_ask_sec:
                            break
        except asyncio.CancelledError:
            self.log.info("Polling task cancelled")
            raise

    def _build_ask_cookies(self, device_id):
        """组装对话接口所需的 cookie。

        小米对话接口要求同时带 deviceId / userId / serviceToken，
        只带 deviceId 会返回 400 MissingRequestCookieException。
        缺失项自动省略，兼容旧版仅存 deviceId 的 auth.json。
        """
        cookie_dict = {"deviceId": device_id}
        state = getattr(self.auth_manager, "_state", None)
        data = getattr(state, "data", None) or {}
        user_id = data.get("userId")
        if user_id:
            cookie_dict["userId"] = str(user_id)
        service_token = ((data.get("sids") or {}).get("micoapi") or {}).get(
            "serviceToken"
        )
        if service_token:
            cookie_dict["serviceToken"] = service_token

        # 交给 aiohttp 的是解析后的键值对；COOKIE_TEMPLATE 是同一协议的字符串形态，
        # 这里用它做一次自检，避免模板与实际字段脱节。
        expected = [
            part.split("=", 1)[0].strip()
            for part in COOKIE_TEMPLATE.split(";")
            if part.strip()
        ]
        missing = [
            name
            for name in ("deviceId", "userId", "serviceToken")
            if name not in cookie_dict
        ]
        if missing and self._last_error != "cookie:" + ",".join(missing):
            self.log.warning(
                "[CONV] 对话接口 cookie 缺少 %s，将可能返回 400；"
                "请重新登录以补全 auth.json（模板字段: %s）",
                missing,
                expected,
            )
            self._last_error = "cookie:" + ",".join(missing)
        return cookie_dict

    def _note_failure(self, reason: str) -> None:
        """记录一次失败并推进退避；日志按原因去重，避免刷屏。"""
        self._consecutive_failures += 1
        self._backoff_sec = min(self._backoff_sec * 2, BACKOFF_MAX_SEC)
        if reason != self._last_error:
            self.log.warning(
                "[CONV] 轮询对话失败(第 %d 次): %s；进入退避 %ds",
                self._consecutive_failures,
                reason,
                self._backoff_sec,
            )
            self._last_error = reason
        elif self._consecutive_failures == FAILURE_WARN_THRESHOLD:
            self.log.warning(
                "[CONV] 对话轮询已连续失败 %d 次（%s）。若长期无法恢复，"
                "可设置 XIAOMUSIC_GET_ASK_BY_MINA=true 改用 mina 通道。",
                self._consecutive_failures,
                reason,
            )

    def _note_success(self) -> None:
        if self._consecutive_failures:
            self.log.info(
                "[CONV] 对话轮询恢复正常（此前连续失败 %d 次）",
                self._consecutive_failures,
            )
        self._consecutive_failures = 0
        self._backoff_sec = BACKOFF_START_SEC
        self._last_error = ""

    async def get_latest_ask_from_xiaoai(self, session, device_id):
        cookies = self._build_ask_cookies(device_id)
        retries = 3
        for i in range(retries):
            try:
                timeout = ClientTimeout(total=15)
                hardware = self.device_manager.get_hardward(device_id)
                url = LATEST_ASK_API.format(
                    hardware=hardware,
                    timestamp=str(int(time.time() * 1000)),
                )
                r = await session.get(url, timeout=timeout, cookies=cookies)

                if r.status != 200:
                    body = ""
                    try:
                        body = (await r.text())[:160].replace("\n", " ")
                    except Exception:
                        pass
                    self._note_failure(f"HTTP {r.status} {body}")
                    if i == retries - 1 and r.status == 401:
                        await self._try_reinit("401错误")
                    continue

            except asyncio.CancelledError:
                self.log.warning("Task was cancelled.")
                return None

            except Exception as e:
                self._note_failure(f"{type(e).__name__}: {e}")
                continue

            try:
                data = await r.json()
            except Exception as e:
                self._note_failure(f"JSON解析失败: {e}")
                if i == retries - 1:
                    self.log.info("Maybe outof date trying to re init it")
                    await self._try_reinit("JSON解析失败")
            else:
                self._note_success()
                return self._get_last_query(device_id, data)
        if self._consecutive_failures % FAILURE_WARN_THRESHOLD == 0:
            self.log.warning("get_latest_ask_from_xiaoai. All retries failed.")

    async def _try_reinit(self, reason):
        elapsed = time.time() - self._last_reinit_time
        if elapsed < REINIT_COOLDOWN_SEC:
            self.log.warning(
                f"触发reinit冷却中({elapsed:.0f}s/{REINIT_COOLDOWN_SEC}s)，"
                f"跳过本次reinit(原因: {reason})"
            )
            return
        self._last_reinit_time = time.time()
        self.log.info(f"触发reinit(原因: {reason})")
        await self.auth_manager.init_all_data()

    async def get_latest_ask_by_mina(self, device_id):
        """通过Mina服务获取最新对话

        使用Mina服务API获取对话记录，适用于特定硬件类型。

        Args:
            device_id: 设备ID

        Returns:
            None - 通过 _check_last_query 更新内部状态
        """
        try:
            did = self.device_manager.get_did(device_id)
            # 动态获取最新的 mina_service
            if self.auth_manager.mina_service is None:
                self.log.warning(
                    f"mina_service is None, skip get_latest_ask_by_mina for device {device_id}"
                )
                return
            messages = await self.auth_manager.mina_service.get_latest_ask(device_id)
            self.log.debug(
                f"get_latest_ask_by_mina device_id:{device_id} did:{did} messages:{messages}"
            )
            for message in messages:
                query = message.response.answer[0].question
                answer = message.response.answer[0].content
                last_record = {
                    "time": message.timestamp_ms,
                    "did": did,
                    "query": query,
                    "answer": answer,
                }
                self._check_last_query(last_record)
        except Exception as e:
            error_str = str(e)
            if (
                "Login failed" in error_str
                or "70016" in error_str
                or "401" in error_str
            ):
                await self._try_reinit(f"mina API失败: {e}")
            else:
                self.log.warning(f"get_latest_ask_by_mina {e}")
        return

    def _get_last_query(self, device_id, data):
        """从API响应数据中提取最后一条对话

        解析小爱API返回的JSON数据，提取最新的对话记录。

        Args:
            device_id: 设备ID
            data: API响应数据

        Returns:
            None - 通过 _check_last_query 更新内部状态
        """
        did = self.device_manager.get_did(device_id)
        self.log.debug(f"_get_last_query device_id:{device_id} did:{did} data:{data}")
        if d := data.get("data"):
            records = json.loads(d).get("records")
            if not records:
                return
            last_record = records[0]
            last_record["did"] = did
            answers = last_record.get("answers", [{}])
            if answers:
                answer = answers[0].get("tts", {}).get("text", "").strip()
                last_record["answer"] = answer
            self._check_last_query(last_record)

    def _check_last_query(self, last_record):
        """检查并更新最后一条对话记录

        验证对话记录的时间戳，如果是新记录则更新并触发事件。

        Args:
            last_record: 对话记录字典，包含 did、time、query、answer 等字段
        """
        did = last_record["did"]
        timestamp = last_record.get("time")
        query = last_record.get("query", "").strip()
        self.log.debug(f"{did} 获取到最后一条对话记录：{query} {timestamp}")

        if timestamp > self.last_timestamp[did]:
            self.last_timestamp[did] = timestamp
            self.last_record = last_record
            self.new_record_event.set()
