"""每个设备的播放状态唯一所有者（兼容层）。

实现已迁移到 xiaomusic.core.state（L0）；本模块只做 re-export，
保证历史 import 路径 xiaomusic.device_state 不变。
"""

from xiaomusic.core.state import (
    DeviceLike,
    DeviceStateStore,
    PlayerSnapshot,
    StateStore,
)

__all__ = [
    "DeviceLike",
    "DeviceStateStore",
    "PlayerSnapshot",
    "StateStore",
]
