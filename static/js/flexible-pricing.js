(function () {
  "use strict";

  function number(value, fallback) {
    var parsed = Number(String(value == null ? "" : value).replace(/,/g, ""));
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function format(value) {
    return new Intl.NumberFormat("en-US", {
      maximumFractionDigits: 0,
    }).format(value || 0);
  }

  function init(root) {
    if (!root || root.__flexiblePricing) return root && root.__flexiblePricing;
    var sourceId = root.getAttribute("data-flex-pricing-source");
    var source = sourceId ? document.getElementById(sourceId) : null;
    if (!source) return null;

    var catalog;
    try {
      catalog = JSON.parse(source.textContent || "{}");
    } catch (_error) {
      return null;
    }
    if (!catalog.periods || !catalog.periods.length) return null;

    var countInput = root.querySelector("[data-flex-teacher-count]");
    var planInput = root.querySelector("[data-flex-plan-input]");
    var capacityInput = root.querySelector("[data-flex-capacity-input]");
    var priceOutput = root.querySelector("[data-flex-price]");
    var capacityOutput = root.querySelector("[data-flex-capacity]");
    var countOutput = root.querySelector("[data-flex-count-output]");
    var spareOutput = root.querySelector("[data-flex-spare]");
    var monthlyOutput = root.querySelector("[data-flex-monthly]");
    var statusOutput = root.querySelector("[data-flex-status]");
    var result = root.querySelector("[data-flex-result]");
    var periodButtons = root.querySelectorAll("[data-flex-period]");
    var minimumCount = number(root.getAttribute("data-min-teacher-count"), 1);
    var initialPeriod = root.getAttribute("data-initial-period") || "1y";
    var activePeriod = catalog.periods.some(function (period) {
      return period.key === initialPeriod;
    }) ? initialPeriod : catalog.periods[catalog.periods.length - 1].key;

    function period() {
      return catalog.periods.find(function (item) {
        return item.key === activePeriod;
      }) || catalog.periods[0];
    }

    function quoteFor(teacherCount) {
      return period().quotes.find(function (quote) {
        return quote.capacity >= teacherCount;
      }) || null;
    }

    function setPeriod(periodKey) {
      if (!catalog.periods.some(function (item) { return item.key === periodKey; })) return;
      activePeriod = periodKey;
      render();
    }

    function render() {
      var rawCount = Math.round(number(countInput && countInput.value, minimumCount));
      var teacherCount = Math.max(rawCount, minimumCount, 1);
      if (countInput && String(teacherCount) !== countInput.value) {
        countInput.value = String(teacherCount);
      }
      var quote = quoteFor(teacherCount);

      Array.prototype.forEach.call(periodButtons, function (button) {
        var selected = button.getAttribute("data-flex-period") === activePeriod;
        button.classList.toggle("is-active", selected);
        button.setAttribute("aria-pressed", selected ? "true" : "false");
      });
      if (countOutput) countOutput.textContent = format(teacherCount);

      if (!quote) {
        root.classList.add("is-over-limit");
        if (result) result.setAttribute("aria-disabled", "true");
        if (priceOutput) priceOutput.textContent = "عرض مخصص";
        if (capacityOutput) capacityOutput.textContent = "+100";
        if (spareOutput) spareOutput.textContent = "—";
        if (monthlyOutput) monthlyOutput.textContent = "تواصل معنا لتجهيز سعة أكبر";
        if (statusOutput) statusOutput.textContent = "هذه السعة تحتاج عرضاً مخصصاً من فريق توثيق.";
        if (planInput) {
          planInput.value = "";
          planInput.checked = false;
          planInput.removeAttribute("data-price");
        }
        if (capacityInput) capacityInput.value = "";
        root.dispatchEvent(new CustomEvent("flex-pricing:change", { bubbles: true, detail: null }));
        return;
      }

      var spare = Math.max(quote.capacity - teacherCount, 0);
      root.classList.remove("is-over-limit");
      if (result) result.removeAttribute("aria-disabled");
      if (priceOutput) priceOutput.textContent = quote.price_display || format(quote.price);
      if (capacityOutput) capacityOutput.textContent = format(quote.capacity);
      if (spareOutput) spareOutput.textContent = format(spare);
      if (monthlyOutput) monthlyOutput.textContent = "يعادل " + (quote.monthly_equivalent_display || format(quote.monthly_equivalent)) + " ريال شهرياً";
      if (statusOutput) {
        statusOutput.textContent = spare
          ? "سعة مناسبة مع " + format(spare) + " مقاعد احتياطية للنمو."
          : "سعة مطابقة لعدد فريقك دون مقاعد غير مستخدمة.";
      }
      if (planInput) {
        planInput.value = String(quote.plan_id);
        planInput.checked = true;
        planInput.setAttribute("data-price", String(quote.price));
        planInput.setAttribute("data-label", "اشتراك بسعة " + format(quote.capacity) + " معلماً · " + period().label);
      }
      if (capacityInput) capacityInput.value = String(quote.capacity);

      root.dispatchEvent(new CustomEvent("flex-pricing:change", {
        bubbles: true,
        detail: {
          period: activePeriod,
          teacherCount: teacherCount,
          capacity: quote.capacity,
          spare: spare,
          price: quote.price,
          planId: quote.plan_id,
        },
      }));
    }

    Array.prototype.forEach.call(periodButtons, function (button) {
      button.addEventListener("click", function () {
        setPeriod(button.getAttribute("data-flex-period"));
      });
    });
    if (countInput) {
      countInput.addEventListener("input", render);
      countInput.addEventListener("change", render);
    }
    var decrease = root.querySelector("[data-flex-decrease]");
    var increase = root.querySelector("[data-flex-increase]");
    if (decrease && countInput) {
      decrease.addEventListener("click", function () {
        countInput.value = String(Math.max(minimumCount, number(countInput.value, minimumCount) - 1));
        render();
      });
    }
    if (increase && countInput) {
      increase.addEventListener("click", function () {
        countInput.value = String(number(countInput.value, minimumCount) + 1);
        render();
      });
    }

    var api = { render: render, setPeriod: setPeriod };
    Object.defineProperty(root, "__flexiblePricing", { value: api });
    render();
    return api;
  }

  function initAll() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-flex-pricing]"), init);
  }

  window.FlexiblePricing = Object.freeze({ init: init, initAll: initAll });
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll, { once: true });
  } else {
    initAll();
  }
}());
