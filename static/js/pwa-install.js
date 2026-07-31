(function () {
  "use strict";

  if (window.__tawtheeqPwaInstallerLoaded) return;
  window.__tawtheeqPwaInstallerLoaded = true;

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js").catch(function () {});
    });
  }

  var promptRoot = document.getElementById("pwaInstallPrompt");
  var installAction = document.getElementById("pwaInstallAction");
  var closeButton = document.getElementById("pwaInstallClose");
  var laterButton = document.getElementById("pwaInstallLater");
  var description = document.getElementById("pwaInstallDescription");
  var steps = document.getElementById("pwaInstallSteps");

  if (!promptRoot || !installAction || !closeButton || !laterButton || !description || !steps) {
    return;
  }

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
  var dismissedKey = "tawtheeq_pwa_install_dismissed_session_v1";
  var deferredPrompt = null;
  var instructionsVisible = false;
  var previousBodyOverflow = "";

  if (!isMobile || isStandalone) return;

  try {
    if (window.sessionStorage.getItem(dismissedKey) === "1") return;
  } catch (error) {}

  function setSteps(items) {
    steps.innerHTML = "";
    items.forEach(function (item) {
      var listItem = document.createElement("li");
      listItem.textContent = item;
      steps.appendChild(listItem);
    });
  }

  function configureFallback() {
    deferredPrompt = null;
    instructionsVisible = true;
    steps.hidden = false;

    if (isIOS && isSafari) {
      description.textContent = "أضف توثيق إلى شاشة iPhone أو iPad الرئيسية وافتحه كتطبيق مستقل.";
      setSteps([
        "اضغط زر المشاركة في Safari.",
        "اختر «إضافة إلى الشاشة الرئيسية».",
        "فعّل «فتح كتطبيق ويب» ثم اضغط «إضافة»."
      ]);
      installAction.textContent = "فهمت، أرني لاحقًا";
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

    description.textContent = "متصفحك يسمح عادةً بإضافة توثيق من قائمته إلى الشاشة الرئيسية.";
    setSteps([
      "افتح قائمة المتصفح ⋮.",
      "اختر «تثبيت التطبيق» أو «إضافة إلى الشاشة الرئيسية».",
      "وافق على الإضافة."
    ]);
    installAction.textContent = "فهمت، أرني لاحقًا";
  }

  function showPrompt() {
    if (promptRoot.hidden) previousBodyOverflow = document.body.style.overflow;
    promptRoot.hidden = false;
    promptRoot.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
    window.requestAnimationFrame(function () {
      installAction.focus();
    });
  }

  function hidePrompt(rememberForSession) {
    promptRoot.hidden = true;
    promptRoot.setAttribute("aria-hidden", "true");
    document.body.style.overflow = previousBodyOverflow;
    if (rememberForSession) {
      try {
        window.sessionStorage.setItem(dismissedKey, "1");
      } catch (error) {}
    }
  }

  function showFallbackInstructions() {
    configureFallback();
    showPrompt();
  }

  window.addEventListener("beforeinstallprompt", function (event) {
    event.preventDefault();
    deferredPrompt = event;
    instructionsVisible = false;
    steps.hidden = true;
    description.textContent = "ثبّت توثيق للوصول السريع وفتحه بواجهة مستقلة من شاشتك الرئيسية.";
    installAction.textContent = "تثبيت الآن";
    showPrompt();
  });

  installAction.addEventListener("click", function () {
    if (!deferredPrompt) {
      if (instructionsVisible) {
        hidePrompt(true);
      } else {
        showFallbackInstructions();
      }
      return;
    }

    deferredPrompt.prompt();
    deferredPrompt.userChoice.then(function (choice) {
      if (choice && choice.outcome === "accepted") {
        hidePrompt(true);
      } else {
        configureFallback();
      }
      deferredPrompt = null;
    }).catch(function () {
      configureFallback();
    });
  });

  closeButton.addEventListener("click", function () {
    hidePrompt(true);
  });

  laterButton.addEventListener("click", function () {
    hidePrompt(true);
  });

  promptRoot.addEventListener("click", function (event) {
    if (event.target === promptRoot) hidePrompt(true);
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !promptRoot.hidden) {
      hidePrompt(true);
    }
  });

  window.addEventListener("appinstalled", function () {
    hidePrompt(true);
  });

  window.setTimeout(function () {
    if (!deferredPrompt && promptRoot.hidden) {
      configureFallback();
      showPrompt();
    }
  }, 900);
}());
