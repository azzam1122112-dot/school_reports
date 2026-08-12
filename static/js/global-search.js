/* static/js/global-search.js
   ─────────────────────────────────────────────────────────────────────────
   صندوق البحث الموحّد في الترويسة.

   ثلاثة قرارات تستحق التفسير:

   * **التأخير قبل الطلب (debounce).** كل نداء يمرّ على سبعة جداول. والإرسال
     مع كل ضغطة مفتاح كان سيُصدر عشرة طلبات لكلمة من عشرة أحرف — تسعةٌ منها
     نتيجتُها مُهدرة قبل أن تصل.

   * **إلغاء الطلب السابق.** بلا ``AbortController`` تتسابق الردود: طلبٌ بطيء
     لحرفين قد يصل بعد طلبٍ سريع لخمسة، فيكتب نتائجَ قديمة فوق الجديدة. وهو
     عطلٌ يظهر متقطّعاً ويستحيل تفسيره لمن يبلّغ عنه.

   * **البناء عبر ``textContent`` لا ``innerHTML``.** عناوينُ النتائج محتوى
     مستخدمين — عنوانُ تقرير كتبه معلّم. وبناؤها كسلسلة HTML يجعل كل عنوان
     ناقلاً محتملاً للحقن. و``textContent`` يُغلق الباب بنيوياً لا بالتهريب.
   ───────────────────────────────────────────────────────────────────────── */
(function () {
  "use strict";

  var root = document.querySelector("[data-global-search]");
  if (!root) return;

  var input = root.querySelector("#globalSearchInput");
  var panel = root.querySelector("#globalSearchResults");
  var list = root.querySelector("#globalSearchList");
  var empty = root.querySelector("#globalSearchEmpty");
  var status = root.querySelector("#globalSearchStatus");
  var clearButton = root.querySelector("#globalSearchClear");
  if (!input || !panel || !list) return;

  var endpoint = input.getAttribute("data-search-url");
  var minLength = parseInt(input.getAttribute("data-min-length"), 10) || 2;
  var DEBOUNCE_MS = 220;

  var timer = null;
  var controller = null;
  var activeIndex = -1;
  var options = [];

  function close() {
    panel.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    activeIndex = -1;
  }

  function open() {
    panel.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }

  function highlight(index) {
    options.forEach(function (option, i) {
      var isActive = i === index;
      option.classList.toggle("is-active", isActive);
      option.setAttribute("aria-selected", isActive ? "true" : "false");
    });
    if (index >= 0 && options[index]) {
      input.setAttribute("aria-activedescendant", options[index].id);
      options[index].scrollIntoView({ block: "nearest" });
    } else {
      input.removeAttribute("aria-activedescendant");
    }
    activeIndex = index;
  }

  function render(results) {
    list.textContent = "";
    options = [];

    results.forEach(function (hit, index) {
      var item = document.createElement("li");
      item.className = "gsearch__item";
      item.id = "gsearch-option-" + index;
      item.setAttribute("role", "option");
      item.setAttribute("aria-selected", "false");

      var link = document.createElement("a");
      link.className = "gsearch__link";
      link.href = hit.url;

      var icon = document.createElement("i");
      icon.className = "fa-solid " + (hit.icon || "fa-circle");
      icon.setAttribute("aria-hidden", "true");

      var body = document.createElement("span");
      body.className = "gsearch__body";

      var title = document.createElement("strong");
      title.className = "gsearch__title";
      // محتوى مستخدمين — لا يُبنى كـHTML أبداً.
      title.textContent = hit.title || "";

      var meta = document.createElement("small");
      meta.className = "gsearch__meta";
      meta.textContent = [hit.label, hit.subtitle].filter(Boolean).join(" · ");

      body.appendChild(title);
      body.appendChild(meta);
      link.appendChild(icon);
      link.appendChild(body);
      item.appendChild(link);
      list.appendChild(item);
      options.push(item);
    });

    var found = results.length > 0;
    if (empty) empty.hidden = found;
    if (status) {
      status.textContent = found
        ? results.length + " نتيجة"
        : "لا نتائج مطابقة";
    }
    open();
    highlight(-1);
  }

  function run(query) {
    if (controller) controller.abort();
    controller = new AbortController();

    fetch(endpoint + "?q=" + encodeURIComponent(query), {
      signal: controller.signal,
      credentials: "same-origin",
      headers: { "X-Requested-With": "XMLHttpRequest" }
    })
      .then(function (response) {
        if (!response.ok) throw new Error("search failed: " + response.status);
        return response.json();
      })
      .then(function (payload) {
        render(payload.results || []);
      })
      .catch(function (error) {
        if (error && error.name === "AbortError") return;
        // بحثٌ تعذّر يُقال، ولا يُترك الصندوق صامتاً يوحي بأن لا نتائج.
        if (status) status.textContent = "تعذّر البحث. أعد المحاولة.";
        if (empty) {
          empty.textContent = "تعذّر البحث. أعد المحاولة.";
          empty.hidden = false;
        }
        list.textContent = "";
        options = [];
        open();
      });
  }

  input.addEventListener("input", function () {
    var query = input.value.trim();
    if (clearButton) clearButton.hidden = query.length === 0;

    if (timer) window.clearTimeout(timer);
    if (query.length < minLength) {
      close();
      if (status) status.textContent = "";
      return;
    }
    timer = window.setTimeout(function () {
      run(query);
    }, DEBOUNCE_MS);
  });

  input.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      close();
      return;
    }
    if (panel.hidden || options.length === 0) return;

    if (event.key === "ArrowDown") {
      event.preventDefault();
      highlight((activeIndex + 1) % options.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      highlight(activeIndex <= 0 ? options.length - 1 : activeIndex - 1);
    } else if (event.key === "Enter" && activeIndex >= 0) {
      event.preventDefault();
      var link = options[activeIndex].querySelector("a");
      if (link) link.click();
    }
  });

  if (clearButton) {
    clearButton.addEventListener("click", function () {
      input.value = "";
      clearButton.hidden = true;
      close();
      input.focus();
    });
  }

  document.addEventListener("click", function (event) {
    if (!root.contains(event.target)) close();
  });
})();
