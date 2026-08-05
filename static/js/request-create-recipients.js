/* Request create page: resilient recipients loader (external script, CSP-safe). */
(function () {
  if (window.__ticketRecipientsExternalReady) return;
  window.__ticketRecipientsExternalReady = true;

  function ready(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn, { once: true });
      return;
    }
    fn();
  }

  ready(function () {
    var dept = document.getElementById("id_department");
    var box = document.getElementById("id_recipients");
    if (!dept || !box) return;

    var endpoint = box.getAttribute("data-members-url") || "";
    if (!endpoint) return;

    function parseSelectedFromData() {
      var raw = box.getAttribute("data-selected") || "";
      if (!raw) return new Set();
      return new Set(
        raw
          .split(",")
          .map(function (v) {
            return (v || "").trim();
          })
          .filter(Boolean)
      );
    }

    function selectedIdsFromInputs() {
      return Array.from(box.querySelectorAll('input[type="checkbox"][name="recipients"]:checked')).map(
        function (el) {
          return String(el.value);
        }
      );
    }

    function setHelp(text, isError) {
      var cls = isError ? "error-text" : "help-text";
      box.innerHTML = '<div class="' + cls + '">' + text + "</div>";
    }

    function renderRecipients(items) {
      if (!Array.isArray(items) || items.length === 0) {
        setHelp("لا يوجد موظفون مسجلون في هذا القسم حالياً.", false);
        return;
      }

      var preSelected = parseSelectedFromData();
      var html = ['<div class="recipients-grid">'];
      items.forEach(function (u) {
        var id = String(u.id);
        var checked = preSelected.has(id) ? " checked" : "";
        var safeName = String(u.name || "—")
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;");
        html.push(
          '<label class="rcpt' +
            (checked ? " selected" : "") +
            '"><input type="checkbox" name="recipients" value="' +
            id +
            '"' +
            checked +
            "><span>" +
            safeName +
            "</span></label>"
        );
      });
      html.push("</div>");
      box.innerHTML = html.join("");

      box.querySelectorAll('input[type="checkbox"][name="recipients"]').forEach(function (cb) {
        cb.addEventListener("change", function () {
          var label = cb.closest("label");
          if (!label) return;
          label.classList.toggle("selected", cb.checked);
        });
      });

      // Convenience: select only member automatically when no prior selection.
      if (items.length === 1 && selectedIdsFromInputs().length === 0) {
        var first = box.querySelector('input[type="checkbox"][name="recipients"]');
        if (first) {
          first.checked = true;
          var firstLabel = first.closest("label");
          if (firstLabel) firstLabel.classList.add("selected");
        }
      }
    }

    async function loadMembers(slug) {
      if (!slug) {
        setHelp("الرجاء اختيار القسم أولاً.", false);
        return;
      }

      box.innerHTML = '<div class="help-text">جاري تحميل الموظفين...</div>';
      try {
        var url = endpoint + "?department=" + encodeURIComponent(slug);
        var response = await fetch(url, {
          method: "GET",
          headers: { "X-Requested-With": "XMLHttpRequest" },
          credentials: "same-origin",
        });
        if (!response.ok) throw new Error("HTTP " + response.status);

        var payload = await response.json();
        var list = Array.isArray(payload && payload.results) ? payload.results : [];
        renderRecipients(list);
      } catch (err) {
        console.error("Failed to load recipients", err);
        setHelp("تعذر تحميل بيانات الموظفين.", true);
      }
    }

    dept.addEventListener("change", function (event) {
      box.removeAttribute("data-selected");
      var slug = String((event && event.target && event.target.value) || "").trim();
      loadMembers(slug);
    });

    var initialSlug = String(dept.value || "").trim();
    if (initialSlug) loadMembers(initialSlug);
    else setHelp("الرجاء اختيار القسم أولاً.", false);
  });
})();
