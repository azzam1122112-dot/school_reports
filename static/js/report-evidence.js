(function () {
  "use strict";

  var MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
  var WARNING_BYTES = 5 * 1024 * 1024;

  function gcd(a, b) {
    while (b) { var next = a % b; a = b; b = next; }
    return a || 1;
  }

  function updateOrder(editor) {
    var cards = Array.prototype.slice.call(editor.querySelectorAll("[data-evidence-card]"));
    cards.forEach(function (card, index) {
      var order = card.querySelector('input[name$="-order"]');
      var number = card.querySelector("[data-evidence-number]");
      if (order) order.value = index + 1;
      if (number) number.textContent = String(index + 1).padStart(2, "0");
    });
  }

  function bindCard(editor, card) {
    var input = card.querySelector('input[type="file"]');
    var preview = card.querySelector("[data-preview-image]");
    var placeholder = card.querySelector("[data-preview-placeholder]");
    var ratio = card.querySelector("[data-image-ratio]");
    var note = card.querySelector("[data-file-note]");
    var fit = card.querySelector('select[name$="-fit_mode"]');
    var deletion = card.querySelector('input[name$="-DELETE"]');
    var description = card.querySelector('input[name$="-description"]');

    card.querySelectorAll("[data-image-source]").forEach(function (button) {
      button.addEventListener("click", function () {
        if (!input) return;
        if (button.getAttribute("data-image-source") === "camera") input.setAttribute("capture", "environment");
        else input.removeAttribute("capture");
        input.click();
      });
    });

    function syncFit() {
      if (preview && fit) preview.style.objectFit = fit.value === "cover" ? "cover" : "contain";
    }
    if (fit) fit.addEventListener("change", syncFit);
    syncFit();

    if (deletion) {
      deletion.addEventListener("change", function () {
        card.classList.toggle("is-deleted", deletion.checked);
      });
    }

    if (input) input.addEventListener("change", function () {
      var file = input.files && input.files[0];
      input.removeAttribute("capture");
      if (!file) return;
      note.className = "ree-file-note";
      var extension = (file.name.split(".").pop() || "").toLowerCase();
      if (["jpg", "jpeg", "png", "webp"].indexOf(extension) === -1) {
        note.textContent = "صيغة الصورة غير مدعومة. اختر JPG أو PNG أو WebP.";
        note.classList.add("is-error");
        input.value = "";
        return;
      }
      if (file.size > MAX_UPLOAD_BYTES) {
        note.textContent = "حجم الملف أكبر من 10MB ولن يقبله النظام.";
        note.classList.add("is-error");
      } else if (file.size > WARNING_BYTES) {
        note.textContent = "الصورة كبيرة؛ سيحسنها السيرفر قبل الحفظ.";
        note.classList.add("is-warning");
      } else {
        note.textContent = "الحجم قبل التحسين: " + (file.size / 1024 / 1024).toFixed(1) + "MB";
      }

      var url = URL.createObjectURL(file);
      preview.onload = function () {
        var width = preview.naturalWidth;
        var height = preview.naturalHeight;
        var divisor = gcd(width, height);
        ratio.textContent = width + " × " + height + " · " + (width / divisor) + ":" + (height / divisor);
        URL.revokeObjectURL(url);
      };
      preview.onerror = function () {
        note.textContent = "تعذرت معاينة الصورة. اختر ملف JPG أو PNG أو WebP صالحًا.";
        note.classList.add("is-error");
        URL.revokeObjectURL(url);
      };
      preview.src = url;
      preview.hidden = false;
      if (placeholder) placeholder.hidden = true;
      if (deletion) { deletion.checked = false; card.classList.remove("is-deleted"); }
      if (description) {
        description.required = true;
        description.setAttribute("aria-required", "true");
      }
      syncFit();
    });

    card.querySelectorAll("[data-move]").forEach(function (button) {
      button.addEventListener("click", function () {
        var direction = button.getAttribute("data-move");
        var sibling = direction === "up" ? card.previousElementSibling : card.nextElementSibling;
        if (!sibling) return;
        if (direction === "up") card.parentNode.insertBefore(card, sibling);
        else card.parentNode.insertBefore(sibling, card);
        updateOrder(editor);
      });
    });
  }

  document.querySelectorAll("[data-report-evidence-editor]").forEach(function (editor) {
    editor.querySelectorAll("[data-evidence-card]").forEach(function (card) { bindCard(editor, card); });
    updateOrder(editor);
  });
})();
