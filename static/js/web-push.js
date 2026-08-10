(function () {
  "use strict";

  if (window.__tawtheeqWebPushLoaded) return;
  window.__tawtheeqWebPushLoaded = true;

  var root = document.getElementById("webPushPrompt");
  if (!root || !("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) return;

  var enableButton = document.getElementById("webPushEnable");
  var laterButton = document.getElementById("webPushLater");
  var closeButton = document.getElementById("webPushPromptClose");
  var statusNode = document.getElementById("webPushPromptStatus");
  var configUrl = root.getAttribute("data-config-url");
  var subscribeUrl = root.getAttribute("data-subscribe-url");
  var DISMISSED_UNTIL_KEY = "tawtheeq_web_push_dismissed_until_v1";
  var DISMISS_DAYS = 30;
  var SHOW_DELAY_MS = 25000;
  var config = null;

  function storedNumber(key) {
    try { return Number(window.localStorage.getItem(key) || 0); } catch (error) { return 0; }
  }

  function rememberDismissal(days) {
    try {
      window.localStorage.setItem(DISMISSED_UNTIL_KEY, String(Date.now() + days * 86400000));
    } catch (error) {}
  }

  function isDismissed() { return storedNumber(DISMISSED_UNTIL_KEY) > Date.now(); }

  function isStandalone() {
    return Boolean(
      (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) ||
      (window.matchMedia && window.matchMedia("(display-mode: fullscreen)").matches) ||
      window.navigator.standalone
    );
  }

  function csrfToken() {
    var field = document.querySelector("#__csrf input[name='csrfmiddlewaretoken']");
    return field ? field.value : "";
  }

  function hide(days) {
    root.hidden = true;
    root.setAttribute("aria-hidden", "true");
    if (days) rememberDismissal(days);
  }

  function show() {
    if (Notification.permission !== "default" || isDismissed() || !isStandalone()) return;
    var installPrompt = document.getElementById("pwaInstallPrompt");
    if (installPrompt && !installPrompt.hidden) {
      window.setTimeout(show, 15000);
      return;
    }
    root.hidden = false;
    root.setAttribute("aria-hidden", "false");
  }

  function base64UrlToUint8Array(value) {
    var padding = "=".repeat((4 - value.length % 4) % 4);
    var raw = window.atob((value + padding).replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from(raw, function (character) { return character.charCodeAt(0); });
  }

  function postSubscription(subscription) {
    return window.fetch(subscribeUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
      body: JSON.stringify({ subscription: subscription.toJSON() })
    }).then(function (response) {
      if (!response.ok) throw new Error("subscription_sync_failed");
      return response.json();
    });
  }

  function registrationReady() {
    return navigator.serviceWorker.getRegistration("/").then(function (registration) {
      if (registration) return registration;
      return navigator.serviceWorker.register("/sw.js?v=9", { scope: "/", updateViaCache: "none" });
    }).then(function () { return navigator.serviceWorker.ready; });
  }

  function subscribeCurrentDevice() {
    if (!config || !config.enabled || !config.publicKey) return Promise.reject(new Error("push_not_configured"));
    return registrationReady().then(function (registration) {
      return registration.pushManager.getSubscription().then(function (subscription) {
        if (subscription) return subscription;
        return registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: base64UrlToUint8Array(config.publicKey)
        });
      });
    }).then(function (subscription) {
      return postSubscription(subscription).then(function () { return subscription; });
    });
  }

  function enable() {
    enableButton.disabled = true;
    statusNode.textContent = "جارٍ تفعيل إشعارات هذا الجهاز…";
    Notification.requestPermission().then(function (permission) {
      if (permission !== "granted") throw new Error("permission_denied");
      return subscribeCurrentDevice();
    }).then(function () {
      statusNode.textContent = "تم التفعيل بنجاح. ستصل التنبيهات حتى عند إغلاق التطبيق.";
      try { window.localStorage.removeItem(DISMISSED_UNTIL_KEY); } catch (error) {}
      window.setTimeout(function () { hide(); }, 1800);
    }).catch(function (error) {
      if (Notification.permission === "denied" || error.message === "permission_denied") {
        statusNode.textContent = "الإذن مرفوض من الجهاز. يمكنك السماح به لاحقًا من إعدادات المتصفح أو التطبيق.";
        rememberDismissal(180);
      } else {
        statusNode.textContent = "تعذر التفعيل الآن. تحقق من الاتصال ثم حاول مرة أخرى.";
      }
    }).finally(function () { enableButton.disabled = false; });
  }

  window.TawtheeqPush = {
    enable: enable,
    sync: subscribeCurrentDevice,
    isSupported: true
  };

  enableButton.addEventListener("click", enable);
  laterButton.addEventListener("click", function () { hide(DISMISS_DAYS); });
  closeButton.addEventListener("click", function () { hide(DISMISS_DAYS); });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !root.hidden) hide(DISMISS_DAYS);
  });

  window.fetch(configUrl, { credentials: "same-origin", cache: "no-store" })
    .then(function (response) { if (!response.ok) throw new Error("config_failed"); return response.json(); })
    .then(function (value) {
      config = value;
      if (!config.enabled) return;
      if (Notification.permission === "granted") {
        subscribeCurrentDevice().catch(function () {});
      } else if (Notification.permission === "default") {
        window.setTimeout(show, SHOW_DELAY_MS);
      }
    }).catch(function () {});

  window.addEventListener("appinstalled", function () {
    if (Notification.permission === "default") window.setTimeout(show, 8000);
  });
}());
