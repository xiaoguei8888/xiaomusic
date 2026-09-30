"""校验对话轮询的 cookie 组装与失败退避。

运行：  .venv/bin/python test/test_conversation_cookies.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xiaomusic.conversation import (  # noqa: E402
    BACKOFF_MAX_SEC,
    BACKOFF_START_SEC,
    ConversationPoller,
)


class _Log:
    def __init__(self):
        self.warnings = []
        self.infos = []

    def warning(self, *a, **k):
        self.warnings.append(a[0] % a[1:] if len(a) > 1 else a[0])

    def info(self, *a, **k):
        self.infos.append(a[0] % a[1:] if len(a) > 1 else a[0])

    def debug(self, *a, **k):
        pass


class _State:
    def __init__(self, data):
        self.data = data


class _Auth:
    def __init__(self, data):
        self._state = _State(data)


def _poller(state_data):
    return ConversationPoller(
        config=None, log=_Log(), auth_manager=_Auth(state_data), device_manager=None
    )


def test_cookies_include_full_template_fields():
    """回归：只传 deviceId 会被小米接口拒绝（400 MissingRequestCookieException）。"""
    p = _poller({"userId": 1084787080, "sids": {"micoapi": {"serviceToken": "TK"}}})
    got = p._build_ask_cookies("DEV1")
    assert got == {"deviceId": "DEV1", "userId": "1084787080", "serviceToken": "TK"}, got
    print("cookies_include_full_template_fields OK", sorted(got))


def test_cookies_tolerate_legacy_state():
    p = _poller({})
    got = p._build_ask_cookies("DEV1")
    assert got == {"deviceId": "DEV1"}, got
    # 并且给出一次告警
    assert p.log.warnings, "缺失字段应产生告警"
    print("cookies_tolerate_legacy_state OK")


def test_cookie_warning_is_deduplicated():
    p = _poller({})
    p._build_ask_cookies("DEV1")
    first = len(p.log.warnings)
    p._build_ask_cookies("DEV1")
    p._build_ask_cookies("DEV1")
    assert len(p.log.warnings) == first, p.log.warnings
    print("cookie_warning_is_deduplicated OK")


def test_backoff_grows_and_caps():
    p = _poller({})
    assert p._backoff_sec == BACKOFF_START_SEC
    seen = []
    for _ in range(10):
        p._note_failure("HTTP 400 boom")
        seen.append(p._backoff_sec)
    assert seen[0] == BACKOFF_START_SEC * 2, seen
    assert seen[-1] == BACKOFF_MAX_SEC, seen
    assert p._consecutive_failures == 10
    # 同原因只告警一次（去重），不会刷屏
    assert len(p.log.warnings) <= 2, p.log.warnings
    print("backoff_grows_and_caps OK", seen[:3], "...", seen[-1])


def test_success_resets_backoff():
    p = _poller({})
    p._note_failure("HTTP 400 boom")
    p._note_failure("HTTP 400 boom")
    p._note_success()
    assert p._consecutive_failures == 0
    assert p._backoff_sec == BACKOFF_START_SEC
    assert p._last_error == ""
    print("success_resets_backoff OK")


def test_failure_reason_change_is_reported():
    p = _poller({})
    p._note_failure("HTTP 400 boom")
    before = len(p.log.warnings)
    p._note_failure("TimeoutError: boom")
    assert len(p.log.warnings) > before, "失败原因变化应重新告警"
    print("failure_reason_change_is_reported OK")


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
