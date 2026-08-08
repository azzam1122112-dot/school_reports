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
    const planChoices = form.querySelectorAll(".plan-duration-choice");
    const storageSelect = form.querySelector("#archiveStorageUnits");
    // مساحتان منفصلتان تُباعان بمنتجين منفصلين، فلكل واحدة قائمتها وسطرها.
    const archiveSpaceSelect = form.querySelector("#archiveSpaceUnits");
    const orderTotal = form.querySelector("#orderTotal");
    const orderEmptyState = form.querySelector("#orderEmptyState");
    const receiptSubmit = form.querySelector("#submitBtn");
    const tamaraSubmit = form.querySelector("#tamaraSubmit");
    const moyasarSubmit = form.querySelector("#moyasarSubmit");
    const paymentChoices = form.querySelectorAll("[data-payment-choice]");
    const paymentPanels = form.querySelectorAll("[data-payment-panel]");
    const tamaraCheckout = form.querySelector('[data-payment-panel="tamara"]');
    const tamaraInstallmentAmount = form.querySelector(
      "#tamaraInstallmentAmount",
    );
    const tamaraInstallmentAmounts = form.querySelectorAll(
      "[data-tamara-installment-amount]",
    );
    const mobileSelectedCount = document.querySelector(
      "#mobileSelectedCount",
    );
    const mobileOrderTotal = document.querySelector("#mobileOrderTotal");
    const mobileCheckoutContinue = document.querySelector(
      "#mobileCheckoutContinue",
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

    function archiveSpaceAmount() {
      if (!archiveSpaceSelect) return null;
      const option =
        archiveSpaceSelect.options[archiveSpaceSelect.selectedIndex];
      return option ? parsePrice(option.dataset.price) : 0;
    }

    function optionSizeLabel(select) {
      if (!select) return "";
      const option = select.options[select.selectedIndex];
      if (!option) return "";
      return option.textContent.trim().split("—")[0].trim();
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
      // A button left in its "submitting" state is unrecoverable: the label
      // would never be rewritten again, so a failed or blocked submission
      // leaves a dead button reading "sending…" forever. Clear the state
      // whenever we are asked to describe the button afresh.
      button.classList.remove("loading");
      button.disabled = !enabled;
      button.setAttribute("aria-disabled", String(!enabled));
      const label = button.querySelector("span");
      if (label) {
        label.textContent = enabled ? enabledLabel : disabledLabel;
      }
    }

    function recompute() {
      const subscriptionSelected = isOn("include_subscription");
      const addonSelected = isOn("include_archive_addon");
      const storageSelected = isOn("include_archive_storage");
      const archiveSpaceSelected = isOn("include_archive_space");
      const anySelected =
        subscriptionSelected ||
        addonSelected ||
        storageSelected ||
        archiveSpaceSelected;
      const plan = selectedPlan();
      const planName = plan
        ? plan.dataset.label || plan
            .closest(".plan-duration-choice")
            ?.querySelector(".duration-copy strong")
            ?.textContent.trim()
        : "";

      setItemAmount("subscription", subscriptionAmount());
      setItemAmount("addon", addonAmount());
      setItemAmount("storage", storageAmount());
      setItemAmount("archiveSpace", archiveSpaceAmount());
      setItemLabel("subscription", planName || "تجديد اشتراك المدرسة");
      // Storage is bought with the capacity, so name it before payment.
      const storageNote = form.querySelector("[data-summary-storage]");
      if (storageNote) {
        const includedStorage = plan ? plan.dataset.storage : "";
        storageNote.textContent = includedStorage
          ? `يشمل مساحة تخزين ${includedStorage}`
          : "";
        storageNote.hidden = !includedStorage;
      }
      const workSize = optionSizeLabel(storageSelect);
      setItemLabel(
        "storage",
        workSize
          ? `مساحة عمل المدرسة ${workSize}`
          : "مساحة إضافية لعمل المدرسة",
      );
      const archiveSize = optionSizeLabel(archiveSpaceSelect);
      setItemLabel(
        "archiveSpace",
        archiveSize
          ? `مساحة الأرشفة السنوية ${archiveSize}`
          : "مساحة إضافية للأرشفة السنوية",
      );
      setItemVisibility("subscription", subscriptionSelected);
      setItemVisibility("addon", addonSelected);
      setItemVisibility("storage", storageSelected);
      setItemVisibility("archiveSpace", archiveSpaceSelected);

      let total = 0;
      if (subscriptionSelected) total += subscriptionAmount();
      if (addonSelected) total += addonAmount();
      if (storageSelected) total += storageAmount() || 0;
      if (archiveSpaceSelected) total += archiveSpaceAmount() || 0;

      const selectedCount = [
        subscriptionSelected,
        addonSelected,
        storageSelected,
        archiveSpaceSelected,
      ].filter(Boolean).length;

      if (orderTotal) orderTotal.textContent = formatAmount(total);
      if (mobileOrderTotal) mobileOrderTotal.textContent = formatAmount(total);
      if (mobileSelectedCount) {
        mobileSelectedCount.textContent = `${selectedCount} ${
          selectedCount === 1 ? "خدمة" : "خدمات"
        }`;
      }
      setSubmitState(
        mobileCheckoutContinue,
        anySelected,
        "متابعة للدفع",
        "اختر خدمة أولًا",
      );
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
      setSubmitState(
        moyasarSubmit,
        anySelected,
        "المتابعة للدفع الإلكتروني",
        "اختر خدمة للمتابعة",
      );
    }

    function selectPaymentMethod(method) {
      paymentChoices.forEach((choice) => {
        const selected = choice.dataset.paymentChoice === method;
        choice.classList.toggle("is-selected", selected);
        choice.setAttribute("aria-pressed", String(selected));
      });
      paymentPanels.forEach((panel) => {
        panel.hidden = panel.dataset.paymentPanel !== method;
      });
    }

    paymentChoices.forEach((choice) => {
      choice.addEventListener("click", () => {
        selectPaymentMethod(choice.dataset.paymentChoice || "bank_transfer");
      });
    });

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

    function activateSubscription() {
      const subscriptionToggle = input("include_subscription");
      if (subscriptionToggle && !subscriptionToggle.disabled) {
        subscriptionToggle.checked = true;
        subscriptionToggle.dispatchEvent(
          new Event("change", { bubbles: true }),
        );
      }
    }

    // Listen on the visible card, not only the radio. A checked default radio
    // does not emit `change` when its card is clicked again.
    planChoices.forEach((choice) => {
      choice.addEventListener("click", () => {
        activateSubscription();
        recompute();
      });
    });

    planRadios.forEach((radio) => {
      radio.addEventListener("change", () => {
        recompute();
      });
    });

    form.addEventListener("flex-pricing:change", () => {
      activateSubscription();
      recompute();
    });

    // اختيار حجم يعني نيّة الشراء، فيُفعّل مفتاح بنده — لكلٍّ مفتاحه، وإلا
    // اختار المدير مساحة أرشفة فأُضيفت مساحة عمل إلى الطلب.
    [
      [storageSelect, "include_archive_storage"],
      [archiveSpaceSelect, "include_archive_space"],
    ].forEach(([select, toggleName]) => {
      if (!select) return;
      select.addEventListener("change", () => {
        const toggle = input(toggleName);
        if (toggle && !toggle.disabled) {
          toggle.checked = true;
          toggle.dispatchEvent(new Event("change", { bubbles: true }));
        }
        recompute();
      });
    });

    if (mobileCheckoutContinue) {
      mobileCheckoutContinue.addEventListener("click", () => {
        if (mobileCheckoutContinue.disabled) return;
        const selectedMethod = form.querySelector(
          "[data-payment-choice].is-selected",
        )?.dataset.paymentChoice;
        const target =
          (selectedMethod &&
            form.querySelector(`[data-payment-panel="${selectedMethod}"]`)) ||
          form.querySelector(".payment-method-heading");
        target?.scrollIntoView({ behavior: "smooth", block: "center" });
      });
    }

    const api = {
      form,
      isOn,
      recompute,
      receiptSubmit,
      tamaraSubmit,
      moyasarSubmit,
      selectPaymentMethod,
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
