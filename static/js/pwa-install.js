(function () {
  "use strict";

  if (window.__tawtheeqPwaInstallerLoaded) return;
  window.__tawtheeqPwaInstallerLoaded = true;

  var SW_URL = "/sw.js?v=9";
  var DISMISSED_UNTIL_KEY = "tawtheeq_pwa_install_dismissed_until_v2";
  var LAST_AUTO_SHOWN_KEY = "tawtheeq_pwa_install_last_auto_shown_v1";
  var DISMISS_DAYS = 90;
  var AUTO_RESURFACE_DAYS = 7;
  var AUTO_NATIVE_DELAY_MS = 15000;
  var AUTO_IOS_DELAY_MS = 20000;
  var AUTO_FALLBACK_DELAY_MS = 22000;

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register(SW_URL, {
        scope: "/",
        updateViaCache: "none"
      }).then(function (registration) {
        registration.update().catch(function () {});
      }).catch(function () {});
    });
  }

  var promptRoot = document.getElementById("pwaInstallPrompt");
  var installAction = document.getElementById("pwaInstallAction");
  var closeButton = document.getElementById("pwaInstallClose");
  var laterButton = document.getElementById("pwaInstallLater");
  var description = document.getElementById("pwaInstallDescription");
  var steps = document.getElementById("pwaInstallSteps");
  var autoPromptAllowed = Boolean(
    promptRoot && promptRoot.getAttribute("data-auto-prompt") === "true"
  );

  var userAgent = navigator.userAgent || "";
  var isIPadOS = /macintosh/i.test(userAgent) && navigator.maxTouchPoints > 1;
  var isIOS = /iphone|ipad|ipod/i.test(userAgent) || isIPadOS;
  var isAndroid = /android/i.test(userAgent);
  var isSafari = isIOS && /safari/i.test(userAgent) && !/crios|fxios|edgios|opios/i.test(userAgent);
  var hasCoarsePointer = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
  var hasMobileWidth = window.matchMedia && window.matchMedia("(max-width: 1024px)").matches;
  var isMobile = isIOS || isAndroid || (hasCoarsePointer && hasMobileWidth);
  var isStandalone =
    (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) ||
    (window.matchMedia && window.matchMedia("(display-mode: fullscreen)").matches) ||
    Boolean(window.navigator.standalone);
  var deferredPrompt = null;
  var instructionsVisible = false;

  function getStoredNumber(key) {
    try {
      return Number(window.localStorage.getItem(key) || 0);
    } catch (error) {
      return 0;
    }
  }

  function getDismissedUntil() {
    return getStoredNumber(DISMISSED_UNTIL_KEY);
  }

  function isDismissed() {
    return getDismissedUntil() > Date.now();
  }

  function wasAutoPromptShownRecently() {
    var lastShown = getStoredNumber(LAST_AUTO_SHOWN_KEY);
    return lastShown > 0 && lastShown + (AUTO_RESURFACE_DAYS * 24 * 60 * 60 * 1000) > Date.now();
  }

  function rememberAutoPromptShown() {
    try {
      window.localStorage.setItem(LAST_AUTO_SHOWN_KEY, String(Date.now()));
    } catch (error) {}
  }

  function rememberDismissal(days) {
    try {
      window.localStorage.setItem(
        DISMISSED_UNTIL_KEY,
        String(Date.now() + (days * 24 * 60 * 60 * 1000))
      );
    } catch (error) {}
  }

  if (!promptRoot || !installAction || !closeButton || !laterButton || !description || !steps) {
    window.TawtheeqPWA = { isStandalone: isStandalone, canInstall: false };
    return;
  }

  function setSteps(items) {
    steps.innerHTML = "";
    items.forEach(function (item) {
      var listItem = document.createElement("li");
      listItem.textContent = item;
      steps.appendChild(listItem);
    });
  }

  function configureNativePrompt() {
    instructionsVisible = false;
    steps.hidden = true;
    description.textContent = "ثبّت منصة توثيق للوصول السريع وفتحها بواجهة مستقلة من شاشتك الرئيسية.";
    installAction.textContent = "تثبيت الآن";
  }

  function configureFallback() {
    deferredPrompt = null;
    instructionsVisible = true;
    steps.hidden = false;

    if (isIOS && isSafari) {
      description.textContent = "أضف منصة توثيق إلى شاشة iPhone أو iPad الرئيسية وافتحها كتطبيق مستقل.";
      setSteps([
        "اضغط زر المشاركة في Safari.",
        "اختر «إضافة إلى الشاشة الرئيسية».",
        "فعّل «فتح كتطبيق ويب» ثم اضغط «إضافة»."
      ]);
      installAction.textContent = "حسنًا";
      return;
    }

    if (isIOS) {
      description.textContent = "للتثبيت على iPhone أو iPad افتح هذه الصفحة في Safari أولًا.";
      setSteps([
        "انسخ رابط الصفحة وافتحه في Safari.",
        "اضغط مشاركة ثم «إضافة إلى الشاشة الرئيسية».",
        "فعّل «فتح كتطبيق ويب» ثم اضغط «إضافة»."
      ]);
      installAction.textContent = "حسنًا";
      return;
    }

    description.textContent = "يمكنك إضافة منصة توثيق من قائمة المتصفح إلى الشاشة الرئيسية.";
    setSteps([
      "افتح قائمة المتصفح ⋮.",
      "اختر «تثبيت التطبيق» أو «إضافة إلى الشاشة الرئيسية».",
      "وافق على الإضافة."
    ]);
    installAction.textContent = "حسنًا";
  }

  function showPrompt(options) {
    options = options || {};
    if (
      isStandalone ||
      (!options.explicit && (
        !autoPromptAllowed || !isMobile || isDismissed() || wasAutoPromptShownRecently()
      ))
    ) return false;
    promptRoot.hidden = false;
    promptRoot.setAttribute("aria-hidden", "false");
    if (!options.explicit) rememberAutoPromptShown();
    return true;
  }

  function hidePrompt(days) {
    promptRoot.hidden = true;
    promptRoot.setAttribute("aria-hidden", "true");
    if (days) rememberDismissal(days);
  }

  function showInstallPrompt(explicit) {
    if (isStandalone) return false;
    if (deferredPrompt) configureNativePrompt();
    else configureFallback();
    return showPrompt({ explicit: Boolean(explicit) });
  }

  window.TawtheeqPWA = {
    isStandalone: isStandalone,
    canInstall: !isStandalone && isMobile,
    showInstallPrompt: function () { return showInstallPrompt(true); }
  };

  if (!isStandalone) {
    window.addEventListener("beforeinstallprompt", function (event) {
      event.preventDefault();
      deferredPrompt = event;
      configureNativePrompt();
      if (autoPromptAllowed) {
        window.setTimeout(function () {
          if (deferredPrompt && document.visibilityState === "visible") showPrompt();
        }, AUTO_NATIVE_DELAY_MS);
      }
    });
  }

  installAction.addEventListener("click", function () {
    if (!deferredPrompt) {
      hidePrompt(DISMISS_DAYS);
      return;
    }

    var installEvent = deferredPrompt;
    deferredPrompt = null;
    installEvent.prompt();
    installEvent.userChoice.then(function (choice) {
      if (choice && choice.outcome === "accepted") {
        hidePrompt(365);
      } else {
        hidePrompt(DISMISS_DAYS);
      }
    }).catch(function () {
      configureFallback();
      showPrompt({ explicit: true });
    });
  });

  closeButton.addEventListener("click", function () { hidePrompt(DISMISS_DAYS); });
  laterButton.addEventListener("click", function () { hidePrompt(DISMISS_DAYS); });

  document.addEventListener("keydown", function (event) {
    if (promptRoot.hidden) return;
    if (event.key === "Escape") {
      hidePrompt(DISMISS_DAYS);
    }
  });

  window.addEventListener("appinstalled", function () {
    deferredPrompt = null;
    hidePrompt(365);
  });

  if (autoPromptAllowed && isIOS && isMobile && !isStandalone && !isDismissed()) {
    window.setTimeout(function () {
      if (promptRoot.hidden && document.visibilityState === "visible") {
        configureFallback();
        showPrompt();
      }
    }, AUTO_IOS_DELAY_MS);
  }

  // بعض متصفحات Android لا تطلق beforeinstallprompt رغم إمكانية الإضافة من
  // القائمة. نعطي الحدث الأصلي الأولوية، ثم نعرض تعليمات يدوية إن لم يصل.
  if (autoPromptAllowed && isMobile && !isIOS && !isStandalone && !isDismissed()) {
    window.setTimeout(function () {
      if (promptRoot.hidden && document.visibilityState === "visible") {
        if (deferredPrompt) configureNativePrompt();
        else configureFallback();
        showPrompt();
      }
    }, AUTO_FALLBACK_DELAY_MS);
  }
}());
