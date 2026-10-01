"""播放条「播放/暂停」图标的回归测试。

背景（用户可见缺陷）：
设备播放时图标永远停在 play_circle_outline —— 只有本机播放
（loadAndPlayMusic）会改图标，而设备播放状态来自 WebSocket 推送，
onmessage 只更新了歌曲文字、没有同步图标。

规则：图标只反映「当前控制目标」的播放状态 ——
本机播放（web_device）看 <audio>，设备播放看推送的 is_playing。
"""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "xiaomusic" / "static" / "default"


def read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def strip_comments(text: str) -> str:
    """去掉 JS 注释，避免注释里的字样让断言永远通过。"""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", line) for line in text.splitlines())


def test_play_pause_icon_element_and_initial_state():
    html = read("index.html")
    m = re.search(r'<span id="playPauseIcon"[^>]*>([^<]+)</span>', html)
    assert m, "播放条缺少 #playPauseIcon"
    assert m.group(1) == "play_circle_outline", "初始图标应为 play_circle_outline"


def test_icon_helper_maps_both_states():
    js = strip_comments(read("md.js"))
    m = re.search(r"function setPlayPauseIcon\(playing\)\s*\{(.*?)\n\}", js, flags=re.S)
    assert m, "缺少统一的 setPlayPauseIcon"
    body = m.group(1)
    assert "pause_circle_outline" in body, "播放中应显示暂停图标"
    assert "play_circle_outline" in body, "暂停/停止时应显示播放图标"


def test_websocket_playing_state_updates_icon():
    """核心回归：设备在播放时图标必须变成暂停按钮。"""
    js = strip_comments(read("md.js"))
    m = re.search(r"ws\.onmessage = \(event\) => \{(.*?)\n    \};", js, flags=re.S)
    assert m, "未找到 WebSocket onmessage"
    body = m.group(1)
    assert "is_playing" in body, "推送里应有播放状态"
    assert "setPlayPauseIcon" in body, "设备播放状态变化没有同步播放/暂停图标"


def test_local_player_icon_follows_audio_element():
    js = strip_comments(read("md.js"))
    assert "isWebDeviceSelected" in js, "缺少「是否本机播放」的判断"
    m = re.search(r"function updateWebPlayingUI\(\)\s*\{(.*?)\n\}", js, flags=re.S)
    assert m, "未找到 updateWebPlayingUI"
    body = m.group(1)
    assert "setPlayPauseIcon" in body, "本机 play/pause/停止没有同步图标"


def test_no_icon_write_bypasses_helper():
    """历史写法 playMusicIcon.textContent = ... 必须消失，否则又会两边打架。"""
    js = strip_comments(read("md.js"))
    assert "playMusicIcon.textContent" not in js, "仍有绕过 setPlayPauseIcon 的图标写入"
