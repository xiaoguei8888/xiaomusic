# XiaoMusic 架构说明

> 定位：**小爱音箱语音监听与控制 + 本地/NAS 音乐播放 + 极简 Web 管理**。
> 在线音乐、下载、JS 插件、二维码登录已移除，详见 ADR-0001。

## 分层

```
L4  api/         入站适配：routers/* · websocket · cli           只做协议转换
L3  services     用例编排：xiaomusic.py(门面) · command_handler · conversation
L2  domain       领域：device_player(单设备播放) · music_library(曲库/歌单/标签)
L1  adapters     出站适配：auth/login_flow(小米云) · utils/music_utils(ffmpeg/mutagen)
L0  core         内核：events(事件总线) · device_state(状态唯一源) · config · const
```

依赖方向单向：`api → services → domain → adapters`，`core` 被所有层依赖。禁止反向 import。

## 目录

| 路径 | 职责 |
|---|---|
| `xiaomusic/cli.py` | 参数解析、日志、启动 uvicorn |
| `xiaomusic/xiaomusic.py` | 门面：装配组件 + 命令目标方法 |
| `xiaomusic/api/app.py` | FastAPI 实例、lifespan、静态文件挂载 |
| `xiaomusic/api/dependencies.py` | `app.state` 单一状态源、Basic/JWT 鉴权 |
| `xiaomusic/api/routers/*` | system / device / music / playlist / media / login |
| `xiaomusic/api/websocket.py` | 播放状态推送（事件驱动，1s 兜底） |
| `xiaomusic/command_handler.py` | 口令匹配 → `commands` 注册表 → 门面方法 |
| `xiaomusic/conversation.py` | 轮询音箱对话，触发命令 |
| `xiaomusic/device_player.py` | 单设备播放控制、云端状态快照、定时器 |
| `xiaomusic/music_library.py` | 曲库扫描、歌单、标签/封面缓存、模糊搜索 |
| `xiaomusic/auth*.py` / `login_flow.py` | 小米登录、token 刷新、设备发现 |
| `xiaomusic/events.py` / `device_state.py` | 事件总线 / 播放状态唯一可变源 |
| `xiaomusic/utils/*` | file / music / text / system 工具 |

## 数据流

```
语音轮询 或 Web 请求
   → CommandHandler / router（只做解析与转发）
   → XiaoMusic 门面 → DevicePlayer / MusicLibrary / AuthManager
   → DeviceStateStore（唯一写状态处）→ EventBus.publish
        → WebSocket 推送（只读快照）
        → ConfigManager 落盘
```

播放投递：本地文件 → `media` 路由暴露 URL → `/cmd` 或 ubus `player_play_music` 推给音箱。
云端状态快照是权威来源，本地计时器仅作兜底，两者在 `get_offset_duration` 内做插值。

## 并发模型

- **单 asyncio 事件循环**，无自定义线程；子进程仅 ffmpeg/ffprobe（`create_subprocess_exec`）。
- 阻塞解析（mutagen）走 `asyncio.to_thread`。
- 每设备定时器由 `XiaoMusicDevice` 统一持有，取消走 `cancel_all_timer`。

## 模块替换

新增能力 = 新路由模块 + 在 `api/routers/__init__.py` 注册一行 + 在门面装配；
移除能力 = 删除模块目录 + 删除注册行 + 删除门面引用（本轮即按此方式删掉 4 个能力）。
