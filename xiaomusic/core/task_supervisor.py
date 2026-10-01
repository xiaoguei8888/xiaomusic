"""统一任务监管（L0）。

替代散落在门面（xiaomusic.running_task）与各设备定时器里的
asyncio.create_task + 手工 cancel 逻辑：

* 命名：每个任务有稳定名字，便于日志定位与替换；
* 持引用：supervisor 持有 Task 强引用，避免「任务被 GC 掉」的经典坑；
* 取消：cancel_all() 取消并回收全部在管任务；
* 异常必记日志：done 回调里读取 task.exception() 并写入注入的 log，
  未注入时退回模块 logger，绝不静默吞掉。

接口：add(target, name=None, *, replace=True) / cancel_all() / stats()。

本模块只依赖标准库，不得 import 任何 xiaomusic 业务模块。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

_LOGGER = logging.getLogger("xiaomusic.core.task_supervisor")


def _default_task_name(target: Any) -> str:
    for attr in ("__qualname__", "__name__"):
        value = getattr(target, attr, None)
        if isinstance(value, str) and value:
            return value
    return "task"


class TaskSupervisor:
    """一个命名、可取消、可统计的 asyncio 任务容器。

    log 需为 logging.Logger 兼容对象（info/warning/error/exception）；
    传入项目里的 self.log 即可让任务异常进入同一日志管道。
    """

    def __init__(self, log: Any = None, name: str = "tasks") -> None:
        self.name = name
        self._log = log
        self._tasks: dict[str, asyncio.Task] = {}
        self._created = 0
        self._completed = 0
        self._cancelled = 0
        self._failed = 0
        self._last_error: BaseException | None = None

    def __repr__(self) -> str:
        return f"<TaskSupervisor name={self.name!r} active={self.active_names()!r}>"

    # -- 登记 ---------------------------------------------------------------
    def add(
        self, target: Any, name: str | None = None, *, replace: bool = True
    ) -> asyncio.Task:
        """登记一个协程 / awaitable / 已存在的 Task，返回其 Task。"""
        return self.create_task(target, name, replace=replace)

    def create_task(
        self, target: Any, name: str | None = None, *, replace: bool = True
    ) -> asyncio.Task:
        """创建并登记任务。

        replace=True（默认）：同名旧任务仍在运行时先取消它；
        replace=False：为同名任务追加 #2/#3 后缀。
        """
        task = self._coerce_task(target, name)
        key = name or task.get_name()
        existing = self._tasks.get(key)
        if existing is not None and existing is not task:
            if not existing.done():
                if replace:
                    self._write_log(
                        "warning", f"{self.name}: replace running task {key!r}"
                    )
                    existing.cancel()
                else:
                    key = self._unique_name(key)
            else:
                self._tasks.pop(key, None)
        self._created += 1
        self._tasks[key] = task
        task.add_done_callback(lambda done, _key=key: self._on_done(_key, done))
        return task

    def _coerce_task(self, target: Any, name: str | None) -> asyncio.Task:
        if isinstance(target, asyncio.Task):
            if name:
                target.set_name(name)
            return target
        if asyncio.iscoroutine(target):
            if name:
                return asyncio.create_task(target, name=name)
            return asyncio.create_task(target)
        if asyncio.isfuture(target):
            return asyncio.ensure_future(target)
        raise TypeError(
            f"TaskSupervisor.add expects coroutine/awaitable/Task, got {type(target)!r}"
        )

    def _unique_name(self, key: str) -> str:
        suffix = 2
        while f"{key}#{suffix}" in self._tasks:
            suffix += 1
        return f"{key}#{suffix}"

    # -- 完成回调 -----------------------------------------------------------
    def _on_done(self, key: str, task: asyncio.Task) -> None:
        if self._tasks.get(key) is task:
            self._tasks.pop(key, None)
        if task.cancelled():
            self._cancelled += 1
            self._write_log("info", f"{self.name}: task {key!r} cancelled")
            return
        error = task.exception()
        if error is not None:
            self._failed += 1
            self._last_error = error
            self._write_log(
                "error", f"{self.name}: task {key!r} failed: {error!r}", error
            )
            return
        self._completed += 1

    # -- 取消 ---------------------------------------------------------------
    async def cancel_all(self) -> int:
        """取消并等待全部在管任务，返回实际取消的数量。"""
        pending = [(key, task) for key, task in self._tasks.items() if not task.done()]
        if not pending:
            self._write_log("info", f"{self.name}: cancel_all no active task")
            return 0
        for key, task in pending:
            self._write_log("info", f"{self.name}: cancel task {key!r}")
            task.cancel()
        await asyncio.gather(*(task for _, task in pending), return_exceptions=True)
        return len(pending)

    # -- 查询 ---------------------------------------------------------------
    @property
    def active(self) -> list[asyncio.Task]:
        return [task for task in self._tasks.values() if not task.done()]

    def active_names(self) -> list[str]:
        return [key for key, task in self._tasks.items() if not task.done()]

    def get(self, name: str) -> asyncio.Task | None:
        """取在管任务；已完成或不存在返回 None。"""
        task = self._tasks.get(name)
        if task is None or task.done():
            return None
        return task

    def is_finish(self) -> bool:
        """是否已无在管任务（供门面 is_task_finish 复用）。"""
        return not self.active

    def stats(self) -> dict[str, Any]:
        """返回任务计数快照，便于观测/健康检查。"""
        return {
            "name": self.name,
            "created": self._created,
            "active": len(self.active),
            "completed": self._completed,
            "cancelled": self._cancelled,
            "failed": self._failed,
            "active_names": self.active_names(),
            "last_error": repr(self._last_error)
            if self._last_error is not None
            else None,
        }

    # -- 日志 ---------------------------------------------------------------
    def _write_log(
        self, level: str, message: str, error: BaseException | None = None
    ) -> None:
        log = self._log if self._log is not None else _LOGGER
        writer = getattr(log, level, None)
        if not callable(writer):
            writer = getattr(log, "info", None)
        if not callable(writer):
            return
        if error is not None:
            writer(message, exc_info=error)
        else:
            writer(message)


__all__ = ["TaskSupervisor"]
