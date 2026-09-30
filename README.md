# XiaoMusic（精简版）

用小爱音箱播放本地/NAS 音乐，并拦截语音指令。基于 [hanxi/xiaomusic](https://github.com/hanxi/xiaomusic)（MIT）裁剪。

## 能力范围

| 有 | 没有 |
|---|---|
| 连接小爱音箱、轮询最新对话、拦截语音口令 | 在线音乐搜索/歌单/换源（MusicFree、LX Server） |
| 播放本地/NAS 音乐目录 | 下载音乐（yt-dlp、bilibili、m3u8、代理流） |
| 本地歌单增删改、收藏 | JS 插件与 `exec` 自定义口令 |
| 本地模糊搜索与多结果选择（第 N 个） | 二维码扫码登录 |
| 单曲/全部循环、随机、顺序、单曲播放 | 定时任务、自更新、统计上报、edge-tts |
| 分钟后关机、TTS 播报、报错音效 | Web 面板"推送链接给音箱"、代理播放 |
| 极简 Web 管理页（设置/播放/歌单/调试）与 WS 状态推送 | |

## 运行

需要 Python 3.10+ 与 ffmpeg（本地时长、转码、音量均衡需要）。

```bash
python -m venv .venv && source .venv/bin/activate
pip install -U pip && pip install -e .
python xiaomusic.py --port 8090 --verbose
```

Docker：

```bash
docker build -t xiaomusic .
docker run -d --name xiaomusic -p 8090:8090 \
  -v /path/to/music:/app/music -v /path/to/conf:/app/conf \
  -e MI_USER=你的小米账号 -e MI_PASS=你的小米密码 -e MI_DID=你的设备DID \
  -e XIAOMUSIC_HOSTNAME=http://192.168.1.100 xiaomusic
```

## 配置

关键环境变量（完整清单见 `config-example.json`）：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `MI_USER` / `MI_PASS` | 小米账号密码 | 空 |
| `MI_DID` | 设备 DID，逗号分隔支持多设备 | 空 |
| `XIAOMUSIC_MUSIC_PATH` | 本地音乐目录 | `music` |
| `XIAOMUSIC_HOSTNAME` | 音箱可访问的主机地址 | `http://192.168.2.5` |
| `XIAOMUSIC_PORT` | Web/API 监听端口 | `8090` |
| `XIAOMUSIC_PUBLIC_PORT` | 歌曲访问端口，音箱必须能访问 | `58090` |
| `XIAOMUSIC_CONF_PATH` | 配置与持久化目录 | `conf` |
| `XIAOMUSIC_ENABLE_PULL_ASK` | 轮询音箱最新一句话以拦截语音指令 | `true` |
| `XIAOMUSIC_DISABLE_HTTPAUTH` | 关闭 Web 基础鉴权 | `true` |
| `XIAOMUSIC_VERBOSE` | 详细日志 | `false` |

升级自旧版本时执行一次 `python scripts/migrate_settings.py`，清理已废弃的配置键。

## 默认语音口令

| 口令 | 动作 |
|------|------|
| 播放歌曲 / 放歌曲 | `play` |
| 播放本地歌曲 / 本地播放歌曲 | `playlocal` |
| 下一首 / 上一首 | `play_next` / `play_prev` |
| 播放列表 / 播放歌单 | `play_music_list` |
| 播放列表第 | `play_music_list_index` |
| 单曲循环 / 全部循环 / 随机播放 / 单曲播放 / 顺序播放 | `set_play_type_*` |
| 分钟后关机 | `stop_after_minute` |
| 刷新列表 | `gen_music_list` |
| 加入收藏 / 收藏歌曲 / 取消收藏 | `add_to_favorites` / `del_from_favorites` |
| 删除歌曲 | `cmd_del_music` |
| 关机 / 暂停 / 停止 / 停止播放 | `stop` |

## 常见问题

**音箱拉不到音乐？** `XIAOMUSIC_HOSTNAME` + `XIAOMUSIC_PUBLIC_PORT` 必须拼成音箱能访问的地址（局域网 IP，不要 localhost）。

**语音指令不拦截？** 确认 `XIAOMUSIC_ENABLE_PULL_ASK=true`、账号密码正确、`MI_DID` 填了对应设备，并打开 `XIAOMUSIC_VERBOSE=true` 看日志。

**无法登录？** 本 fork 只支持账号密码（`MI_USER`/`MI_PASS`）或 `conf/setting.json` 里的 `cookie`。

**放不出声或时长不对？** 检查 ffmpeg 是否可用。

## 许可

[MIT](LICENSE)。基于 [hanxi/xiaomusic](https://github.com/hanxi/xiaomusic) 修改，原作者 涵曦。
