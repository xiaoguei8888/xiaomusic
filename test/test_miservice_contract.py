"""依赖契约测试：真实的 miservice 必须支持 login_flow 的调用形式。

事故背景（2026-10-01，线上 500）：
    test/test_login_flow.py 用 _FakeMiAccount 替换了 login_flow.MiAccount，
    而那个假实现自己就带着 otp_callback 形参，于是「真实依赖到底支不支持」
    从未被验证过：
      - 本地 .venv 恰好是 pip 装的 miservice 3.0.1（支持 otp_callback）；
      - 镜像按 pyproject 装的是 miservice-fork 2.9.3（不支持），
        整个包里 otp_callback 只出现 0 次。
    结果容器起来了、健康检查也通过，一点「登录」就 500：
      TypeError: MiAccount.__init__() got an unexpected keyword argument 'otp_callback'

    这个文件只盯真实签名与真实依赖声明，不允许再被 mock 掩盖。

运行：  .venv/bin/python test/test_miservice_contract.py
"""

import importlib.metadata as md
import inspect
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from miservice import MiAccount, MiIOService, MiNAService, miio_command  # noqa: E402

try:  # Python >= 3.11
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    tomllib = None

PYPROJECT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pyproject.toml"
)


def _declared_dependencies():
    """读取 pyproject 声明的运行时依赖；无 tomllib 时返回 None。"""
    if tomllib is None:  # pragma: no cover
        return None
    with open(PYPROJECT, "rb") as f:
        return tomllib.load(f)["project"]["dependencies"]


def _dist_name(requirement):
    """从 'miservice>=3.0.1,<4.0.0' 这类声明里取出包名。"""
    return re.split(r"[<>=!~;\[\s]", requirement.strip(), maxsplit=1)[0]


def _version_tuple(text):
    return tuple(int(part) for part in re.findall(r"\d+", text)[:3])


def test_real_miaccount_accepts_login_flow_call():
    """login_flow._new_account() 的实参形式必须被真实 MiAccount 接受。"""
    signature = inspect.signature(MiAccount.__init__)
    # 与 login_flow.py 一一对应：session, username, password, token_store, otp_callback
    signature.bind(object(), "account", "password", object(), otp_callback=None)


def test_real_miaccount_exposes_otp_callback_parameter():
    assert "otp_callback" in inspect.signature(MiAccount.__init__).parameters


def test_code_imports_are_provided_by_real_package():
    """auth.py / device_player.py 用到的名字必须真的存在。"""
    for obj in (MiAccount, MiNAService, MiIOService, miio_command):
        assert obj is not None
    assert callable(miio_command)


def test_declared_dependency_is_miservice_not_stale_fork():
    deps = _declared_dependencies()
    if deps is None:  # pragma: no cover
        return
    names = {_dist_name(d) for d in deps}
    assert "miservice" in names, f"pyproject 未声明 miservice：{deps}"
    assert "miservice-fork" not in names, (
        "miservice-fork 最高只到 2.9.3，MiAccount 没有 otp_callback，会让登录接口 500"
    )


def test_installed_provider_is_the_one_declared():
    """已安装的 miservice 必须够新，且不能同时装着 miservice-fork。"""
    assert _version_tuple(md.version("miservice")) >= (3, 0, 1)
    try:
        fork_version = md.version("miservice-fork")
    except md.PackageNotFoundError:
        fork_version = None
    assert fork_version is None, (
        f"miservice-fork {fork_version} 与 miservice 提供同一个 miservice 包，不能共存"
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
