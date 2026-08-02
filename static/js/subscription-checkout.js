(() => {
  "use strict";

  function parsePrice(value) {
    let normalized = String(value ?? "")
      .trim()
      .replace(/\s/g, "")
      .replace(/٬/g, "")
      .replace(/٫/g, ".");

    if (normalized.includes(",") && !normalized.includes(".")) {
      normalized = normalized.replace(",", ".");
    } else {
      normalized = normalized.replace(/,/g, "");
    }

    const number = Number(normalized);
    return Number.isFinite(number) ? number : 0;
  }

  function formatAmount(value) {
    return new Intl.NumberFormat("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(Math.round((value + Number.EPSILON) * 100) / 100);
  }

  function init(root = document) {
    const pageRoot =
      root && root.id === "archiveOrder"
        ? root
        : root.querySelector?.("#archiveOrder");
    if (!pageRoot) return null;
    if (pageRoot.__subscriptionCheckout) {
      return pageRoot.__subscriptionCheckout;
    }

    const form = pageRoot.querySelector("#paymentForm");
    if (!form) return null;

    const orderToggles = form.querySelectorAll(".order-toggle");
    const planRadios = form.querySelectorAll('input[name="plan_id"]');
    const storageSelect = form.querySelector("#archiveStorageUnits");
    const orderTotal = form.querySelector("#orderTotal");
    const orderEmptyState = form.querySelector("#orderEmptyState");
    const receiptSubmit = form.querySelector("#submitBtn");
    const tamaraSubmit = form.querySelector("#tamaraSubmit");
    const tamaraCheckout = form.querySelector(".tamara-checkout");
    const tamaraInstallmentAmount = form.querySelector(
      "#tamaraInstallmentAmount",
    );
    const tamaraInstallmentAmounts = form.querySelectorAll(
      "[data-tamara-installment-amount]",
    );

    function input(name) {
      return form.querySelector(`input[name="${name}"]`);
    }

    function isOn(name) {
      const control = input(name);
      return Boolean(control && control.checked && !control.disabled);
    }

    function selectedPlan() {
      return form.querySelector('input[name="plan_id"]:checked');
    }

    function subscriptionAmount() {
      const plan = selectedPlan();
      return plan ? parsePrice(plan.dataset.price) : 0;
    }

    function addonAmount() {
      const addon = input("include_archive_addon");
      return addon?.dataset.price ? parsePrice(addon.dataset.price) : 0;
    }

    function storageAmount() {
      if (!storageSelect) return null;
      const option = storageSelect.options[storageSelect.selectedIndex];
      return option ? parsePrice(option.dataset.price) : 0;
    }

    function setItemAmount(key, value) {
      const element = form.querySelector(`[data-amount-for="${key}"]`);
      if (element) {
        element.textContent =
          value == null ? "—" : `${formatAmount(value)} ريال`;
      }
    }

    function setItemLabel(key, value) {
      const element = form.querySelector(`[data-summary-label="${key}"]`);
      if (element && value) element.textContent = value;
    }

    function setItemVisibility(key, visible) {
      const row = form.querySelector(`[data-summary-for="${key}"]`);
      if (row) row.hidden = !visible;
    }

    function setSubmitState(button, enabled, enabledLabel, disabledLabel) {
      if (!button) return;
      button.disabled = !enabled;
      button.setAttribute("aria-disabled", String(!enabled));
      const label = button.querySelector("span");
      if (label && !button.classList.contains("loading")) {
        label.textContent = enabled ? enabledLabel : disabledLabel;
      }
    }

    function recompute() {
      const subscriptionSelected = isOn("include_subscription");
      const addonSelected = isOn("include_archive_addon");
      const storageSelected = isOn("include_archive_storage");
      const anySelected =
        subscriptionSelected || addonSelected || storageSelected;
      const plan = selectedPlan();
      const selectedStorage = storageSelect
        ? storageSelect.options[storageSelect.selectedIndex]
        : null;
      const planName = plan
        ? plan
            .closest(".plan-duration-choice")
            ?.querySelector(".duration-copy strong")
            ?.textContent.trim()
        : "";

      setItemAmount("subscription", subscriptionAmount());
      setItemAmount("addon", addonAmount());
      setItemAmount("storage", storageAmount());
      setItemLabel("subscription", planName || "تجديد اشتراك المدرسة");
      setItemLabel(
        "storage",
        selectedStorage
          ? `مساحة الأرشيف ${selectedStorage.textContent
              .trim()
              .split("—")[0]
              .trim()}`
          : "مساحة إضافية للأرشيف",
      );
      setItemVisibility("subscription", subscriptionSelected);
      setItemVisibility("addon", addonSelected);
      setItemVisibility("storage", storageSelected);

      let total = 0;
      if (subscriptionSelected) total += subscriptionAmount();
      if (addonSelected) total += addonAmount();
      if (storageSelected) total += storageAmount() || 0;

      if (orderTotal) orderTotal.textContent = formatAmount(total);
      const installment = total / 4;
      const formattedInstallment = formatAmount(installment);
      if (tamaraInstallmentAmount) {
        tamaraInstallmentAmount.textContent = formattedInstallment;
      }
      tamaraInstallmentAmounts.forEach((element) => {
        element.textContent = `${formattedInstallment} ر.س`;
      });
      if (tamaraCheckout) {
        tamaraCheckout.classList.toggle("is-ready", anySelected);
      }
      if (orderEmptyState) orderEmptyState.hidden = anySelected;
      setSubmitState(
        receiptSubmit,
        anySelected,
        "إرسال إيصال التحويل البنكي",
        "اختر خدمة لإرسال إيصال التحويل",
      );
      setSubmitState(
        tamaraSubmit,
        anySelected,
        "المتابعة والدفع عبر تمارا",
        "اختر خدمة للدفع عبر تمارا",
      );
    }

    orderToggles.forEach((toggle) => {
      const sync = () => {
        const item = toggle.closest(".order-item");
        if (item) {
          item.classList.toggle(
            "is-active",
            toggle.checked && !toggle.disabled,
          );
        }
        recompute();
      };
      toggle.addEventListener("change", sync);
      sync();
    });

    planRadios.forEach((radio) => {
      radio.addEventListener("change", () => {
        const subscriptionToggle = input("include_subscription");
        if (subscriptionToggle && !subscriptionToggle.disabled) {
          subscriptionToggle.checked = true;
          subscriptionToggle.dispatchEvent(
            new Event("change", { bubbles: true }),
          );
        }
        recompute();
      });
    });

    if (storageSelect) {
      storageSelect.addEventListener("change", () => {
        const storageToggle = input("include_archive_storage");
        if (storageToggle && !storageToggle.disabled) {
          storageToggle.checked = true;
          storageToggle.dispatchEvent(
            new Event("change", { bubbles: true }),
          );
        }
        recompute();
      });
    }

    const api = {
      form,
      isOn,
      recompute,
      receiptSubmit,
      tamaraSubmit,
    };
    Object.defineProperty(pageRoot, "__subscriptionCheckout", {
      configurable: false,
      enumerable: false,
      value: api,
      writable: false,
    });
    pageRoot.dataset.checkoutReady = "1";
    recompute();
    return api;
  }

  window.SubscriptionCheckout = Object.freeze({ init });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => init(), {
      once: true,
    });
  } else {
    init();
  }
})();
