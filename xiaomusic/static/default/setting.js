// ============ 字体加载检测 ============
// 检测字体加载完成，避免图标文字闪烁
(function () {
  // 使用 Promise.race 实现超时保护
  const fontLoadTimeout = new Promise(resolve => {
    setTimeout(() => {
      console.warn('字体加载超时，强制显示图标');
      resolve('timeout');
    }, 3000);
  });

  const fontLoadReady = document.fonts.ready.then(() => 'loaded');

  Promise.race([fontLoadReady, fontLoadTimeout]).then((result) => {
    document.body.classList.add('fonts-loaded');
    if (result === 'loaded') {
      console.log('Material Icons 字体加载完成');
    }
  }).catch((error) => {
    console.error('字体加载检测失败:', error);
    // 出错时也显示图标，避免永久隐藏
    document.body.classList.add('fonts-loaded');
  });
})();

// 设置面板的根容器。所有选择器都必须限定在它内部，
// 避免在合并为单页后误操作播放器的元素。
const SETTING_ROOT = "#setting";

let settingInitialized = false;

function initSettingPanel() {
  if (settingInitialized) {
    return;
  }
  settingInitialized = true;

  const $root = $(SETTING_ROOT);
  if (!$root.length) {
    return;
  }

  $.get("/getversion", function (data, status) {
      console.log(data, status, data["version"]);
      $("#setting-version").text(`${data.version}`);
    });

    // 遍历设置面板内的 select，默认选中只有1个选项的
    const autoSelectOne = () => {
      $root.find("select").each(function () {
        // 如果select元素仅有一个option子元素
        if ($(this).children("option").length === 1) {
          // 选中这个option
          $(this).find("option").prop("selected", true);
        }
      });
    };

    function updateCheckbox(selector, mi_did, device_list, accountPassValid) {
      // 清除现有的内容
      $(selector).empty();

      // 将 mi_did 字符串通过逗号分割转换为数组，以便于判断默认选中项
      var selected_dids = mi_did.split(",");

      //如果device_list为空，则可能是未设置小米账号密码或者已设置密码，但是没有过小米验证，此处需要提示用户
      if (device_list.length == 0) {
        const loginTips = accountPassValid
          ? `<div class="login-tips">未发现可用的小爱设备，请确认登录的是正确的小米账号，并关闭加速代理，或先在<a href="https://www.mi.com">小米官网</a>完成人脸/滑块验证。如仍未解决，请在「账号登录」中点击「完成验证（发送短信）」。</div>`
          : `<div class="login-tips">未发现可用的小爱设备，请先在下方「账号登录」中登录小米账号。</div>`;
        $(selector).append(loginTips);
        return;
      }
      $.each(device_list, function (index, device) {
        var did = device.miotDID;
        var hardware = device.hardware;
        var name = device.name;
        // 创建复选框元素
        var checkbox = $("<input>", {
          type: "checkbox",
          id: did,
          value: `${did}`,
          class: "custom-checkbox", // 添加样式类
          // 如果mi_did中包含了该did，则默认选中
          checked: selected_dids.indexOf(did) !== -1,
        });

        // 创建标签元素
        var label = $("<label>", {
          for: did,
          class: "checkbox-label", // 添加样式类
          text: `【${hardware} ${did}】${name}`, // 设定标签内容
        });

        var item = $("<div>", { class: "checkbox-item" });
        item.append(checkbox).append(label);
        $(selector).append(item);      });
    }

    function getSelectedDids(containerSelector) {
      var selectedDids = [];

      // 仅选择给定容器中选中的复选框
      $(containerSelector + " .custom-checkbox:checked").each(function () {
        var did = this.value;
        selectedDids.push(did);
      });

      return selectedDids.join(",");
    }

    // 获取设备列表（供“获取设备列表”按钮和初始加载共用）
    function fetchDeviceList(callback) {
      $.get("/getsetting?need_device_list=true", function (data, status) {
        if (typeof callback === "function") {
          callback(data, status);
        }
      }).fail(function (xhr) {
        alert(
          "获取设备列表失败: " +
            (xhr.responseJSON && xhr.responseJSON.detail
              ? xhr.responseJSON.detail
              : xhr.statusText)
        );
      });
    }

    // 初始加载：拉取配置并填充表单与设备列表
    fetchDeviceList(function (data, status) {
      console.log(data, status);
      var accountPassValid = data.account && data.password;
      updateCheckbox("#mi_did", data.mi_did || "", data.device_list || [], accountPassValid);

      for (const key in data) {
        const $element = $root.find("#" + key);
        if ($element.length) {
          if (data[key] === true) {
            $element.val("true");
          } else if (data[key] === false) {
            $element.val("false");
          } else {
            $element.val(data[key]);
          }
        }
      }

      autoSelectOne();
      if (typeof toggleMultiResultAction === "function") {
        toggleMultiResultAction();
      }
      if (typeof toggleCoverSelect === "function") {
        toggleCoverSelect();
      }
      var $audioIdSel = $("#use_music_audio_id");
      if (!$audioIdSel.val()) {
        $audioIdSel.prop("selectedIndex", 0);
      }
      $audioIdSel.trigger("change");

      loadLoginStatus();
    });

      $("#update-devices").on("click", function () {
      var $btn = $(this);
      var oldText = $btn.text();
      $btn.prop("disabled", true).text("更新中…");
      $.get("/device_list")
        .done(function (data) {
          var currentMiDid = getSelectedDids("#mi_did");
          var raw = data.devices || {};
          var deviceList = Object.keys(raw).map(function (did) {
            var d = raw[did];
            return { miotDID: d.did || did, hardware: d.hardware || "", name: d.name || "" };
          });
          updateCheckbox("#mi_did", currentMiDid, deviceList);
        })
        .fail(function (xhr) {
          alert("更新设备列表失败: " + (xhr.responseJSON && xhr.responseJSON.detail ? xhr.responseJSON.detail : xhr.statusText));
        })
        .always(function () {
          $btn.prop("disabled", false).text(oldText);
        });
    });


    $(".save-button").on("click", () => {
      var setting = $("#setting");
      var inputs = setting.find("input, select, textarea");
      var data = {};
      inputs.each(function () {
        var id = this.id;
        if (id) {
          data[id] = $(this).val();
        }
      });
      var did_list = getSelectedDids("#mi_did");
      data["mi_did"] = did_list;
      console.log(data);

      $.ajax({
        type: "POST",
        url: "/savesetting",
        contentType: "application/json",
        data: JSON.stringify(data),
        success: (msg) => {
          alert(msg);
          location.reload();
        },
        error: (msg) => {
          alert(msg);
        },
      });
    });

    $("#refresh_music_tag").on("click", () => {
      $.ajax({
        type: "POST",
        url: "/refreshmusictag",
        contentType: "application/json",
        success: (res) => {
          console.log(res);
          alert(res.ret);
        },
        error: (res) => {
          console.log(res);
          alert(res);
        },
      });
    });

    $("#clear_cache").on("click", () => {
      Object.keys(localStorage)
        .filter((key) => key.startsWith("accordion:"))
        .forEach((key) => localStorage.removeItem(key));
      alert("清除成功");
    });

    $("#hostname").on("change", function () {
      const hostname = $(this).val();
      // 检查是否包含端口号（1到5位数字）
      if (hostname.match(/:\d{1,5}$/)) {
        alert("hostname禁止带端口号");
        // 移除端口号
        $(this).val(hostname.replace(/:\d{1,5}$/, ""));
      }
    });

    $("#auto-hostname").on("click", () => {
      const protocol = window.location.protocol;
      const hostname = window.location.hostname;
      if (hostname == "127.0.0.1" || hostname == "localhost") {
        alert("hostname 不能是 127.0.0.1 或者 localhost");
        return;
      }
      const baseUrl = `${protocol}//${hostname}`;
      console.log(baseUrl);
      $("#hostname").val(baseUrl);
    });

    $("#auto-port").on("click", () => {
      let port = window.location.port;
      if (port == 0) {
        const protocol = window.location.protocol;
        if (protocol == "https:") {
          port = 443;
        } else {
          port = 80;
        }
      }
      console.log(port);
      $("#public_port").val(port);
    });

    (function initAccordion() {
      const $root = $(SETTING_ROOT);
      const $items = $root.find(".accordion-item");
      if (!$items.length) return;

      const STORAGE_PREFIX = "accordion:";

      function storageKey(id) {
        return STORAGE_PREFIX + id;
      }

      function setExpanded($item, expanded, persist) {
        const $header = $item.find(".accordion-header");
        const $content = $item.find(".accordion-content");
        const id = $item.data("section") || $header.attr("id");

        $header.attr("aria-expanded", expanded ? "true" : "false");
        $item.toggleClass("expanded", expanded);
        $content.attr("aria-hidden", expanded ? "false" : "true");

        if (persist && id) {
          localStorage.setItem(storageKey(id), expanded ? "true" : "false");
        }
      }

      $items.each(function () {
        const $item = $(this);
        const id = $item.data("section");
        const stored = id ? localStorage.getItem(storageKey(id)) : null;
        setExpanded($item, stored === "true", false);
      });

      $root.on("click", ".accordion-header", function (e) {
        e.preventDefault();
        const $item = $(this).closest(".accordion-item");
        const expanded = $(this).attr("aria-expanded") === "true";
        setExpanded($item, !expanded, true);
      });

      $root.on("keydown", ".accordion-header", function (e) {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          $(this).trigger("click");
        }
      });
    })();

    window.toggleMultiResultAction = function () {
      var val = $("#enable_multi_result_selection").val();
      if (val === "true") {
        $("#multi_result_action_row").show();
      } else {
        $("#multi_result_action_row").hide();
      }
    };

    // 联动启用封面和封面选择
    window.toggleCoverSelect = function () {
      var isContinuePlay = $("#continue_play").val() === "true";
      var $coverSelect = $("#use_music_audio_id");
      var $coverLabel = $('label[for="use_music_audio_id"]');
      var $coverPreview = $("#cover_preview");
      if (isContinuePlay) {
        $coverSelect.prop("disabled", false);
        $coverLabel.css("color", "");
        $coverSelect.css("color", "");

        // 恢复正常状态
        $coverPreview.css("opacity", "1");
        $coverSelect.trigger("change");
      } else {
        $coverSelect.prop("disabled", true);
        $coverLabel.css("color", "#999");
        $coverSelect.css("color", "transparent");

        // 变成默认占位图
        $coverPreview.attr("src", "https://y.gtimg.cn/music/photo_new/T001R500x500M000000s13WK28L1rN.jpg");
        $coverPreview.css("display", "block");
        $coverPreview.css("opacity", "0.4");
      }
    };

    $("#continue_play").on("change", window.toggleCoverSelect);

    // 封面下拉框的图片切换逻辑
    $("#use_music_audio_id").on("change", function() {
      // 只有在 continue_play 为 true（开启状态）时，才允许切换图片
      if ($("#continue_play").val() === "true") {
        var imgSrc = $(this).find("option:selected").attr("data-img");
        var $preview = $("#cover_preview");
        if (imgSrc) {
          $preview.attr("src", imgSrc).css("display", "block");
        } else {
          $preview.css("display", "none");
        }
      }
    });

    let verifyTimer = null;
    let verifyCountdownTimer = null;
    let verifySid = "micoapi";
    // 提交验证码后最多等多久（ms）。后端不会永远停在 pending（要么 ok，
    // 要么 verify.state=failed），这里是「后端卡住时前端陪着无限等」的兜底。
    const VERIFY_RESULT_TIMEOUT_MS = 60000;
    let verifyDeadline = 0;

    // 兜底：把不存在的 DOM 当成空集合处理，避免一个缺失元素让整段脚本中断。
    // 历史上这里引用过 /api/login/qrcode（后端已删除，返回 404），属于死代码。
    function showLoginError(msg) {
      switchLoginTab("verify");
      finishVerify("⚠️ " + msg);
    }

    function stopVerifyPolling() {
      if (verifyTimer) {
        clearInterval(verifyTimer);
        verifyTimer = null;
      }
    }

    /* 本轮验证结束：先停轮询，再写最终文案。
     * 顺序不能反 —— pollLoginState 每 2s 会把 #verify-status 无条件改回
     * 「等待验证结果...」，任何失败/成功提示都会被它覆盖掉。 */
    function finishVerify(msg) {
      stopVerifyPolling();
      stopVerifyCountdown();
      if (msg) { $("#verify-status").text(msg); }
    }

    /* ---------- 登录方式切换（账号密码 / 短信验证码） ----------
     * 纯 UI 操作：绝不发网络请求。验证码窗口在后端是 300s 的 pending 状态，
     * 用户来回切 tab 不应该重发短信 —— 重发会触发 180s 限流，让用户 3 分钟输不了码。 */
    function switchLoginTab(which) {
      const want = which === "verify" ? "verify" : "password";
      $("#tab-password").toggleClass("is-active", want === "password")
        .attr("aria-selected", want === "password" ? "true" : "false")
        .attr("tabindex", want === "password" ? "0" : "-1");
      $("#tab-verify").toggleClass("is-active", want === "verify")
        .attr("aria-selected", want === "verify" ? "true" : "false")
        .attr("tabindex", want === "verify" ? "0" : "-1");
      $("#panel-password").toggle(want === "password");
      $("#panel-verify").toggle(want === "verify");
      if (want === "verify") {
        clearVerifyBadge();
        $("#verify-code").trigger("focus");
      }
    }

    function showVerifyBadge() {
      $("#tab-verify-badge").show();
    }

    function clearVerifyBadge() {
      $("#tab-verify-badge").hide();
    }

    /* 验证码倒计时：让「窗口还在」这件事可见，而不是让用户猜。 */
    function startVerifyCountdown(seconds) {
      stopVerifyCountdown();
      let left = seconds;
      const tick = function () {
        if (left <= 0) {
          stopVerifyCountdown();
          $("#verify-countdown").remove();
          return;
        }
        const m = Math.floor(left / 60);
        const s = String(left % 60).padStart(2, "0");
        $("#verify-countdown").remove();
        $("#verify-status").after(
          '<div class="verify-countdown" id="verify-countdown">验证码 ' + m + ":" + s + " 后失效</div>"
        );
        left -= 1;
      };
      tick();
      verifyCountdownTimer = setInterval(tick, 1000);
    }

    function stopVerifyCountdown() {
      if (verifyCountdownTimer) {
        clearInterval(verifyCountdownTimer);
        verifyCountdownTimer = null;
      }
      $("#verify-countdown").remove();
    }

    function showVerifyBox(method, waitSeconds) {
      $("#verify-label").text((method === "Email" ? "邮箱" : "短信") + "验证码:");
      const $code = $("#verify-code");
      // 不要无条件清空：用户可能切走 tab 又切回来，清空等于让他重新输。
      if (!$code.val()) {
        $code.trigger("focus");
      }
      $("#verify-status").text("验证码已发送，请查收并输入");
      startVerifyCountdown(waitSeconds || 300);
    }

    // 本轮验证的目标 sid 完成了吗？
    function sidDone(res, sid) {
      return !!(res && res.sids && res.sids[sid] === "ok");
    }

    // 后端是否已判定本轮验证失败（验证码错误/过期/被拒）？
    // 必须连 sid 一起比：verify 是「最后一次尝试」的全局结果，可能属于另一个 sid。
    function verifyFailed(res) {
      return !!(res && res.verify && res.verify.state === "failed" &&
        res.verify.sid === verifySid);
    }

    function pollLoginState() {
      $.get("/api/login/status", function (res) {
        if (!res) return;
        if (sidDone(res, verifySid)) {
          clearVerifyBadge();
          finishVerify("✅ 验证成功，正在获取设备列表...");
          applyLoginSuccess("✅ 验证成功，正在获取设备列表...");
        } else if (res.state === "cooldown") {
          finishVerify("请求过于频繁，请稍后再试");
        } else if (verifyFailed(res)) {
          // 后端已有终局结论，继续轮询只会一直显示「等待验证结果...」。
          finishVerify("⚠️ " + (res.verify.message || "验证失败，请重新发送验证码"));
        } else if (verifyDeadline && Date.now() > verifyDeadline) {
          finishVerify("⚠️ 等待验证结果超时，请重新发送验证码");
        } else {
          $("#verify-status").text("等待验证结果...");
        }
      });
    }

    function beginVerify(sid, method, waitSeconds) {
      verifySid = sid || "xiaomiio";
      verifyDeadline = 0; // 新一轮：清掉上一轮的超时判定
      showVerifyBox(method, waitSeconds);
      // 需要二次验证时自动切到验证码 tab，用户不用自己找入口。
      switchLoginTab("verify");
      stopVerifyPolling();
      verifyTimer = setInterval(pollLoginState, 2000);
    }

    function startLogin() {
      let $btn = $("#login-submit").prop("disabled", true);
      clearVerifyBadge();
      stopVerifyCountdown();
      // 新的登录尝试意味着上一轮验证提示已过期，避免用户看到自相矛盾的状态
      $("#verify-status").text("尚未发起验证");
      $("#login-status").text("登录中...");
      $.ajax({
        url: "/api/login/start",
        method: "POST",
        contentType: "application/json",
        data: JSON.stringify({
          account: $("#login-account").val().trim(),
          password: $("#login-password").val(),
        }),
      }).done(function (res) {
        $btn.prop("disabled", false);
        if (!res) { $("#login-status").text("登录失败：无响应"); return; }

        if (res.state === "cooldown") {
          $("#login-status").text("请求过于频繁，请稍后再试");
          return;
        }

        // 后端在需要二次验证时返回 state=pending，并带上待验证的 sid
        // （可能是 micoapi，也可能是 xiaomiio —— 后者决定设备列表能否拉到）。
        if (res.state === "pending") {
          $("#login-status").text("账号密码正确，小米要求二次验证 → 需要短信验证码");
          showVerifyBadge();
          switchLoginTab("verify");
          beginVerify(res.sid, res.method, res.waitSeconds);
          return;
        }

        // micoapi 拿到就说明账号密码可用；xiaomiio 是否可用由 renderLoginStatus
        // 如实呈现（需要短信验证时会给出入口）。
        // 注意：这里**不能**再 location.reload() —— 那会把用户踢出设置面板、
        // 回到首页，而且看不到任何登录结果。
        if (sidDone(res, "micoapi")) {
          applyLoginSuccess();
          return;
        }

        $("#login-status").text("登录失败，请检查账号密码");
      }).fail(function (xhr) {
        $btn.prop("disabled", false);
        $("#login-status").text("请求失败: " + xhr.statusText);
      });
    }

    // 用户在没有表单提交的情况下，直接为未验证的 sid 补发一次验证
    function requestVerify(sid) {
      $.ajax({
        url: "/api/login/verify/open",
        method: "POST",
        contentType: "application/json",
        data: JSON.stringify({ sid: sid }),
      }).done(function (res) {
        if (!res || !res.success) {
          showLoginError((res && res.message) || "验证发起失败");
          return;
        }
        beginVerify(res.sid || sid, res.method, res.waitSeconds);
      }).fail(function (xhr) {
        showLoginError("请求失败: " + xhr.statusText);
      });
    }

    function submitVerify() {
      let code = $("#verify-code").val().trim();
      if (!code) { $("#verify-status").text("请输入验证码"); return; }
      let $btn = $("#verify-submit").prop("disabled", true);
      $.ajax({
        url: "/api/login/verify/submit",
        method: "POST",
        contentType: "application/json",
        data: JSON.stringify({ sid: verifySid, code: code }),
      }).done(function (res) {
        $btn.prop("disabled", false);
        // 后端拒绝时返回的也是 HTTP 200（success:false）。不读响应体就会把
        // 「当前没有等待中的验证」当成提交成功，然后一直傻等（实测 3h54m）。
        if (!res || !res.success) {
          finishVerify("⚠️ " + ((res && res.message) || "提交失败，请重新发送验证码"));
          return;
        }
        verifyDeadline = Date.now() + VERIFY_RESULT_TIMEOUT_MS;
        $("#verify-status").text("已提交，等待结果...");
      }).fail(function (xhr) {
        $btn.prop("disabled", false);
        finishVerify("提交失败: " + xhr.statusText);
      });
    }

    $("#login-submit").on("click", startLogin);
    $("#verify-submit").on("click", submitVerify);
    $("#verify-code").on("keydown", function (e) {
      if (e.key === "Enter") submitVerify();
    });

    // tab 切换：纯 UI，零网络请求（防止误触发重发短信 → 180s 限流）
    $("#tab-password").on("click", function () { switchLoginTab("password"); });
    $("#tab-verify").on("click", function () { switchLoginTab("verify"); });

    // 重发是显式的用户动作：只有点这个按钮才会重新请求短信。
    $("#verify-resend").on("click", function () {
      requestVerify(verifySid);
    });

    // 登录状态区渲染：文案必须与 /api/login/status 的语义严格一致
    function renderLoginStatus(res) {
      const $status = $("#login-status");
      const micoapiOk = !!(res.sids && res.sids.micoapi === "ok");
      const xiaomiioOk = !!(res.sids && res.sids.xiaomiio === "ok");

      if (micoapiOk && xiaomiioOk) {
        // 两个 sid 都正常，设备列表才是真的可用
        $status.text(
          "✅ 账号可用，设备列表可用（" + (res.device_count || 0) +
          " 个设备）｜无需短信验证"
        );
        $("#login-verify-hint").hide();
        return;
      }

      if (micoapiOk) {
        // 账号可登录，但 xiaomiio 未验证 → 设备列表一定拉不到。
        // 这里不能写「已登录（N 个设备）」，否则与「没找到小爱音箱」自相矛盾。
        // 关键是给用户一个能点的入口，而不是只告诉他「需完成验证」。
        $status.text(
          "⚠️ 账号已登录，但设备列表不可用｜需要短信验证（设备列表由 xiaomiio 提供）"
        );
        $("#login-verify-hint")
          .show()
          .off("click")
          .on("click", function () {
            requestVerify("xiaomiio");
          });
        return;
      }

      $status.text(
        "⚠️ 未登录｜输入账号密码登录；只有小米要求二次验证时才需要短信验证码"
      );
      $("#login-verify-hint").hide();
    }

    function loadLoginStatus() {
      $.get("/api/login/status", function (res) {
        if (!res) return;
        renderLoginStatus(res);
      }).fail(function () {
        $("#login-status").text("登录状态获取失败");
      });
    }

    /* 登录/验证成功后就地生效，不再整页刷新。
     * /api/login/verify/check 会在服务端绑定服务、拉一次设备列表；
     * 再用 reinitForDevice() 重建首页的设备下拉（md.js 的全局函数，
     * 独立设置页 setting.html 没有它），最后重渲染登录状态。 */
    function applyLoginSuccess(message) {
      $("#login-status").text(message || "✅ 登录成功，正在获取设备列表...");
      $.ajax({ url: "/api/login/verify/check", method: "POST" })
        .always(function () {
          if (typeof reinitForDevice === "function") {
            reinitForDevice();
          }
          loadLoginStatus();
        });
    }

    $("#use_music_audio_id").trigger("change");

}

window.initSettingPanel = initSettingPanel;