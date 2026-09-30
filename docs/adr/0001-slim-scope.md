# ADR-0001 精简边界：只保留音箱监听与本地音乐

- 状态：已采纳（M0）
- 背景：仓库由上游 fork 而来，README 声称"精简版"，但代码里在线音乐、JS 插件、下载链路全部存在且已接线，文档与实现互相矛盾。
- 决策：以"**管理音箱的监听与播放**"为唯一定位，删除在线音乐（MusicFree/LX Server）、JS 插件运行时、下载（yt-dlp/bilibili/m3u8/代理流）、二维码登录、`exec` 自定义口令。
- 影响：Python 语句 8207 → 3847（-53%），文件 42 → 34，运行依赖 16 → 11，Docker 镜像不再需要 Node.js；线程模型从"3 线程 + 锁 + 轮询"回归单事件循环。
- 替代方案：保留全部能力并补齐测试（被否，工作量与维护成本翻倍且与 fork 定位不符）。

# ADR-0002 配置与状态单一来源

- 状态：已采纳（M0）
- 决策：运行时实例只挂在 `app.state`，路由通过 `Depends(get_xiaomusic)` 获取；播放状态只由 `DeviceStateStore` 写入并广播事件；缓存路径由 `config.tag_cache_path` / `picture_cache_path` 派生自 `conf_path`。
- 影响：删除模块级全局 `download_tasks`、`_proxy_token_cache` 与已废弃的 20 个配置字段；`scripts/migrate_settings.py` 负责旧配置迁移。

# ADR-0003 并发模型：单事件循环 + 子进程

- 状态：已采纳（M0）
- 决策：全部 I/O 走 asyncio；外部进程只用 `asyncio.create_subprocess_exec`（ffmpeg/ffprobe）；阻塞的 mutagen 解析用 `asyncio.to_thread`；不再引入 threading。
- 影响：JS 插件运行时的 3 个线程与 `time.sleep` 轮询随模块删除；后续若需并行任务，统一用 asyncio task。

# ADR-0004 测试与验收基线

- 状态：已采纳（M0）
- 现状：`test/*.py` 为纯 assert 脚本（10 个，全部通过），语句覆盖率 22.6%。
- 决策：M1 起引入 pytest + pytest-cov，按 domain/services ≥95%、全局 ≥90% 的目标推进；覆盖率门禁进 CI（M3 起 `--cov-fail-under=90`）。
