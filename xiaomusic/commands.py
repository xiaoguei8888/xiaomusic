"""显式命令注册表。

历史遗留：命令派发靠 getattr(xiaomusic, opvalue)，口令写错只在运行时才炸，
也无法静态校验。这里集中登记所有可派发命令，作为口令表(key_word_dict)与
实际方法的单一事实源，供 CommandHandler 校验/解析。

后续阶段会把各 handler 从 god-facade 搬到领域模块，本表是其迁移锚点。
"""

from __future__ import annotations

# 所有可被命令层派发的命令名（即 XiaoMusic 上的方法名）。
# key_word_dict 的每个值、以及 match_cmd 直接返回的 "select_index"/"exec"，
# 都必须落在此集合内，否则视为配置或代码错误。
COMMAND_NAMES: frozenset[str] = frozenset(
    {
        # 播放传输
        "play",
        "playlocal",
        "play_music_list",
        "play_music_list_index",
        "play_next",
        "play_prev",
        "stop",
        "stop_after_minute",
        # 播放模式
        "set_play_type_one",
        "set_play_type_all",
        "set_play_type_rnd",
        "set_play_type_sin",
        "set_play_type_seq",
        # 内容 / 收藏
        "gen_music_list",
        "cmd_del_music",
        "add_to_favorites",
        "del_from_favorites",
        # 在线
        "online_play",
        "online_playlist_play",
        "singer_play",
        # 交互（由 match_cmd 直接返回，不在口令表中）
        "select_index",
        "exec",
    }
)


def is_command(name: str) -> bool:
    """判断 name 是否为已登记命令。"""
    return name in COMMAND_NAMES


def resolve(instance, name: str):
    """在实例上解析命令的可调用对象。

    未知命令返回 None（而不是抛 AttributeError），由调用方决定如何记录。
    """
    if name not in COMMAND_NAMES:
        return None
    return getattr(instance, name, None)
