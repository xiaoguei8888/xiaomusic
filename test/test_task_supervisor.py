"""校验 core.task_supervisor：命名、持引用、取消、异常必记日志。

运行：  .venv/bin/python test/test_task_supervisor.py
"""

import asyncio
import contextlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xiaomusic.core.task_supervisor import TaskSupervisor  # noqa: E402


class _RecordingLog:
    """logging.Logger 兼容的最小记录器（任意 level 都可用）。"""

    def __init__(self):
        self.records = []

    def __getattr__(self, level):
        def _log(message, *args, **kwargs):
            self.records.append((level, message))

        return _log


def test_add_holds_reference_and_name():
    async def main():
        sup = TaskSupervisor()

        async def work():
            await asyncio.sleep(0)
            return 42

        task = sup.add(work(), "work")
        assert task.get_name() == "work"
        assert sup.get("work") is task
        assert sup.active_names() == ["work"]
        assert sup.is_finish() is False
        assert await task == 42
        await asyncio.sleep(0)  # 让 done 回调结算统计
        assert sup.is_finish() is True
        assert sup.get("work") is None
        stats = sup.stats()
        assert (stats["created"], stats["completed"], stats["failed"]) == (1, 1, 0), (
            stats
        )
        assert stats["active"] == 0 and stats["active_names"] == []
        print("add_holds_reference_and_name OK", stats)

    asyncio.run(main())


def test_exception_is_always_logged():
    async def main():
        log = _RecordingLog()
        sup = TaskSupervisor(log=log, name="unit")

        async def boom():
            raise ValueError("bad command")

        task = sup.add(boom(), "boom")
        with contextlib.suppress(ValueError):
            await task
        await asyncio.sleep(0)
        levels = [level for level, _ in log.records]
        assert "error" in levels, log.records
        assert "bad command" in log.records[0][1], log.records
        stats = sup.stats()
        assert stats["failed"] == 1, stats
        assert "ValueError" in stats["last_error"], stats
        print("exception_is_always_logged OK", log.records, stats)

    asyncio.run(main())


def test_cancel_all_cancels_and_clears():
    async def main():
        sup = TaskSupervisor()

        async def forever():
            await asyncio.sleep(3600)

        t1 = sup.add(forever(), "a")
        t2 = sup.add(forever(), "b")
        assert sup.is_finish() is False
        cancelled = await sup.cancel_all()
        assert cancelled == 2, cancelled
        assert t1.cancelled() and t2.cancelled()
        await asyncio.sleep(0)
        assert sup.is_finish() is True
        assert sup.stats()["cancelled"] == 2, sup.stats()
        assert await sup.cancel_all() == 0, "second cancel_all must be a no-op"
        print("cancel_all_cancels_and_clears OK", sup.stats())

    asyncio.run(main())


def test_replace_cancels_previous_same_name():
    async def main():
        sup = TaskSupervisor()

        async def forever():
            await asyncio.sleep(3600)

        old = sup.add(forever(), "timer")
        new = sup.add(forever(), "timer")
        with contextlib.suppress(asyncio.CancelledError):
            await old
        assert old.cancelled(), "replaced task must be cancelled"
        assert sup.get("timer") is new
        assert sup.active_names() == ["timer"]
        assert await sup.cancel_all() == 1
        print("replace_cancels_previous_same_name OK")

    asyncio.run(main())


def test_replace_false_uses_suffix():
    async def main():
        sup = TaskSupervisor()

        async def forever():
            await asyncio.sleep(3600)

        first = sup.add(forever(), "timer", replace=False)
        second = sup.add(forever(), "timer", replace=False)
        assert sup.get("timer") is first
        assert sup.get("timer#2") is second
        assert sorted(sup.active_names()) == ["timer", "timer#2"]
        assert await sup.cancel_all() == 2
        print("replace_false_uses_suffix OK")

    asyncio.run(main())


def test_add_existing_task_is_registered_not_wrapped():
    async def main():
        sup = TaskSupervisor()

        async def work():
            return 1

        task = asyncio.create_task(work())
        registered = sup.add(task, "renamed")
        assert registered is task
        assert task.get_name() == "renamed"
        assert sup.get("renamed") is task
        assert await task == 1
        await asyncio.sleep(0)
        assert sup.stats()["completed"] == 1
        print("add_existing_task_is_registered_not_wrapped OK")

    asyncio.run(main())


def test_empty_supervisor_stats_and_finish():
    sup = TaskSupervisor(name="empty")
    assert sup.is_finish() is True
    assert sup.active_names() == []
    stats = sup.stats()
    assert (
        stats["name"] == "empty"
        and stats["created"] == 0
        and stats["last_error"] is None
    )
    print("empty_supervisor_stats_and_finish OK", stats)


def test_add_rejects_non_awaitable():
    async def main():
        sup = TaskSupervisor()
        try:
            sup.add(123, "bad")
        except TypeError as exc:
            assert "awaitable" in str(exc), exc
        else:
            raise AssertionError("expected TypeError")
        print("add_rejects_non_awaitable OK")

    asyncio.run(main())


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
