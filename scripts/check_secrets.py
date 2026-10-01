#!/usr/bin/env python3
"""提交前保密检查：阻止账号 / 密码 / token 进入 git。

用法：
    python scripts/check_secrets.py <file> [<file> ...]   # pre-commit 会传文件名
    python scripts/check_secrets.py --staged              # 检查暂存区
    python scripts/check_secrets.py --all                 # 检查所有已跟踪文件

命中即退出码 1，并打印文件名 + 规则名（只报长度，不回显密钥本身）。
纯标准库，不依赖 pre-commit 也能单独跑。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

# 这些路径一旦入库就等于泄露，无论内容是什么
BLOCKED_PATH_RULES: list[tuple[str, re.Pattern]] = [
    ("运行配置目录 conf/", re.compile(r"(^|/)conf/")),
    ("小米 token 文件", re.compile(r"(^|/)[.]mi[.]token$")),
    ("登录态文件", re.compile(r"(^|/)auth[.]json$")),
    ("设备 ID 文件", re.compile(r"(^|/)[.]device_id$")),
    ("登录态备份目录", re.compile(r"(^|/)[.]auth_backup/")),
    ("token 文件", re.compile(r"[.]token$")),
    ("运行配置 setting.json", re.compile(r"(^|/)setting[.]json$")),
    ("环境变量文件", re.compile(r"(^|/)[.]env([.].+)?$")),
]

# 占位符不算泄露（示例文档、模板、compose 变量引用）
PLACEHOLDERS = {
    "",
    "xxx",
    "xx",
    "password",
    "passwd",
    "your_password",
    "yourpassword",
    "changeme",
    "change_me",
    "todo",
    "test",
    "dummy",
    "fake",
    "secret",
    "密码",
    "账号",
    "你的小米密码",
    "你的小米账号",
    "你的设备did",
}
PLACEHOLDER_RE = re.compile(r"^(\$[{].*\}|<.*>|\*+|-+|\$[A-Z_]+)$")

# 内容规则：正则里的第 1 个捕获组是被判定的敏感值
CONTENT_RULES: list[tuple[str, re.Pattern]] = [
    ("JSON 明文密码", re.compile(r'"(?:password|passwd|pwd)"\s*:\s*"([^"]*)"', re.I)),
    ("JSON 明文账号", re.compile(r'"(?:account|username)"\s*:\s*"([^"]*)"', re.I)),
    ("JSON 明文 cookie", re.compile(r'"cookie"\s*:\s*"([^"]*)"', re.I)),
    (
        "环境变量明文密码",
        # 值里排除反斜杠：否则 "-e MI_PASS=密码\n" 会把字面 \n 也算进密码
        re.compile(
            r"(?:MI_PASS|XIAOMUSIC_HTTPAUTH_PASSWORD)\s*[=:]\s*([^\s\\]+)", re.I
        ),
    ),
    (
        "小米 token",
        re.compile(
            # 值必须带引号：否则会把 "serviceToken": service_token 这类代码误报
            r"(?:passToken|serviceToken|ssecurity|pass_token|access_token)"
            r'["\']?\s*[=:]\s*["\']([A-Za-z0-9+/=_\-]{12,})["\']',
            re.I,
        ),
    ),
    ("私钥", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]

MIN_LEN = {"JSON 明文账号": 6, "JSON 明文密码": 4, "JSON 明文 cookie": 4}
MAX_BYTES = 2 * 1024 * 1024

# 行级白名单：测试夹具确实要写假密钥时，在该行加这个标记即可放行
ALLOW_MARKER = "check-secrets:allow"


def _is_placeholder(value: str) -> bool:
    v = value.strip().strip('"').strip("'")
    return v.lower() in PLACEHOLDERS or bool(PLACEHOLDER_RE.match(v))


def check_path(path: str) -> list[tuple[str, str]]:
    """路径规则：返回 [(规则名, 说明)]。"""
    norm = path.replace("\\", "/").lstrip("./")
    return [
        (rule, "该路径禁止入库") for rule, rx in BLOCKED_PATH_RULES if rx.search(norm)
    ]


def check_content(path: str) -> list[tuple[str, str]]:
    """内容规则：返回 [(规则名, 说明)]，说明里只含长度，不含密钥。"""
    try:
        with open(path, "rb") as f:
            raw = f.read(MAX_BYTES)
    except OSError:
        return []
    if b"\x00" in raw[:1024]:
        return []
    text = raw.decode("utf-8", errors="ignore")
    # 带 ALLOW_MARKER 的行不参与内容检查
    text = "\n".join(line for line in text.splitlines() if ALLOW_MARKER not in line)
    found = []
    for rule, rx in CONTENT_RULES:
        for m in rx.finditer(text):
            if rule == "私钥":
                found.append((rule, "命中私钥块"))
                continue
            value = m.group(1) if m.groups() else ""
            if len(value.strip()) < MIN_LEN.get(rule, 3):
                continue
            if _is_placeholder(value):
                continue
            found.append((rule, f"明文值长度={len(value.strip())}"))
    return found


def find_violations(paths: list[str]) -> list[tuple[str, str, str]]:
    out = []
    for path in paths:
        for rule, detail in check_path(path):
            out.append((path, rule, detail))
        for rule, detail in check_content(path):
            out.append((path, rule, detail))
    return out


def _tracked_files() -> list[str]:
    r = subprocess.run(["git", "ls-files"], capture_output=True, text=True)
    return [p for p in r.stdout.splitlines() if p]


def _staged_files() -> list[str]:
    r = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        capture_output=True,
        text=True,
    )
    return [p for p in r.stdout.splitlines() if p]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="提交前保密检查")
    parser.add_argument("paths", nargs="*", help="要检查的文件")
    parser.add_argument("--all", action="store_true", help="检查所有已跟踪文件")
    parser.add_argument("--staged", action="store_true", help="检查暂存区文件")
    args = parser.parse_args(argv)

    if args.all:
        paths = _tracked_files()
    elif args.staged:
        paths = _staged_files()
    else:
        paths = args.paths

    violations = find_violations(paths)
    if violations:
        print("发现可能的凭据泄露，已阻止提交：", file=sys.stderr)
        for path, rule, detail in violations:
            print(f"  {path}  [{rule}] {detail}", file=sys.stderr)
        print(
            "",
            file=sys.stderr,
        )
        print(
            "如果确认是占位符/测试数据，请改成明确的占位写法"
            "（空串、<...>、由环境变量注入），或把该文件加入 .gitignore。",
            file=sys.stderr,
        )
        return 1
    print(f"check_secrets OK（检查 {len(paths)} 个文件，未发现明文凭据）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
