#!/usr/bin/env python3
"""M0 精简版配置迁移脚本。

把 conf/setting.json 中属于"在线音乐 / 下载 / 缓存 / 代理"等已移除能力的
配置键删除，只保留"本地音乐 + 音箱控制"所需的键。

用法：
    .venv/bin/python scripts/migrate_settings.py [setting.json 路径]

不传路径时默认使用仓库根目录下的 conf/setting.json。
脚本幂等：重复执行不会有新的改动，会打印本次实际删除的键。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 需要从 setting.json 中清除的键（与 xiaomusic/config.py 已删除的字段一致）
REMOVED_KEYS: tuple[str, ...] = (
    "temp_path",
    "download_path",
    "cache_dir",
    "cache_max_size_mb",
    "cache_song_name",
    "proxy",
    "loudnorm",
    "search_prefix",
    "music_list_url",
    "music_list_json",
    "disable_download",
    "use_music_api",
    "use_music_audio_id",
    "use_music_id",
    "keywords_online_play",
    "keywords_online_playlist_play",
    "keywords_singer_play",
    "enable_yt_dlp_cookies",
    "web_music_proxy",
    "enable_auto_clean_temp",
    "enable_config_example",
    "remove_id3tag",
    "convert_to_mp3",
)


# 随在线音乐一起移除的动作，key_word_dict / key_match_order 中指向它们
# 的口令属于悬空映射，一并清理。
DANGLING_ACTIONS: tuple[str, ...] = (
    "online_play",
    "online_playlist_play",
    "singer_play",
)


def default_setting_path() -> Path:
    # scripts/migrate_settings.py -> 仓库根目录
    repo_root = Path(__file__).resolve().parents[1]
    return repo_root / "conf" / "setting.json"


def strip_dangling_keywords(data: dict) -> list[str]:
    """清除 key_word_dict / key_match_order 中指向已移除动作的口令。

    返回本次被清除的口令列表（幂等：再次执行返回空列表）。
    """
    removed_phrases: list[str] = []
    key_word_dict = data.get("key_word_dict")
    if isinstance(key_word_dict, dict):
        for phrase in [k for k, v in key_word_dict.items() if v in DANGLING_ACTIONS]:
            del key_word_dict[phrase]
            removed_phrases.append(phrase)

    key_match_order = data.get("key_match_order")
    if isinstance(key_match_order, list) and removed_phrases:
        data["key_match_order"] = [
            phrase for phrase in key_match_order if phrase not in removed_phrases
        ]
    return removed_phrases


def migrate(setting_path: Path) -> tuple[list[str], list[str]]:
    """删除过时键 / 悬空口令并写回，返回 (被删键, 被删口令)。"""
    with setting_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    removed = [key for key in REMOVED_KEYS if key in data]
    for key in removed:
        del data[key]

    removed_phrases = strip_dangling_keywords(data)

    if removed or removed_phrases:
        with setting_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")

    return removed, removed_phrases


def main() -> int:
    if len(sys.argv) > 2:
        print(f"用法: {sys.argv[0]} [setting.json 路径]", file=sys.stderr)
        return 2

    setting_path = Path(sys.argv[1]) if len(sys.argv) == 2 else default_setting_path()
    if not setting_path.exists():
        print(f"配置文件不存在: {setting_path}", file=sys.stderr)
        return 1

    removed, removed_phrases = migrate(setting_path)
    if removed:
        print(f"已从 {setting_path} 删除 {len(removed)} 个过时配置键:")
        for key in removed:
            print(f"  - {key}")
    if removed_phrases:
        print(
            f"已清除 {len(removed_phrases)} 个指向已移除动作的口令 "
            f"({', '.join(DANGLING_ACTIONS)}):"
        )
        for phrase in removed_phrases:
            print(f"  - {phrase}")
    if not removed and not removed_phrases:
        print(f"{setting_path} 无需清理，已是最新配置")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
