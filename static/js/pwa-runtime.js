(function () {
  "use strict";

  var network = document.getElementById("pwaNetworkStatus");
  var update = document.getElementById("pwaUpdateReady");
  var updateButton = document.getElementById("pwaUpdateAction");
  var waitingWorker = null;

  function syncNetwork() {
    if (!network) return;
    var online = navigator.onLine;
    network.hidden = online;
    network.setAttribute("aria-hidden", online ? "true" : "false");
    document.documentElement.classList.toggle("is-offline", !online);
  }

  window.addEventListener("online", syncNetwork);
  window.addEventListener("offline", syncNetwork);
  syncNetwork();

  window.addEventListener("tawtheeq:pwa-update", function (event) {
    waitingWorker = event.detail && event.detail.worker;
    if (update) update.hidden = false;
  });

  if (updateButton) updateButton.addEventListener("click", function () {
    if (waitingWorker) waitingWorker.postMessage("SKIP_WAITING");
  });

  if ("serviceWorker" in navigator) {
    var refreshing = false;
    navigator.serviceWorker.addEventListener("controllerchange", function () {
      if (refreshing) return;
      refreshing = true;
      location.reload();
    });
  }

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") syncNetwork();
  });

  document.addEventListener("tawtheeq:draft-too-large", function () {
    var message = "حجم الصور المختارة كبير جدًا للحفظ المحلي. قلّل عددها أو اختر صورًا أصغر قبل مغادرة الصفحة.";
    if (window.rcAlert) window.rcAlert(message, { type: "warning" });
  });

  var moreButton = document.querySelector("[data-mobile-tabbar-more]");
  var hamburger = document.getElementById("hamburger");
  if (moreButton && hamburger) {
    moreButton.addEventListener("click", function () { hamburger.click(); });
    var drawer = document.getElementById("mobileDrawer");
    if (drawer && window.MutationObserver) {
      new MutationObserver(function () {
        moreButton.setAttribute("aria-expanded", drawer.classList.contains("open") ? "true" : "false");
      }).observe(drawer, { attributes: true, attributeFilter: ["class"] });
    }
  }
}());
