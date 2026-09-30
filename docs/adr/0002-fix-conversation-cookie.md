# ADR-0002 对话接口 cookie 与失败退避

- 状态：已采纳（M0 补丁）
- 背景：真机运行发现语音轮询每分钟约 180 条 HTTP 400，语音口令完全收不到。
- 根因：小米对话接口要求 `deviceId + userId + serviceToken` 三件套，代码只传了 `deviceId`；`const.py` 里早定义了 `COOKIE_TEMPLATE` 却从未使用。实测：仅 deviceId → 400 MissingRequestCookieException；加 userId → 401；三件套齐 → 200。
- 决策：按模板组装 cookie（缺字段自动省略并告警一次）；失败指数退避 2s→60s 且按原因去重日志；连续失败提示可切换 `XIAOMUSIC_GET_ASK_BY_MINA`。
- 验证：真机重启后 400 归零；新增 `test/test_conversation_cookies.py` 锁定行为。
