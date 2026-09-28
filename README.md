# XiaoMusic（精简版）

用小爱音箱播放本地/NAS 音乐，并拦截语音指令。基于 [hanxi/xiaomusic](https://github.com/hanxi/xiaomusic)（MIT）裁剪的极简 fork，只保留离线播放和基础语音控制。

## 核心能力

1. **连接小米/小爱音箱并拦截语音指令**：轮询音箱最新一句话，匹配关键词后执行播放、停止、切歌等动作。
2. **播放本地/NAS 音乐目录里的音乐**：扫描指定目录，不需要任何在线音乐服务。
3. **极简 Web 管理页面**：仅保留 `static/default` 主题。

## 精简说明

本 fork 移除了上游的以下功能，上游文档里的相关内容在这里不适用：

在线音乐搜索/下载（yt-dlp、bilibili）、JS 插件与 `exec` 自定义口令、二维码扫码登录、定时任务（crontab/apscheduler）、目录监控（watchdog）、统计上报（ga4mp/sentry）、edge-tts 外部语音（改用小爱原生 TTS）、自更新、以及多个第三方 Web 主题。

## Docker 部署

先在仓库根目录构建镜像：

```bash
docker build -t xiaomusic .
```

运行（挂载音乐目录和 conf 目录，映射两个端口）：

```bash
docker run -d --name xiaomusic \
  -p 8090:8090 \
  -p 58090:58090 \
  -v /path/to/music:/app/music \
  -v /path/to/conf:/app/conf \
  -e MI_USER=你的小米账号 \
  -e MI_PASS=你的小米密码 \
  -e MI_DID=你的设备DID \
  -e XIAOMUSIC_HOSTNAME=http://192.168.1.100 \
  -e XIAOMUSIC_MUSIC_PATH=/app/music \
  xiaomusic
```

- `/path/to/music` 和 `/path/to/conf` 是宿主机目录，按实际情况修改。
- `XIAOMUSIC_HOSTNAME` 必须是**音箱能访问到的地址**（局域网 IP，不要用 `localhost`）。
- 镜像内已内置 ffmpeg。容器内默认 `XIAOMUSIC_PORT=8090`、`XIAOMUSIC_PUBLIC_PORT=58090`、`XIAOMUSIC_MUSIC_PATH=music`。

## 本地运行

需要 Python 3.10+，并自行安装 ffmpeg（时长、转码、音量均衡都会用到）。

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
python xiaomusic.py --port 8090 --verbose
```

如果已安装 [PDM](https://pdm-project.org/)，也可：

```bash
pdm install
pdm run python xiaomusic.py --port 8090 --verbose
```

## 配置

关键环境变量：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `MI_USER` | 小米账号 | 空 |
| `MI_PASS` | 小米密码 | 空 |
| `MI_DID` | 设备 DID，逗号分隔支持多设备 | 空 |
| `XIAOMUSIC_MUSIC_PATH` | 本地音乐目录 | `music` |
| `XIAOMUSIC_HOSTNAME` | 音箱可访问的主机地址 | `http://192.168.2.5` |
| `XIAOMUSIC_PORT` | Web/API 监听端口 | `8090` |
| `XIAOMUSIC_PUBLIC_PORT` | 歌曲访问端口，音箱必须能访问 `hostname:public_port` | `58090` |
| `XIAOMUSIC_ENABLE_PULL_ASK` | 轮询音箱最新一句话以拦截语音指令 | `true` |
| `XIAOMUSIC_GET_ASK_BY_MINA` | 通过 mina 接口获取音箱最近对话 | `false` |
| `XIAOMUSIC_PULL_ASK_SEC` | 轮询间隔（秒） | `1` |
| `XIAOMUSIC_CONF_PATH` | 配置与持久化目录 | `conf` |
| `XIAOMUSIC_EDGE_TTS_VOICE` | edge-tts 语音；留空表示用小爱原生 TTS，设为 `disable` 关闭语音提示 | 空 |
| `XIAOMUSIC_DISABLE_HTTPAUTH` | 关闭 Web 基础鉴权 | `true` |
| `XIAOMUSIC_HTTPAUTH_USERNAME` | 基础鉴权用户名 | 空 |
| `XIAOMUSIC_HTTPAUTH_PASSWORD` | 基础鉴权密码 | 空 |
| `XIAOMUSIC_LOG_FILE` | 日志文件路径 | `xiaomusic.log.txt` |
| `XIAOMUSIC_VERBOSE` | 输出详细日志 | `false` |

`cookie` 字段没有专用环境变量，直接写在 `conf/setting.json` 里。

## 默认语音口令

| 口令 | 动作 |
|------|------|
| 播放歌曲 / 放歌曲 | `play` |
| 播放本地歌曲 / 本地播放歌曲 | `playlocal` |
| 下一首 | `play_next` |
| 上一首 | `play_prev` |
| 播放列表 / 播放歌单 | `play_music_list` |
| 播放列表第 | `play_music_list_index` |
| 单曲循环 | `set_play_type_one` |
| 全部循环 | `set_play_type_all` |
| 随机播放 | `set_play_type_rnd` |
| 单曲播放 | `set_play_type_sin` |
| 顺序播放 | `set_play_type_seq` |
| 分钟后关机 | `stop_after_minute` |
| 刷新列表 | `gen_music_list` |
| 加入收藏 / 收藏歌曲 | `add_to_favorites` |
| 取消收藏 | `del_from_favorites` |
| 删除歌曲 | `cmd_del_music` |
| 关机 / 暂停 / 停止 / 停止播放 | `stop` |

## Web 管理

访问 `http://<host>:8090/`，会 302 重定向到 `/static/default/index.html`。

鉴权：`XIAOMUSIC_DISABLE_HTTPAUTH` 默认 `true`，即默认不开启基础鉴权。要开启就把该项设为 `false`，并配置 `XIAOMUSIC_HTTPAUTH_USERNAME` 和 `XIAOMUSIC_HTTPAUTH_PASSWORD`。

## 常见问题

**音箱拉不到音乐？**
`XIAOMUSIC_HOSTNAME` 加上 `XIAOMUSIC_PUBLIC_PORT` 必须拼成音箱能访问的地址。音箱和本服务要在同一局域网，用主机 IP，不要用 `localhost` 或 `127.0.0.1`。确认 `public_port`（默认 58090）从音箱侧可达。

**语音指令不拦截？**
先确认 `XIAOMUSIC_ENABLE_PULL_ASK=true`（默认已开），再确认 `MI_USER` / `MI_PASS` 正确、`MI_DID` 填了对应设备。排查时打开 `XIAOMUSIC_VERBOSE=true` 看日志。

**无法登录？**
本 fork 已移除二维码扫码登录。只能用小米账号密码（`MI_USER` / `MI_PASS`），或在 `conf/setting.json` 中配置 `cookie`。

**没有语音提示（TTS）？**
`XIAOMUSIC_EDGE_TTS_VOICE` 默认空，表示使用小爱原生 TTS；设为 `disable` 会完全关闭语音提示。

**放不出声或时长不对？**
检查 ffmpeg 是否可用。Docker 镜像已内置，本地运行需要自行安装。

## 许可

[MIT](LICENSE)。基于 [hanxi/xiaomusic](https://github.com/hanxi/xiaomusic) 修改，原作者 涵曦。
