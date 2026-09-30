"""极简依赖容器。

设计目标（对应 M1 目标 8：模块注入与移除简单）：
- 具体实现类名只出现在 bootstrap/ 与各 Module 内部；
- 依赖按注册顺序解析，构造顺序不再硬编码在门面里；
- 支持单例（默认）与工厂两种作用域；
- 不引入第三方依赖。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class ContainerError(RuntimeError):
    """容器解析失败。"""


class Container:
    """极简服务容器。

    用法：
        c = Container()
        c.register("config", lambda: Config())
        c.register_instance("log", logger)
        cfg = c.resolve("config")
    """

    def __init__(self) -> None:
        self._factories: dict[str, Callable[["Container"], Any]] = {}
        self._instances: dict[str, Any] = {}
        self._resolving: list[str] = []

    # ---------- 注册 ----------

    def register(self, name: str, factory: Callable[["Container"], Any]) -> None:
        """注册一个惰性单例工厂。"""
        if name in self._factories or name in self._instances:
            raise ContainerError(f"服务已注册: {name}")
        self._factories[name] = factory

    def register_instance(self, name: str, instance: Any) -> None:
        """注册一个已构造好的实例。"""
        if name in self._factories or name in self._instances:
            raise ContainerError(f"服务已注册: {name}")
        self._instances[name] = instance

    def has(self, name: str) -> bool:
        return name in self._instances or name in self._factories

    # ---------- 解析 ----------

    def resolve(self, name: str) -> Any:
        """解析服务；结果被缓存为单例。"""
        if name in self._instances:
            return self._instances[name]
        factory = self._factories.get(name)
        if factory is None:
            raise ContainerError(f"服务未注册: {name}（已注册: {sorted(self.names())}）")
        if name in self._resolving:
            cycle = " -> ".join([*self._resolving, name])
            raise ContainerError(f"检测到循环依赖: {cycle}")
        self._resolving.append(name)
        try:
            instance = factory(self)
        finally:
            self._resolving.pop()
        self._instances[name] = instance
        return instance

    def resolve_optional(self, name: str, default: Any = None) -> Any:
        return self.resolve(name) if self.has(name) else default

    def names(self) -> list[str]:
        return sorted({*self._instances, *self._factories})


class Module:
    """能力模块协议。

    一个能力 = 一个 Module。新增/移除能力只需在 modules.py 增删一行：
    - register(c)：向容器注册本模块的服务；
    - routes()：返回要挂载的 APIRouter 列表（可为空）；
    - start(c) / stop(c)：可选生命周期钩子。
    """

    name: str = "module"

    def register(self, container: Container) -> None:  # pragma: no cover - 默认空实现
        return None

    def routes(self) -> list[Any]:  # pragma: no cover - 默认无路由
        return []

    async def start(self, container: Container) -> None:  # pragma: no cover
        return None

    async def stop(self, container: Container) -> None:  # pragma: no cover
        return None


class Application:
    """按模块装配应用。

    保持与现有 register_routers 的兼容：模块只负责"提供什么"，
    具体挂载顺序与标签由这里统一决定。
    """

    def __init__(self, container: Container | None = None) -> None:
        self.container = container or Container()
        self._modules: list[Module] = []

    def add(self, module: Module) -> "Application":
        self._modules.append(module)
        module.register(self.container)
        return self

    def modules(self) -> list[Module]:
        return list(self._modules)

    def routers(self) -> list[Any]:
        routers = []
        for module in self._modules:
            routers.extend(module.routes())
        return routers

    async def start(self) -> None:
        for module in self._modules:
            await module.start(self.container)

    async def stop(self) -> None:
        for module in reversed(self._modules):
            await module.stop(self.container)
