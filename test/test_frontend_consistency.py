"""前端一致性测试：JS 引用的 DOM id 必须真实存在于 HTML。

背景（用户可见缺陷，连续踩了两次）：
1. setting.js 里曾有一整套登录/验证码逻辑（startLogin / submitVerify /
   showVerifyBox），引用的 #login-submit、#verify-code、#verify-box 等元素
   在 HTML 中根本不存在 —— 纯死代码，用户永远看不到验证入口。
2. 更隐蔽的一次：面板实际渲染的是 index.html（它自带一份登录表单并加载
   setting.js），而 setting.html 只是单独的设置页。第一次只修了 setting.html，
   用户刷新后面板上依然没有验证按钮。

教训：必须同时校验所有会加载 setting.js 的 HTML。
这类「JS 与 HTML 脱节」的问题不会被后端测试发现，所以在这里锁死。
"""

import re
import sys
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "xiaomusic" / "static" / "default"

# 会加载 setting.js 的页面，任何一个缺元素都会让用户看不到相应 UI
PAGES = ("index.html", "setting.html")


def read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def strip_comments(text: str) -> str:
    """去掉 JS 的 // 行注释和 /* */ 块注释，只看真正会执行的代码。"""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", line) for line in text.splitlines())


def html_ids(name: str) -> set:
    return set(re.findall(r'id="([^"]+)"', read(name)))


def js_referenced_ids() -> set:
    js = read("setting.js")
    refs = set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', js))
    refs |= set(re.findall(r"\$\('#([A-Za-z0-9_-]+)'\)", js))
    return refs


# 登录相关元素：面板上必须有，否则用户点不到验证入口
LOGIN_IDS = {
    "login-status",
    "login-account",
    "login-password",
    "login-submit",
    "login-verify-hint",
    "verify-label",
    "verify-code",
    "verify-submit",
    "verify-status",
}

# 登录方式 tab（账号密码 / 短信验证码）相关元素
TAB_IDS = {
    "tab-password",
    "tab-verify",
    "tab-verify-badge",
    "panel-password",
    "panel-verify",
    "verify-resend",
    "verify-back",
}

# 各页面独有的元素，不要求跨页面存在
PAGE_SPECIFIC = {"setting-version"}

# 由 JS 在运行时动态插入的元素，静态 HTML 里本来就不该有
RUNTIME_CREATED = {"verify-countdown"}


def test_login_elements_exist_in_every_page():
    """核心：每个会加载 setting.js 的页面都要有完整登录元素。

    第 2 次缺陷就是因为只检查了 setting.html，漏掉真正的面板 index.html。
    """
    for page in PAGES:
        ids = html_ids(page)
        missing = sorted(LOGIN_IDS - ids)
        assert not missing, f"{page} 缺少登录元素: {missing}"


def test_all_js_referenced_ids_exist_in_some_page():
    """任何 JS 引用的 id 至少要存在于某个页面，防止再次出现死代码。"""
    known = set()
    for page in PAGES:
        known |= html_ids(page)
    missing = sorted(
        i
        for i in js_referenced_ids()
        if i not in known and i not in PAGE_SPECIFIC and i not in RUNTIME_CREATED
    )
    assert not missing, f"setting.js 引用了所有页面都不存在的元素: {missing}"


def test_qrcode_dead_code_removed():
    """二维码登录已按 ADR-0001 移除，前端代码不应再引用其接口/元素。"""
    for page in PAGES:
        html = strip_comments(read(page))
        assert "qrcode" not in html.lower(), f"{page} 仍有二维码元素残留"

    js = strip_comments(read("setting.js"))
    assert "/api/login/qrcode" not in js, "JS 仍在调用已删除的二维码接口"
    assert "$('#qrcode-img')" not in js and '$("#qrcode-img")' not in js, (
        "JS 仍在操作已删除的二维码图片元素"
    )


def test_verify_button_is_wired():
    """降级状态下必须给用户一个可点的验证入口，而不是只写「需完成验证」。"""
    js = read("setting.js")
    assert "login-verify-hint" in js, "缺少验证入口按钮的绑定"
    assert "/api/login/verify/open" in js, "缺少显式发起验证的接口调用"


def test_panel_and_setting_page_login_forms_match():
    """两个页面的登录表单结构必须一致，避免修一个漏一个。"""
    for page in PAGES:
        ids = html_ids(page)
        # 验证码面板必须包含输入框与提交按钮，缺一个都无法完成验证
        assert "verify-code" in ids and "verify-submit" in ids, (
            f"{page} 的验证码表单不完整"
        )


# ---------------------------------------------------------------- tabs


def test_login_tabs_exist_in_every_page():
    """两个页面都要有「账号密码 / 短信验证码」两个 tab 及其面板。"""
    for page in PAGES:
        ids = html_ids(page)
        missing = sorted(TAB_IDS - ids)
        assert not missing, f"{page} 缺少登录 tab 元素: {missing}"


def test_tabs_have_aria_attributes():
    """tab 必须有无障碍属性，否则读屏用户无法切换。

    历史上样式与语义脱节过一次，这里锁死 role/aria-selected/aria-controls。
    """
    for page in PAGES:
        html = read(page)
        assert 'role="tablist"' in html, f"{page} 缺少 tablist"
        assert 'role="tab"' in html, f"{page} 缺少 tab"
        assert 'role="tabpanel"' in html, f"{page} 缺少 tabpanel"
        assert 'aria-selected="true"' in html, f"{page} 未标记默认选中 tab"
        assert 'aria-selected="false"' in html, f"{page} 未标记未选中 tab"
        assert 'aria-controls="panel-password"' in html, f"{page} tab 未关联面板"
        assert 'aria-controls="panel-verify"' in html, f"{page} tab 未关联面板"


def test_switching_tab_never_sends_network_request():
    """切 tab 必须是纯 UI 操作。

    这是本功能最容易回归、后果最严重的一条：验证码窗口在后端是 300s 的
    pending 状态，若切 tab 时重新调用 open_verification 就会重发短信，
    触发 180s 限流，用户 3 分钟内再也输不了码。

    因此 switchLoginTab 的函数体里不允许出现任何网络调用。
    """
    js = read("setting.js")
    m = re.search(
        r"function switchLoginTab\s*\([^)]*\)\s*\{(.*?)\n    \}",
        js,
        flags=re.S,
    )
    assert m, "未找到 switchLoginTab 函数"
    body = m.group(1)
    for banned in ("$.get", "$.post", "$.ajax", "fetch(", "open_verification"):
        assert banned not in body, (
            f"switchLoginTab 内出现网络调用 {banned!r}；"
            "切 tab 必须零请求，否则会重发短信并触发限流"
        )


def test_pending_state_auto_switches_to_verify_tab():
    """登录返回 state=pending 时必须自动切到验证码 tab。"""
    js = read("setting.js")
    m = re.search(r'if \(res\.state === "pending"\) \{(.*?)\n        \}', js, flags=re.S)
    assert m, "未找到 state=pending 分支"
    body = m.group(1)
    assert "switchLoginTab" in body, "state=pending 时没有自动切到验证码 tab"


def test_verify_resend_requires_explicit_click():
    """重发短信只能是用户点按钮触发，不能由切 tab 等副作用触发。"""
    js = read("setting.js")
    assert '$("#verify-resend").on("click"' in js, "缺少重发按钮的事件绑定"
    # requestVerify（会真的发短信）只允许出现在显式动作里
    assert js.count("requestVerify(") >= 2, "requestVerify 定义/调用缺失"


def test_login_tab_overrides_theme_button_style():
    """tab 必须显式覆盖主题的 button 蓝底白字。

    实测缺陷：app.css 里 `#setting .setting-panel button` 给了蓝底白字，
    未选中的 tab 因此变成「蓝底灰字」，在真实浏览器里几乎看不清
    （jsdom 不做样式计算，纯 DOM 测试抓不到，必须检查样式表本身）。
    """
    css = read("app.css")
    # 基础 tab 规则（含 :not(.is-active) 变体）到下一个选择器为止
    m = re.search(
        r"#setting \.login-tab,\s*\n#setting \.login-tab:not\(\.is-active\)\s*\{(.*?)\}",
        css,
        flags=re.S,
    )
    assert m, "app.css 缺少 #setting .login-tab 基础样式规则"
    block = m.group(1)
    assert "background: transparent !important" in block, (
        "tab 未用 !important 覆盖主题的按钮背景色，会变成蓝底灰字"
    )
    assert "color: #666 !important" in block, "tab 未覆盖主题的按钮文字颜色"

    # 选中态同样要覆盖背景，否则主题蓝底会盖掉 transparent
    m2 = re.search(r"#setting \.login-tab\.is-active\s*\{(.*?)\}", css, flags=re.S)
    assert m2, "app.css 缺少 #setting .login-tab.is-active 样式"
    assert "background: transparent !important" in m2.group(1), (
        "选中态 tab 未覆盖主题按钮背景色"
    )


def test_submit_button_does_not_wrap():
    """「提交」按钮在窄容器里会折成两行，必须禁止换行。

    注意断言必须落在具体规则块内：整个文件里别处也有 nowrap，
    断言全文会让这条测试永远通过（突变测试已验证过这个疏漏）。
    """
    css = read("app.css")
    m = re.search(
        r"#setting \.login-panel \.option-inline,\s*\n#setting \.verify-actions \.option-inline\s*\{(.*?)\}",
        css,
        flags=re.S,
    )
    assert m, "app.css 缺少登录面板按钮的 nowrap 规则"
    block = m.group(1)
    assert "white-space: nowrap" in block, "提交按钮未禁止换行，文字会折成两行"
    assert "flex-shrink: 0" in block, "提交按钮未禁止收缩，窄容器会被压窄而折行"


def test_overdue_qr_copy_removed():
    """删了扫码入口就必须删掉指向它的文案，否则用户会去找不存在的功能。"""
    js = strip_comments(read("setting.js"))
    for banned in ("扫码登录", "先扫码", "通过扫码"):
        assert banned not in js, f"仍有过时文案 {banned!r} 指向已移除的扫码登录"
