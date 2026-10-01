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

## 部署

### Docker（推荐）

```bash
docker build -t xiaomusic .
docker run -d --name xiaomusic --restart unless-stopped \
  -p 8090:8090 -p 58090:58090 \
  -v /path/to/music:/app/music -v /path/to/conf:/app/conf \
  -e TZ=Asia/Shanghai \
  -e MI_USER=你的小米账号 -e MI_PASS=你的小米密码 -e MI_DID=你的设备DID \
  -e XIAOMUSIC_HOSTNAME=http://192.168.1.100 xiaomusic
```

> **两个端口都要发布。** `8090` 是 Web 管理页与 API；`58090` 是音箱拉取音频的端口
> （`XIAOMUSIC_PUBLIC_PORT` 的默认值）。只映射 8090 时音箱拿不到音乐，表现为「放不出声」。
> 改了 `XIAOMUSIC_PUBLIC_PORT` 就要同步改 `-p`。

仓库自带 `docker-compose.yml`，等价于上面的命令：

```bash
XIAOMUSIC_HOSTNAME=http://192.168.1.100 docker compose up -d --build
```

更推荐把变量写进同目录的 `.env`（Compose 会自动读取），再执行 `docker compose up -d --build`。
启动后确认健康状态：

```bash
docker inspect --format '{{.State.Health.Status}}' xiaomusic   # healthy
curl -s http://127.0.0.1:8090/getversion                        # {"version":"0.6.1"}
```

### 从 GHCR 拉取镜像部署（服务器）

镜像由 GitHub Actions 构建并推送到 GHCR，一份 manifest 覆盖 `linux/amd64`、`linux/arm64`、`linux/arm/v7`：

```bash
docker pull ghcr.io/xiaoguei8888/xiaomusic:latest
```

服务器上用 `docker-compose.release.yml` 部署，只拉取、**不在目标机上构建**：

```bash
XIAOMUSIC_IMAGE=ghcr.io/xiaoguei8888/xiaomusic:latest \
XIAOMUSIC_MUSIC_DIR=/docker/filebrowser/srv/xiaomusic/music \
XIAOMUSIC_CONF_DIR=/docker/filebrowser/srv/xiaomusic/conf \
XIAOMUSIC_HOSTNAME=http://192.168.1.100 \
docker compose -f docker-compose.release.yml up -d
```

`XIAOMUSIC_MUSIC_DIR` / `XIAOMUSIC_CONF_DIR` 必须指向**已有数据所在的目录**，否则等于重新初始化。
升级时只换 `XIAOMUSIC_IMAGE` 的 tag 再 `up -d` 即可。

直连 `ghcr.io` 慢时可走南京大学镜像，拉完重新打 tag：

```bash
docker pull ghcr.nju.edu.cn/xiaoguei8888/xiaomusic:latest
docker tag ghcr.nju.edu.cn/xiaoguei8888/xiaomusic:latest ghcr.io/xiaoguei8888/xiaomusic:latest
```

### 发布镜像

`.github/workflows/publish.yml` 在**推 tag**或**手动触发**时构建多架构镜像并推送到 GHCR：

```bash
git tag v0.6.1 && git push origin v0.6.1   # 产出 :0.6.1 / :0.6 / :v0.6.1 / :latest
```

也可以 GitHub → Actions → **publish** → Run workflow，选分支，产出 `:<分支名>`（如 `:slim-m0`）。

两个前置条件：

1. fork 仓库的 Actions 已启用；
2. 首次发布后到仓库 **Packages** 把该包可见性改成 **Public**，否则服务器 `docker pull` 会 401；
   不想公开就让服务器执行 `docker login ghcr.io -u <用户名> -p <PAT>`（PAT 需要 `read:packages`）。

### 部署前检查清单

- [ ] `XIAOMUSIC_HOSTNAME` 填**音箱能访问到的局域网 IP/域名**（不要 `localhost`、不要容器名）
- [ ] `XIAOMUSIC_PUBLIC_PORT` 已映射，且与 `-p` / compose 里的端口一致
- [ ] `conf/` 与 `music/` 已挂载到宿主机，否则重启后配置、歌单、tag 缓存全丢
- [ ] 宿主机防火墙放行 8090 与 58090
- [ ] 从旧版本升级后跑一次 `python scripts/migrate_settings.py`

### 配置优先级（容易踩坑）

`conf/setting.json` 的优先级**高于环境变量**：启动时先用环境变量构造配置，再用 setting.json 覆盖。

所以**不要把 `config-example.json` 整个拷成 `conf/setting.json`** —— 示例里的
`account` / `password` / `mi_did` 是空串，会把环境变量里的账号密码覆盖成空，直接登录失败。
只需要覆盖某个键时，setting.json 里就只写这一个键：

```json
{ "key_word_dict": { "播放我的歌单": "play_music_list" } }
```

`config-example.json` 里有 8 个键没有对应的环境变量，只能写进 setting.json：
`key_word_dict`、`user_key_word_dict`、`key_match_order`、`keywords_singer_play`、
`cookie`、`devices`、`remove_id3tag`、`convert_to_mp3`。

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

## 安全

- 账号、密码、cookie、设备 token 只放在 `conf/`（已被 `.gitignore` 排除）或环境变量里，**不要写进仓库**；
- `scripts/check_secrets.py` 是提交前的保密守卫，可单独运行：

```bash
python scripts/check_secrets.py --staged   # 检查暂存区
python scripts/check_secrets.py --all      # 检查所有已跟踪文件
```

- 装了 pre-commit 时每次提交会自动跑（`.pre-commit-config.yaml` 里的 `check-secrets` 钩子），命中即拒绝提交。

## 许可

[MIT](LICENSE)。基于 [hanxi/xiaomusic](https://github.com/hanxi/xiaomusic) 修改，原作者 涵曦。
