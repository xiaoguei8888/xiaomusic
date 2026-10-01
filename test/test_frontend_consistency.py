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


def test_verify_row_keeps_input_resend_submit_on_one_line():
    """输入框、「重新发送」、「提交」必须在同一行，且顺序不能变。

    用户明确要求三者同行、重发在输入框右侧、提交在最右。
    曾经「提交」在 760px 视口会折成两行，这里同时锁死容器与按钮两端。
    """
    css = read("app.css")
    m = re.search(r"#setting \.verify-code-row\s*\{(.*?)\}", css, flags=re.S)
    assert m, "app.css 缺少 #setting .verify-code-row 规则"
    block = m.group(1)
    assert "display: flex" in block, "验证码行不是 flex 布局"
    assert "flex-wrap: nowrap" in block, "验证码行允许换行，三个元素会被拆到多行"

    for page in PAGES:
        html = read(page)
        row = re.search(
            r'<div class="verify-code-row"[^>]*>(.*?)</div>\s*</div>', html, flags=re.S
        )
        assert row, f"{page} 缺少 .verify-code-row 容器"
        body = row.group(1)
        order = [
            body.find('id="verify-code"'),
            body.find('id="verify-resend"'),
            body.find('id="verify-submit"'),
        ]
        assert all(i >= 0 for i in order), f"{page} 验证码行缺少输入框/重发/提交"
        assert order == sorted(order), (
            f"{page} 验证码行顺序应为 输入框 → 重新发送 → 提交，实际下标 {order}"
        )


def test_resend_copy_is_short_and_back_button_removed():
    """重发按钮文案改为「重新发送」，「返回账号密码」按钮按用户要求删除。"""
    for page in PAGES:
        html = read(page)
        assert "重新发送" in html, f"{page} 缺少重发按钮"
        assert "重新发送验证码" not in html, f"{page} 重发按钮文案过长，应为「重新发送」"
        assert "verify-back" not in html, f"{page} 仍有已删除的返回按钮"

    js = read("setting.js")
    assert "verify-back" not in js, "setting.js 仍绑定已删除的返回按钮"


def test_idle_verify_status_uses_small_warning_text():
    """「尚未发起验证」提示要更小、用警告色，而不是普通正文色。"""
    css = read("app.css")
    m = re.search(r"#setting \.verify-status-line\s*\{(.*?)\}", css, flags=re.S)
    assert m, "app.css 缺少 #setting .verify-status-line 规则"
    block = m.group(1)
    assert "font-size: 12px" in block, "提示文字未缩小"
    assert "color: #e65100" in block, "提示文字未使用警告色"


def test_settings_button_icons_are_white():
    """设置面板按钮内的图标必须是白色，否则蓝底上的深色图标看不清。

    实测缺陷：「完成验证（发送短信）」按钮上的 sms 图标是深绿色，
    在蓝色按钮上几乎不可见。jsdom 不做样式计算，只能校验样式表本身。
    """
    css = read("app.css")
    m = re.search(
        r"#setting \.setting-panel button \.material-icons,\s*\n"
        r"#setting \.accordion-body button \.material-icons,\s*\n"
        r"#setting \.header-buttons button \.material-icons,"
        r"(.*?)\}",
        css,
        flags=re.S,
    )
    assert m, "app.css 缺少设置面板按钮图标的统一颜色规则"
    assert "color: #fff" in m.group(1), "按钮内图标未设置成白色"
    # 必须同时覆盖两类页面：index.html 用 .setting-panel，
    # setting.html 的 accordion-body 没有 setting-panel 类。
    # 真实浏览器实测：漏掉 .accordion-body 时 setting.html 的图标是 rgb(85,85,85)。
    head = m.group(0)
    assert ".setting-panel button .material-icons" in head
    assert ".accordion-body button .material-icons" in head
    assert ".header-buttons button .material-icons" in head


def test_submit_button_does_not_wrap():
    """「提交」按钮在窄容器里会折成两行，必须禁止换行。

    注意断言必须落在具体规则块内：整个文件里别处也有 nowrap，
    断言全文会让这条测试永远通过（突变测试已验证过这个疏漏）。
    """
    css = read("app.css")
    m = re.search(
        r"#setting \.login-panel \.option-inline,\s*\n#setting \.verify-code-row \.option-inline\s*\{(.*?)\}",
        css,
        flags=re.S,
    )
    assert m, "app.css 缺少登录面板按钮的 nowrap 规则"
    block = m.group(1)
    assert "white-space: nowrap" in block, "提交按钮未禁止换行，文字会折成两行"
    assert "flex-shrink: 0" in block, "提交按钮未禁止收缩，窄容器会被压窄而折行"


def test_submit_verify_surfaces_backend_rejection():
    """提交验证码必须读后端响应体：接口被拒时返回的也是 HTTP 200。

    实测缺陷（2026-10-01）：11:22:35 的提交后端回 success=false
    （「当前没有等待中的验证」），前端不读响应体，照样显示
    「已提交，等待结果...」，随后一直轮询。
    """
    js = strip_comments(read("setting.js"))
    m = re.search(r"function submitVerify\(\)\s*\{(.*?)\n    \}", js, flags=re.S)
    assert m, "未找到 submitVerify 函数"
    body = m.group(1)
    assert "res.success" in body, "submitVerify 没检查 success，后端拒绝会被当成提交成功"
    assert "finishVerify" in body, "提交被拒后没有结束本轮等待"


def test_poll_verify_stops_on_failure_and_timeout():
    """轮询必须有终局，不能永远显示「等待验证结果...」。

    实测缺陷：一次失败的验证让浏览器每 2s 轮询 /api/login/status，
    持续 3 小时 54 分（471+ 次），页面上一直只有「等待验证结果...」。
    """
    js = strip_comments(read("setting.js"))
    m = re.search(r"function pollLoginState\(\)\s*\{(.*?)\n    \}", js, flags=re.S)
    assert m, "未找到 pollLoginState 函数"
    body = m.group(1)
    assert "verifyFailed(res)" in body, "轮询不认识后端的验证失败终态"
    assert "verifyDeadline" in body, "轮询没有超时兜底，后端卡住时前端会一直等"
    # 每个终局分支都走 finishVerify（它负责停轮询，见下一条测试）
    assert body.count("finishVerify(") >= 3, "失败/限流/超时分支没有收敛到 finishVerify"
    # 失败终态来自后端的 verify 字段（sids[sid] 会被 60s 周期的自动登录覆盖）
    assert 'res.verify.state === "failed"' in js, "没有按后端的 verify 字段判失败"


def test_finish_verify_stops_polling_before_writing_text():
    """先停轮询再写文案：否则 2s 后的轮询会把失败提示覆盖回「等待验证结果...」。"""
    js = strip_comments(read("setting.js"))
    m = re.search(r"function finishVerify\([^)]*\)\s*\{(.*?)\n    \}", js, flags=re.S)
    assert m, "未找到 finishVerify 函数"
    body = m.group(1)
    assert "stopVerifyPolling" in body, "finishVerify 没有停止轮询，提示会被覆盖"



def test_login_success_stays_on_settings_page():
    """登录成功不能整页刷新把用户踢出设置面板。

    实测缺陷：点「登录」成功后 800ms 无条件 location.reload()，
    设置面板关闭、回到首页，而且没有任何登录结果提示。
    """
    js = strip_comments(read("setting.js"))
    m = re.search(r"function startLogin\(\)\s*\{(.*?)\n    \}", js, flags=re.S)
    assert m, "未找到 startLogin 函数"
    body = m.group(1)
    assert "applyLoginSuccess" in body, "登录成功后没有就地刷新状态"
    assert "location.reload" not in body, "登录成功后仍整页刷新，用户会被踢出设置页"


def test_verify_success_stays_on_settings_page():
    """短信验证成功后同样留在设置页展示状态，而不是刷新走人。"""
    js = strip_comments(read("setting.js"))
    m = re.search(r"function pollLoginState\(\)\s*\{(.*?)\n    \}", js, flags=re.S)
    assert m, "未找到 pollLoginState 函数"
    assert "location.reload" not in m.group(1), "验证成功后仍整页刷新"


def test_login_status_states_device_availability_and_sms_need():
    """状态行必须同时说清「设备列表是否可用」和「需不需要短信验证」。"""
    js = strip_comments(read("setting.js"))
    m = re.search(r"function renderLoginStatus\(res\)\s*\{(.*?)\n    \}", js, flags=re.S)
    assert m, "未找到 renderLoginStatus 函数"
    body = m.group(1)
    assert "设备列表可用" in body, "设备可用时没有明确表达"
    assert "无需短信验证" in body, "没说清什么时候不需要短信验证"
    assert "需要短信验证" in body, "没说清什么时候需要短信验证"

def test_overdue_qr_copy_removed():
    """删了扫码入口就必须删掉指向它的文案，否则用户会去找不存在的功能。"""
    js = strip_comments(read("setting.js"))
    for banned in ("扫码登录", "先扫码", "通过扫码"):
        assert banned not in js, f"仍有过时文案 {banned!r} 指向已移除的扫码登录"
