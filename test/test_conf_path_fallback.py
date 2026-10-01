"""conf_path 为空串时，认证状态必须仍然能落盘。

事故背景（2026-10-01，线上「一直等待验证结果」）：
    box-server 的 conf/setting.json 里残留了 "conf_path": ""，
    而 setting.json 的优先级高于环境变量，于是 config.conf_path 变成空串。
    AuthManager.__init__ 直接拿它拼路径：
        os.path.join("", "auth.json") == "auth.json"   # 没有目录部分
    写盘时 os.path.dirname("auth.json") 得到 ''，临时文件创建失败：
        [AUTH-STATE] 写入 auth.json 失败: [Errno 2] No such file or directory: ''
    于是验证码验证成功后 token 存不下来，后台登录周期一过 sids 就被刷回
    expired，前端轮询 sids[sid]=="ok" 永远为假 → 一直显示「等待验证结果」。

运行：  .venv/bin/python test/test_conf_path_fallback.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xiaomusic.auth_state import AuthState  # noqa: E402
from xiaomusic.config import Config  # noqa: E402


class _Log:
    def __init__(self):
        self.warnings = []

    def debug(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass

    def warning(self, msg, *a, **k):
        self.warnings.append(msg % a if a else msg)

    def error(self, msg, *a, **k):
        self.warnings.append(msg % a if a else msg)

    def exception(self, *a, **k):
        pass


def test_ensure_conf_dir_replaces_empty_string():
    cfg = Config()
    cfg.conf_path = ""
    with tempfile.TemporaryDirectory() as tmp:
        # 空串应回落成默认目录；这里在临时目录里跑，避免污染工作区
        cwd = os.getcwd()
        os.chdir(tmp)
        try:
            resolved = cfg.ensure_conf_dir()
            assert resolved, "空 conf_path 必须被兜底成可用目录"
            assert cfg.conf_path == resolved
            assert os.path.isdir(resolved)
        finally:
            os.chdir(cwd)


def test_setting_json_empty_conf_path_is_fixed_before_use():
    """复刻事故路径：setting.json 里的空串经过 ensure_conf_dir 后必须可用。"""
    cfg = Config()
    cfg.update_config({"conf_path": ""})
    assert cfg.conf_path == "", "前置条件：空串确实会被 setting.json 写进配置"
    with tempfile.TemporaryDirectory() as tmp:
        cwd = os.getcwd()
        os.chdir(tmp)
        try:
            cfg.ensure_conf_dir()
            auth_path = os.path.join(cfg.conf_path, "auth.json")
            # 关键断言：路径必须有目录部分，否则 dirname 为空、临时文件写不出去
            assert os.path.dirname(auth_path), (
                "拼出的 auth.json 路径没有目录部分，写盘会静默失败"
            )
            state = AuthState(auth_path, _Log())
            state.data["account"] = "tester"
            state.save()
            assert os.path.isfile(auth_path), "auth.json 必须真的落盘"
        finally:
            os.chdir(cwd)


def test_empty_conf_path_without_fallback_loses_state():
    """反证：不兜底时空串会让写入失败（保留现场，防止有人把兜底删掉）。"""
    with tempfile.TemporaryDirectory() as tmp:
        cwd = os.getcwd()
        os.chdir(tmp)
        try:
            log = _Log()
            state = AuthState(os.path.join("", "auth.json"), log)
            state.data["account"] = "tester"
            state.save()
            assert not os.path.isfile("auth.json"), "空路径本不该写出文件"
            assert any("No such file or directory" in w for w in log.warnings), (
                f"应记录空路径写入失败，实际: {log.warnings}"
            )
        finally:
            os.chdir(cwd)


def test_auth_manager_prepares_conf_dir():
    """AuthManager 必须在拼路径之前规整 conf_path。"""
    import inspect

    from xiaomusic.auth import AuthManager

    src = inspect.getsource(AuthManager.__init__)
    assert "ensure_conf_dir" in src, (
        "AuthManager.__init__ 必须调用 config.ensure_conf_dir()，"
        "否则空 conf_path 会让 auth.json 永远存不下来"
    )


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
