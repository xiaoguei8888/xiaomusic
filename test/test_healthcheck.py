"""校验容器健康检查脚本的端口解析与探活判定（离线，不访问小米云）。

运行：  .venv/bin/python test/test_healthcheck.py
"""

import http.server
import json
import os
import socket
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import healthcheck  # noqa: E402


def _write_setting(conf_dir, data):
    os.makedirs(conf_dir, exist_ok=True)
    with open(os.path.join(conf_dir, "setting.json"), "w", encoding="utf-8") as f:
        f.write(data if isinstance(data, str) else json.dumps(data))
    return conf_dir


def _free_port():
    """拿一个当前没人监听的端口。"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _serve(status=200):
    """起一个最小 HTTP 服务，返回 (server, port)。"""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.end_headers()

        def log_message(self, *args):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def test_resolve_port_prefers_setting_json():
    """setting.json 优先级高于环境变量（与 cli.py 的覆盖顺序一致）。"""
    with tempfile.TemporaryDirectory() as tmp:
        conf = _write_setting(os.path.join(tmp, "conf"), {"port": 18090})
        assert healthcheck.resolve_port(conf, {"XIAOMUSIC_PORT": "8090"}) == 18090
    print("resolve_port_prefers_setting_json OK")


def test_resolve_port_falls_back_to_env():
    with tempfile.TemporaryDirectory() as tmp:
        conf = _write_setting(os.path.join(tmp, "conf"), {"music_path": "music"})
        assert healthcheck.resolve_port(conf, {"XIAOMUSIC_PORT": "18091"}) == 18091
    print("resolve_port_falls_back_to_env OK")


def test_resolve_port_defaults_when_nothing_set():
    with tempfile.TemporaryDirectory() as tmp:
        got = healthcheck.resolve_port(os.path.join(tmp, "missing"), {})
        assert got == healthcheck.DEFAULT_PORT, got
    print("resolve_port_defaults_when_nothing_set OK")


def test_resolve_port_survives_broken_setting_file():
    """setting.json 损坏时不能让健康检查崩掉，应降级到环境变量。"""
    with tempfile.TemporaryDirectory() as tmp:
        conf = _write_setting(os.path.join(tmp, "conf"), "{ 不是合法 JSON")
        assert healthcheck.resolve_port(conf, {"XIAOMUSIC_PORT": "12345"}) == 12345
    print("resolve_port_survives_broken_setting_file OK")


def test_is_alive_true_when_server_running():
    srv, port = _serve(200)
    try:
        assert healthcheck.is_alive(port, timeout=2.0) is True
    finally:
        srv.shutdown()
        srv.server_close()
    print("is_alive_true_when_server_running OK")


def test_is_alive_true_on_401():
    """开启 Web 基础鉴权时 /getversion 会 401，仍应算存活。"""
    srv, port = _serve(401)
    try:
        assert healthcheck.is_alive(port, timeout=2.0) is True
    finally:
        srv.shutdown()
        srv.server_close()
    print("is_alive_true_on_401 OK")


def test_is_alive_false_when_nothing_listens():
    port = _free_port()
    assert healthcheck.is_alive(port, timeout=1.0) is False
    print("is_alive_false_when_nothing_listens OK")


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {type(e).__name__}: {e}")
    if failed:
        print(f"\n{failed} test(s) FAILED")
        sys.exit(1)
    print("\nALL PASS")
