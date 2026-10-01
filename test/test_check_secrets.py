"""校验提交前的保密守卫：该拦的拦、该放过的放过。

运行：  .venv/bin/python test/test_check_secrets.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import check_secrets  # noqa: E402


def _write(tmp, name, text):
    path = os.path.join(tmp, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def test_blocked_paths_are_rejected():
    for path in (
        "conf/setting.json",
        "xiaomusic/conf/setting.json",
        "conf/.mi.token",
        "conf/auth.json",
        "conf/.device_id",
        "conf/.auth_backup/x",
        "deploy/.env",
    ):
        assert check_secrets.check_path(path), f"{path} 应当被路径规则拦截"
    print("blocked_paths_are_rejected OK")


def test_normal_paths_pass_path_rules():
    for path in (
        "config-example.json",
        "xiaomusic/static/default/setting.js",
        "README.md",
    ):
        assert not check_secrets.check_path(path), f"{path} 不应被路径规则拦截"
    print("normal_paths_pass_path_rules OK")


def test_plaintext_credentials_are_caught():
    with tempfile.TemporaryDirectory() as tmp:
        leak = _write(
            tmp,
            "leak.json",
            '{"account": "13800138000", "password": "hunter2xyz"}',  # check-secrets:allow 测试夹具
        )
        rules = {rule for _p, rule, _d in check_secrets.find_violations([leak])}
        assert "JSON 明文账号" in rules, rules
        assert "JSON 明文密码" in rules, rules
    print("plaintext_credentials_are_caught OK")


def test_placeholders_are_allowed():
    """模板里的空值 / 中文占位 / 变量引用都不算泄露。"""
    var_ref = chr(36) + "{MI_PASS:-}"  # check-secrets:allow 测试夹具
    body = (
        '{"account": "", "password": "", "cookie": ""}\n'
        "-e MI_USER=你的小米账号 -e MI_PASS=你的小米密码\n"
        "MI_PASS: " + var_ref + "\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, "example.json", body)
        assert check_secrets.check_content(path) == [], check_secrets.check_content(
            path
        )
    print("placeholders_are_allowed OK")


def test_code_reference_is_not_a_leak():
    """代码里引用同名变量不应当误报。"""
    body = 'self.data["serviceToken"] = service_token\n"passToken": None\n'
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, "auth_state.py", body)
        assert check_secrets.check_content(path) == [], check_secrets.check_content(
            path
        )
    print("code_reference_is_not_a_leak OK")


def test_quoted_token_is_caught():
    leak = '{"serviceToken": "AbCdEf0123456789+/="}'  # check-secrets:allow 测试夹具
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, "auth.json", leak)
        rules = {rule for _p, rule, _d in check_secrets.find_violations([path])}
        assert "小米 token" in rules, rules
    print("quoted_token_is_caught OK")


def test_private_key_is_caught():
    leak = "-----BEGIN RSA PRIVATE KEY-----\nAAA\n"  # check-secrets:allow 测试夹具
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, "id_rsa", leak)
        rules = {rule for _p, rule, _d in check_secrets.find_violations([path])}
        assert "私钥" in rules, rules
    print("private_key_is_caught OK")


def test_allow_marker_skips_line():
    """测试夹具可以用 check-secrets:allow 显式放行。"""
    body = '{"password": "hunter2xyz"}  # check-secrets:allow' + "\n"
    assert check_secrets.ALLOW_MARKER in body
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(tmp, "fixture.json", body)
        assert check_secrets.check_content(path) == [], check_secrets.check_content(
            path
        )
    print("allow_marker_skips_line OK")


def test_repository_itself_is_clean():
    """仓库当前所有已跟踪文件都必须通过守卫。"""
    tracked = check_secrets._tracked_files()
    violations = check_secrets.find_violations(tracked)
    assert violations == [], violations
    print("repository_itself_is_clean OK", len(tracked), "files")


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
