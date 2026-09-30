# ADR-0003 M1：内核化、容器化与可测性

- 状态：已采纳（M1）
- 背景：M0 精简后语句降到 4139，但门面 345 语句、伪全局（`_LazyProxy`）仍在、覆盖率仅 22.6%，且没有 pytest 基建。
- 决策：
  1. **L0 内核**：新增 `core/`（类型化事件、frozen 快照、统一异常、任务监管），旧 `events.py`/`device_state.py` 退化为 re-export，保证 import 路径不变、M0 调用方零改动。
  2. **门面瘦身**：18 条命令下沉到 `services/command_targets.py`（Mixin）。选择继承而非 `__getattr__`，因为 `test_registry.py` 校验的是类级 `getattr(XiaoMusic, name)`，动态实例属性会绕过校验。门面 345 → **168 语句**。
  3. **消除伪全局**：删除 `_LazyProxy`，路由统一走 `Depends(get_xiaomusic)` 或 `request.app.state`。
  4. **容器与模块协议**：新增 `bootstrap/`（Container + Module + Application），使"新增/移除能力 = 增删一行装配"具备可测试契约（`test/test_container.py`）。
  5. **测试基建**：引入 pytest + pytest-asyncio + pytest-cov，离线夹具（`test/conftest.py`），保留原有纯 assert 脚本；覆盖率 22.6% → **46%**（M1 目标 45%）。
- 影响：单文件职责更清晰；事件类型化后 WS 与落盘可只读快照；后续 M2 可把 `command_targets` 进一步演进为真正的 Service 类。
- 未纳入本 ADR 的已知缺陷（M2 处理）：最近新增歌单的 mtime 排序、"最近新增"顺序被字典序覆盖。
