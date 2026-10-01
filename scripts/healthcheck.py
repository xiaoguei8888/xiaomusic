#!/usr/bin/env python3
"""容器健康检查：确认 Web 服务已经监听并能响应 HTTP。

镜像里的 HEALTHCHECK 与 docker-compose 的 healthcheck 都调用本脚本。

端口解析顺序与运行时保持一致（conf/setting.json > 环境变量 > 8090）：
cli.py 先用环境变量构造 Config，随后用 conf/setting.json 覆盖，
所以 setting.json 里的 port 才是最终生效值。

只依赖标准库，镜像里不需要额外装 curl/wget。
退出码：0 = 健康，1 = 不健康。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_PORT = 8090
DEFAULT_CONF_PATH = "conf"
HEALTH_PATH = "/getversion"
TIMEOUT_SEC = 3.0


def resolve_port(conf_path: str | None = None, env: dict | None = None) -> int:
    """解析监听端口：conf/setting.json 优先，其次 XIAOMUSIC_PORT，最后 8090。"""
    env = os.environ if env is None else env
    if conf_path is None:
        conf_path = env.get("XIAOMUSIC_CONF_PATH") or DEFAULT_CONF_PATH
    try:
        with open(os.path.join(conf_path, "setting.json"), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("port") is not None:
            return int(data["port"])
    except (OSError, ValueError, TypeError):
        pass
    try:
        return int(env.get("XIAOMUSIC_PORT") or DEFAULT_PORT)
    except (TypeError, ValueError):
        return DEFAULT_PORT


def is_alive(port: int, timeout: float = TIMEOUT_SEC) -> bool:
    """探测 /getversion。

    任何非 5xx 的 HTTP 响应都算存活：开启基础鉴权时返回 401 属于「服务在跑」。
    只有连不上或 5xx 才算不健康。
    """
    url = f"http://127.0.0.1:{port}{HEALTH_PATH}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except (urllib.error.URLError, OSError):
        return False


def main() -> int:
    port = resolve_port()
    if is_alive(port):
        print(f"healthy: 127.0.0.1:{port}{HEALTH_PATH}")
        return 0
    print(f"unhealthy: 127.0.0.1:{port}{HEALTH_PATH} 无响应", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
