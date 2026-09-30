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
          ? `<div class="login-tips">未发现可用的小爱设备，请确认已通过扫码登录正确的账号，并关闭加速代理或在<a href="https://www.mi.com">小米官网</a>登陆过人脸或滑块验证。如仍未解决。请根据<a href="https://github.com/hanxi/xiaomusic/issues/99">FAQ</a>的内容解决问题。</div>`
          : `<div class="login-tips">未发现可用的小爱设备，请先在下方扫码登录小米账号</div>`;
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

    $("#cleantempdir").on("click", () => {
      $.ajax({
        type: "POST",
        url: "/api/file/cleantempdir",
        contentType: "application/json",
        data: JSON.stringify({}),
        success: (msg) => {
          alert(msg.ret);
        },
        error: (msg) => {
          alert(msg);
        },
      });
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

    let qrcodeTimer = null;

  function stopQrcodePolling() {
    if (qrcodeTimer) {
      clearInterval(qrcodeTimer);
      qrcodeTimer = null;
    }
  }

    function pollQrcodeStatus() {
      $.get("/api/login/qrcode/status", function (res) {
        if (!res) return;
        if (res.state === "success") {
          stopQrcodePolling();
          $("#qrcode-status").text("登录成功，正在刷新页面...");
          setTimeout(function () { location.reload(); }, 800);
        } else if (res.state === "expired" || res.state === "error") {
          stopQrcodePolling();
          $("#qrcode-status").text("失败: " + (res.message || res.state));
        } else {
          $("#qrcode-status").text("等待扫码...");
        }
      });
    }

    function loadQrcode() {
      let $btn = $("#show-qrcode").prop("disabled", true);
      stopQrcodePolling();
      $("#qrcode-box").show();
      $("#qrcode-img").attr("src", "");
      $("#qrcode-status").text("正在获取二维码...");
      $.get("/api/login/qrcode", function (res) {
        $btn.prop("disabled", false);
        if (!res || !res.success) {
          $("#qrcode-status").text("获取失败: " + ((res && res.message) || "未知错误"));
          return;
        }
        $("#qrcode-img").attr("src", res.qrcode);
        $("#qrcode-status").text("等待扫码...");
        qrcodeTimer = setInterval(pollQrcodeStatus, 2000);
      }).fail(function (xhr) {
        $btn.prop("disabled", false);
        $("#qrcode-status").text("请求失败: " + xhr.statusText);
      });
    }

    $("#show-qrcode").on("click", loadQrcode);

    let verifyTimer = null;
    let verifySid = "micoapi";

    function stopVerifyPolling() {
      if (verifyTimer) {
        clearInterval(verifyTimer);
        verifyTimer = null;
      }
    }

    function showVerifyBox(method) {
      $("#verify-box").show();
      $("#verify-label").text((method === "Email" ? "邮箱" : "短信") + "验证码:");
      $("#verify-code").val("").focus();
      $("#verify-status").text("验证码已发送，请查收并输入");
    }

    function pollLoginState() {
      $.get("/api/login/status", function (res) {
        if (!res) return;
        if (res.sids && res.sids.micoapi === "ok") {
          stopVerifyPolling();
          $("#verify-status").text("登录成功，正在刷新页面...");
          setTimeout(function () { location.reload(); }, 800);
        } else if (res.state === "cooldown") {
          $("#verify-status").text("请求过于频繁，请稍后再试");
        } else {
          $("#verify-status").text("等待验证结果...");
        }
      });
    }

    function startLogin() {
      let $btn = $("#login-submit").prop("disabled", true);
      $("#verify-box").hide();
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
        if (res.sids && res.sids.micoapi === "ok") {
          $("#login-status").text("登录成功，正在刷新页面...");
          setTimeout(function () { location.reload(); }, 800);
          return;
        }
        if (res.state === "pending") {
          verifySid = res.sid || "micoapi";
          $("#login-status").text("需要验证");
          showVerifyBox(res.method);
          stopVerifyPolling();
          verifyTimer = setInterval(pollLoginState, 2000);
        } else {
          $("#login-status").text("登录失败，请检查账号密码");
        }
      }).fail(function (xhr) {
        $btn.prop("disabled", false);
        $("#login-status").text("请求失败: " + xhr.statusText);
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
      }).done(function () {
        $btn.prop("disabled", false);
        $("#verify-status").text("已提交，等待结果...");
      }).fail(function (xhr) {
        $btn.prop("disabled", false);
        $("#verify-status").text("提交失败: " + xhr.statusText);
      });
    }

    $("#login-submit").on("click", startLogin);
    $("#verify-submit").on("click", submitVerify);
    $("#verify-code").on("keydown", function (e) {
      if (e.key === "Enter") submitVerify();
    });

    function loadLoginStatus() {
      $.get("/api/login/status", function (res) {
        if (!res) return;
        if (res.sids && res.sids.micoapi === "ok") {
          let extra = res.sids.xiaomiio === "ok" ? "" : "（设备列表待验证）";
          $("#login-status").text("✅ 已登录" + extra + "（" + (res.device_count || 0) + " 个设备）");
        } else {
          $("#login-status").text("⚠️ 未登录，请输入账号密码登录");
        }
      }).fail(function () {
        $("#login-status").text("登录状态获取失败");
      });
    }

    $("#use_music_audio_id").trigger("change");

}

window.initSettingPanel = initSettingPanel;