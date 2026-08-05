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
