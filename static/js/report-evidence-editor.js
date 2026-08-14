/* محرر الشواهد المصورة — اسم ملف مستقل لكسر أي نسخة مخبأة من المحرر القديم. */
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

    card.querySelectorAll("[data-image-source]").forEach(function (button) {
      button.addEventListener("click", function () {
        if (!input) return;
        if (button.getAttribute("data-image-source") === "camera") {
          input.setAttribute("capture", "environment");
          input.click();
        } else {
          input.removeAttribute("capture");
          // الـ label يفتح المنتقي الأصلي عبر for؛ لا نكرر click برمجياً.
        }
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
      if (note) note.className = "ree-file-note";
      var extension = (file.name.split(".").pop() || "").toLowerCase();
      if (["jpg", "jpeg", "png", "webp"].indexOf(extension) === -1) {
        if (note) {
          note.textContent = "صيغة الصورة غير مدعومة. اختر JPG أو PNG أو WebP.";
          note.classList.add("is-error");
        }
        input.value = "";
        return;
      }
      if (file.size > MAX_UPLOAD_BYTES) {
        if (note) {
          note.textContent = "حجم الملف أكبر من 10MB ولن يقبله النظام.";
          note.classList.add("is-error");
        }
      } else if (file.size > WARNING_BYTES) {
        if (note) {
          note.textContent = "تم اختيار " + file.name + " — الصورة كبيرة وسيحسنها النظام قبل الحفظ.";
          note.classList.add("is-warning");
        }
      } else {
        if (note) note.textContent = "تم اختيار " + file.name + " · " + (file.size / 1024 / 1024).toFixed(1) + "MB";
      }

      if (!preview) return;
      preview.onload = function () {
        var width = preview.naturalWidth;
        var height = preview.naturalHeight;
        var divisor = gcd(width, height);
        if (ratio) ratio.textContent = width + " × " + height + " · " + (width / divisor) + ":" + (height / divisor);
      };
      preview.onerror = function () {
        if (note) {
          note.textContent = "تعذرت معاينة الصورة. اختر ملف JPG أو PNG أو WebP صالحًا.";
          note.classList.add("is-error");
        }
      };
      var reader = new FileReader();
      reader.onload = function () {
        preview.src = reader.result;
        preview.hidden = false;
        if (placeholder) placeholder.hidden = true;
        if (deletion) { deletion.checked = false; card.classList.remove("is-deleted"); }
        syncFit();
      };
      reader.onerror = preview.onerror;
      reader.readAsDataURL(file);
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
