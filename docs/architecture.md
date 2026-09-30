# XiaoMusic 架构说明

> 定位：**小爱音箱语音监听与控制 + 本地/NAS 音乐播放 + 极简 Web 管理**。
> 在线音乐、下载、JS 插件、二维码登录已移除，详见 ADR-0001。

## 分层

```
L4  api/         入站适配：routers/* · websocket · cli           只做协议转换
L3  services/    用例编排：xiaomusic.py(门面) · command_targets(命令目标)
                 command_handler · conversation
L2  domain/      领域：device_player(单设备播放) · music_library(曲库/歌单/标签)
L1  adapters/    出站适配：auth/login_flow(小米云) · utils/music_utils(ffmpeg/mutagen)
L0  core/        内核：events(类型化事件) · state(状态唯一源) · errors · task_supervisor
    bootstrap/   组装根：container(容器) · Module 协议 · Application
```

依赖方向单向：`api → services → domain → adapters`，`core` 与 `bootstrap` 被所有层依赖（core 自身不依赖任何业务模块）。

## 目录

| 路径 | 职责 |
|---|---|
| `xiaomusic/cli.py` | 参数解析、日志、启动 uvicorn |
| `xiaomusic/xiaomusic.py` | 门面：装配 + 生命周期 + 日志 + 跨模块编排（168 语句） |
| `xiaomusic/services/command_targets.py` | 18 条命令的实现与单层转发（Mixin，被门面继承） |
| `xiaomusic/core/events.py` | Event 基类 + 7 个 dataclass 事件 + EventBus（字符串/类型双轨兼容） |
| `xiaomusic/core/state.py` | frozen PlayerSnapshot + DeviceStateStore + StateStore 注册表 |
| `xiaomusic/core/task_supervisor.py` | 后台任务统一命名、取消、异常必记日志 |
| `xiaomusic/core/errors.py` | 统一异常体系（CommandError/AuthError/DeviceError/PlaybackError） |
| `xiaomusic/bootstrap/__init__.py` | Container（注册/解析/循环依赖检测）+ Module 协议 + Application |
| `xiaomusic/api/app.py` | FastAPI 实例、lifespan、静态文件挂载 |
| `xiaomusic/api/dependencies.py` | `app.state` 单一状态源、Basic/JWT 鉴权（无伪全局） |
| `xiaomusic/api/routers/*` | system / device / music / playlist / media / login |
| `xiaomusic/api/websocket.py` | 播放状态推送（事件驱动，1s 兜底） |
| `xiaomusic/command_handler.py` | 口令匹配 → `commands` 注册表 → 门面方法 |
| `xiaomusic/conversation.py` | 轮询音箱对话（cookie 三件套 + 失败指数退避），触发命令 |
| `xiaomusic/device_player.py` | 单设备播放控制、云端状态快照、定时器 |
| `xiaomusic/music_library.py` | 曲库扫描、歌单、标签/封面缓存（conf 派生）、模糊搜索 |
| `xiaomusic/auth*.py` / `login_flow.py` | 小米登录、token 刷新、设备发现 |
| `xiaomusic/utils/*` | file / music / text / system 工具 |

## 数据流

```
语音轮询 或 Web 请求
   → CommandHandler / router（只做解析与转发）
   → XiaoMusic 门面 → CommandTargets → DevicePlayer / MusicLibrary / AuthManager
   → DeviceStateStore（唯一写状态处）→ EventBus.publish（类型化事件）
        → WebSocket 推送（只读 frozen 快照）
        → ConfigManager 落盘
```

播放投递：本地文件 → `media` 路由暴露 URL → `/cmd` 或 ubus `player_play_music` 推给音箱。
云端状态快照是权威来源，本地计时器仅作兜底，两者在 `get_offset_duration` 内做插值。

## 并发模型

- **单 asyncio 事件循环**，无自定义线程；子进程仅 ffmpeg/ffprobe（`create_subprocess_exec`）。
- 阻塞解析（mutagen）走 `asyncio.to_thread`。
- 后台任务统一由 `core/task_supervisor.TaskSupervisor` 持有：命名、集中取消、异常必记日志。
- 对话轮询失败采用指数退避（2s→60s 封顶），日志按原因去重。

## 模块注入与移除

`Container` + `Module` 协议让"能力"成为可增删的装配单元：

```python
app = Application()
app.add(AuthModule()).add(PlaybackModule()).add(WebModule())   # 删掉一行 = 下线一个能力
```

`Module` 提供 `register(container)` / `routes()` / `start()` / `stop()` 四个钩子；
`test/test_container.py` 锁定容器契约（单例、循环依赖、重复注册、删模块即删能力）。
