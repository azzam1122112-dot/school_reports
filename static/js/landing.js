(function () {
  "use strict";

  var menu = document.getElementById("mobileMenu");
  var menuToggle = document.getElementById("menuToggle");
  var menuClose = document.getElementById("menuClose");

  function setMenu(open) {
    if (!menu || !menuToggle) return;
    menu.hidden = !open;
    menuToggle.setAttribute("aria-expanded", open ? "true" : "false");
    document.body.classList.toggle("menu-open", open);
    if (open && menuClose) menuClose.focus();
  }

  if (menuToggle) {
    menuToggle.addEventListener("click", function () {
      setMenu(true);
    });
  }
  if (menuClose) {
    menuClose.addEventListener("click", function () {
      setMenu(false);
      if (menuToggle) menuToggle.focus();
    });
  }
  if (menu) {
    menu.addEventListener("click", function (event) {
      if (
        event.target === menu
        || (event.target.closest && event.target.closest("a"))
      ) {
        setMenu(false);
      }
    });
  }
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && menu && !menu.hidden) setMenu(false);
  });

  var periodButtons = document.querySelectorAll(".period-switch [data-period]");
  Array.prototype.forEach.call(periodButtons, function (button) {
    button.addEventListener("click", function () {
      var period = button.getAttribute("data-period");

      Array.prototype.forEach.call(periodButtons, function (item) {
        var active = item.getAttribute("data-period") === period;
        item.classList.toggle("active", active);
        item.classList.toggle("is-active", active);
        item.setAttribute("aria-pressed", active ? "true" : "false");
      });

      var flexiblePricing = document.querySelector("[data-flex-pricing]");
      if (flexiblePricing && flexiblePricing.__flexiblePricing) {
        flexiblePricing.__flexiblePricing.setPeriod(period);
      }

      Array.prototype.forEach.call(
        document.querySelectorAll("[data-price-card]"),
        function (card) {
          var requested = card.querySelector(
            '[data-period-content="' + period + '"]'
          );
          var fallback = card.querySelector("[data-period-content]");
          Array.prototype.forEach.call(
            card.querySelectorAll("[data-period-content]"),
            function (panel) {
              panel.classList.toggle(
                "active",
                panel === (requested || fallback)
              );
            }
          );
        }
      );
    });
  });

  Array.prototype.forEach.call(
    document.querySelectorAll("details"),
    function (detail) {
      detail.addEventListener("toggle", function () {
        var icon = detail.querySelector("summary i");
        if (icon) {
          icon.classList.toggle("fa-plus", !detail.open);
          icon.classList.toggle("fa-minus", detail.open);
        }
      });
    }
  );

  var roleTabs = Array.prototype.slice.call(
    document.querySelectorAll("[data-role-target]")
  );
  var rolePanels = Array.prototype.slice.call(
    document.querySelectorAll("[data-role-panel]")
  );

  function activateRole(tab, moveFocus) {
    var targetId = tab && tab.getAttribute("data-role-target");
    if (!targetId) return;

    Array.prototype.forEach.call(roleTabs, function (item) {
      var active = item === tab;
      item.classList.toggle("is-active", active);
      item.setAttribute("aria-selected", active ? "true" : "false");
      item.setAttribute("tabindex", active ? "0" : "-1");
    });
    Array.prototype.forEach.call(rolePanels, function (panel) {
      var active = panel.id === targetId;
      panel.hidden = !active;
      panel.classList.toggle("is-active", active);
    });

    if (moveFocus) tab.focus();
  }

  Array.prototype.forEach.call(roleTabs, function (tab, index) {
    tab.addEventListener("click", function () {
      activateRole(tab, false);
    });
    tab.addEventListener("keydown", function (event) {
      var nextIndex = index;
      if (event.key === "ArrowLeft") nextIndex = (index + 1) % roleTabs.length;
      else if (event.key === "ArrowRight") nextIndex = (index - 1 + roleTabs.length) % roleTabs.length;
      else if (event.key === "Home") nextIndex = 0;
      else if (event.key === "End") nextIndex = roleTabs.length - 1;
      else return;

      event.preventDefault();
      activateRole(roleTabs[nextIndex], true);
    });
  });

  // بطاقات الأدوار في الهيرو تنقل الزائر إلى قسم الأدوار مفتوحًا على دوره،
  // لا إلى أول تبويب. الوسم منفصل عن ``data-role-target`` كي لا تدخل البطاقة
  // في مجموعة التبويبات ولا في تنقّل الأسهم داخلها.
  Array.prototype.forEach.call(
    document.querySelectorAll("[data-role-jump]"),
    function (link) {
      link.addEventListener("click", function () {
        var tab = document.querySelector(
          '[data-role-target="' + link.getAttribute("data-role-jump") + '"]'
        );
        if (tab) activateRole(tab, false);
      });
    }
  );

  if (document.body.classList.contains("landing-page")) {
    document.body.classList.add("landing-assistant-delayed");
    var syncLandingScrollState = function () {
      document.body.classList.toggle(
        "landing-page-has-scrolled",
        window.scrollY > 320
      );
      // حالةٌ ثانية بعتبةٍ مختلفة عن قصد: الشريط العلوي يكتسب حدَّه وظلَّه بمجرد
      // أن يبدأ المحتوى بالمرور تحته، بينما عتبة 320 أعلاه تخصّ ظهور مُشغّل
      // المساعد — ربطُهما بعتبةٍ واحدة يجعل أحدهما يتأخّر أو الآخر يتعجّل.
      document.body.classList.toggle("landing-page-is-lifted", window.scrollY > 8);
    };
    syncLandingScrollState();
    window.addEventListener("scroll", syncLandingScrollState, { passive: true });
  }

  var lightbox = document.getElementById("productLightbox");
  var lightboxImage = document.getElementById("productLightboxImage");
  var lightboxCaption = document.getElementById("productLightboxCaption");
  var lightboxClose = document.getElementById("productLightboxClose");

  Array.prototype.forEach.call(
    document.querySelectorAll("[data-product-image]"),
    function (trigger) {
      trigger.addEventListener("click", function () {
        var source = trigger.getAttribute("data-product-image");
        var image = trigger.querySelector("img");
        if (!source) return;

        if (!lightbox || !lightbox.showModal) {
          var openedImage = window.open(source, "_blank");
          if (openedImage) openedImage.opener = null;
          return;
        }

        if (lightboxImage) {
          lightboxImage.src = source;
          lightboxImage.alt = image ? image.alt : "";
        }
        if (lightboxCaption) {
          lightboxCaption.textContent =
            trigger.getAttribute("data-product-caption") || "";
        }
        lightbox.showModal();
      });
    }
  );

  if (lightboxClose && lightbox) {
    lightboxClose.addEventListener("click", function () {
      lightbox.close();
    });
  }
  if (lightbox) {
    lightbox.addEventListener("click", function (event) {
      if (event.target === lightbox) lightbox.close();
    });
    lightbox.addEventListener("close", function () {
      if (lightboxImage) lightboxImage.removeAttribute("src");
    });
  }

  var revealItems = document.querySelectorAll(".reveal");
  if (
    !("IntersectionObserver" in window)
    || window.matchMedia("(prefers-reduced-motion: reduce)").matches
  ) {
    Array.prototype.forEach.call(revealItems, function (item) {
      item.classList.add("is-visible");
    });
  } else {
    var observer = new IntersectionObserver(
      function (entries) {
        Array.prototype.forEach.call(entries, function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.12, rootMargin: "0px 0px -40px" }
    );
    Array.prototype.forEach.call(revealItems, function (item) {
      observer.observe(item);
    });
  }

}());
