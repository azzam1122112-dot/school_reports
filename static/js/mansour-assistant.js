(function () {
  "use strict";

  var root = document.getElementById("mansourAssistant");
  if (!root) return;

  var launcher = document.getElementById("mansourLauncher");
  var panel = document.getElementById("mansourPanel");
  var closeButton = document.getElementById("mansourClose");
  var resetButton = document.getElementById("mansourReset");
  var conversation = document.getElementById("mansourConversation");
  var quickActions = document.getElementById("mansourQuickActions");
  var form = document.getElementById("mansourForm");
  var input = document.getElementById("mansourInput");
  var sendButton = document.getElementById("mansourSend");
  var charCount = document.getElementById("mansourCharCount");
  var endpoint = root.getAttribute("data-endpoint");
  var audience = root.getAttribute("data-audience") || "";
  var isInternal = root.getAttribute("data-internal") === "true";
  var roleLabel = root.getAttribute("data-role-label") || "";
  var history = [];
  var isSending = false;

  // ── بقاء المحادثة عبر التنقّل ───────────────────────────────────────────
  // كان السجل مصفوفةً في الذاكرة، فأي انتقال يمسحه. وأثره أبعد من إزعاج
  // المستخدم: ميزةُ الأسئلة المتتابعة («وين ألقاها؟» تُحلّ من السؤال السابق)
  // لم تكن تعمل عبر الصفحات أصلاً لأن السجل لا يصل إلى الطلب التالي.
  //
  // ``sessionStorage`` لا ``localStorage``: المحادثة تخصّ هذا التبويب وتنتهي
  // بإغلاقه. والمفتاح يحمل بصمة رمز CSRF لأن جانغو يدوّره عند تسجيل الدخول
  // والخروج — فمحادثة مستخدمٍ سابق على الجهاز نفسه لا تُستعاد لمن بعده.
  var MAX_STORED_ENTRIES = 24;
  var transcript = [];

  // الخادم يمرّر بصمة الجلسة. ولا تصلح ``{% csrf_token %}`` لهذا: جانغو
  // يقنّعها بقناعٍ عشوائي في كل طلب، فتتغيّر البصمة مع كل صفحة ولا تُستعاد
  // محادثة قط — وهذا ما ظهر فعلاً عند أول قياس.
  var chatScope = root.getAttribute("data-chat-scope") || "anon";
  var storageKey = "mansour:chat:v1:" + (isInternal ? "internal" : "public") + ":" + chatScope;

  // محادثات الجلسات السابقة على الجهاز نفسه لا تُستعاد، ولا تبقى مخزّنة.
  function dropForeignConversations() {
    try {
      var stale = [];
      for (var i = 0; i < window.sessionStorage.length; i++) {
        var key = window.sessionStorage.key(i);
        if (key && key.indexOf("mansour:chat:") === 0 && key !== storageKey) stale.push(key);
      }
      stale.forEach(function (key) { window.sessionStorage.removeItem(key); });
    } catch (error) { /* التخزين محظور: لا شيء يُنظَّف ولا شيء يُكسر */ }
  }

  function persistConversation() {
    try {
      transcript = transcript.slice(-MAX_STORED_ENTRIES);
      window.sessionStorage.setItem(storageKey, JSON.stringify({
        history: history.slice(-6),
        transcript: transcript
      }));
    } catch (error) {
      // حصّة التخزين ممتلئة أو محظورة: المحادثة تعمل بلا حفظ، ولا شيء يُكسر.
    }
  }

  function forgetConversation() {
    try {
      window.sessionStorage.removeItem(storageKey);
    } catch (error) { /* لا شيء */ }
  }

  function record(role, text, extraClass, sources) {
    transcript.push({
      role: role,
      text: text,
      cls: extraClass || "",
      sources: Array.isArray(sources) ? sources : null
    });
    persistConversation();
  }
  var welcomeMessage = isInternal
    ? "أهلًا بك، أنا منصور. أشرح لك الصفحة الحالية وأساعدك في إنجاز عملك داخل منصة توثيق وفق صلاحياتك" + (roleLabel ? " كـ" + roleLabel : "") + "."
    : "أهلًا بك، أنا منصور. أساعدك في فهم منصة توثيق، اختيار البداية المناسبة، وحل المشكلات بخطوات واضحة وآمنة.";
  var suggestedQuestions = isInternal
    ? ["اشرح لي هذه الصفحة", "كيف أنجز عملي هنا؟", "ما الخطوة التالية المناسبة لدوري؟"]
    : ["ما هي منصة توثيق؟", "كيف أبدأ التجربة؟", "ما الباقات المتاحة؟"];

  function setOpen(open) {
    if (!panel || !launcher) return;
    panel.hidden = !open;
    launcher.setAttribute("aria-expanded", open ? "true" : "false");
    root.classList.toggle("is-open", open);
    if (open) {
      window.setTimeout(function () {
        if (input) input.focus();
      }, 60);
    } else {
      launcher.focus();
    }
  }

  function scrollToLatest() {
    if (conversation) conversation.scrollTop = conversation.scrollHeight;
  }

  function addMessage(text, role, extraClass) {
    if (!conversation) return null;
    var message = document.createElement("p");
    message.className = "mansour-message " + role + (extraClass ? " " + extraClass : "");
    message.textContent = text;
    conversation.appendChild(message);
    scrollToLatest();
    return message;
  }

  function clearConversation(message) {
    if (!conversation) return;
    while (conversation.firstChild) conversation.removeChild(conversation.firstChild);
    addMessage(message, "assistant");
  }

  function isTrustedSource(url) {
    if (url.origin === window.location.origin) return true;
    if (url.protocol !== "https:") return false;
    return /(^|\.)tawtheeq-ksa\.com$/i.test(url.hostname);
  }

  // رابط يشير إلى الصفحة المفتوحة نفسها ولا يختلف إلا بالمرساة: التنقّل إليه
  // إعادةُ تحميلٍ بلا داعٍ، والصواب تمريرٌ في المكان.
  function isSamePageAnchor(url) {
    return url.origin === window.location.origin
      && url.pathname === window.location.pathname
      && Boolean(url.hash);
  }

  function addSources(sources) {
    if (!conversation || !Array.isArray(sources) || !sources.length) return;
    var container = document.createElement("div");
    var label = document.createElement("span");
    container.className = "mansour-sources";
    label.className = "mansour-sources-label";
    label.textContent = "روابط مفيدة";
    container.appendChild(label);

    sources.slice(0, 3).forEach(function (source) {
      if (!source || !source.title || !source.url) return;
      try {
        var url = new URL(source.url, window.location.origin);
        if (!isTrustedSource(url)) return;
        var link = document.createElement("a");
        link.href = url.origin === window.location.origin
          ? url.pathname + url.search + url.hash
          : url.href;
        link.textContent = source.title;

        if (isSamePageAnchor(url)) {
          // نفس الصفحة: أغلق اللوحة ومرّر إلى الموضع بدل إعادة التحميل.
          link.addEventListener("click", function (event) {
            var target = document.getElementById(url.hash.slice(1));
            if (!target) return;
            event.preventDefault();
            setOpen(false);
            target.scrollIntoView({ behavior: "smooth", block: "start" });
          });
        } else {
          // تبويب جديد: المستخدم قد يكون في منتصف نموذج لم يُحفظ، والمحادثة
          // نفسها تُفقد مع التنقّل. فتحُ الرابط جانباً يبقي عمله وسياقه سليمين.
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          link.setAttribute("aria-label", source.title + " (يفتح في تبويب جديد)");
          var hint = document.createElement("span");
          hint.className = "mansour-source-hint";
          hint.setAttribute("aria-hidden", "true");
          hint.textContent = "↗";
          link.appendChild(hint);
        }
        container.appendChild(link);
      } catch (error) {
        return;
      }
    });
    if (container.children.length > 1) {
      conversation.appendChild(container);
      scrollToLatest();
    }
  }

  function addRetry(question) {
    if (!conversation) return;
    var retry = document.createElement("button");
    retry.className = "mansour-retry";
    retry.type = "button";
    retry.textContent = "إعادة المحاولة";
    retry.addEventListener("click", function () {
      retry.remove();
      submitQuestion(question);
    });
    conversation.appendChild(retry);
    scrollToLatest();
  }

  function renderQuickActions() {
    if (!quickActions) return;
    while (quickActions.firstChild) quickActions.removeChild(quickActions.firstChild);
    suggestedQuestions.forEach(function (question) {
      var button = document.createElement("button");
      button.className = "mansour-quick-action";
      button.type = "button";
      button.textContent = question;
      button.disabled = isSending;
      button.addEventListener("click", function () { submitQuestion(question); });
      quickActions.appendChild(button);
    });
  }

  function resetConversation() {
    history = [];
    transcript = [];
    forgetConversation();
    audience = root.getAttribute("data-audience") || "";
    clearConversation(welcomeMessage);
    renderQuickActions();
    if (input) {
      input.value = "";
      input.style.height = "";
      updateCharCount();
      input.focus();
    }
  }

  function setSending(sending) {
    isSending = sending;
    if (sendButton) sendButton.disabled = sending;
    if (input) input.disabled = sending;
    if (resetButton) resetButton.disabled = sending;
    if (quickActions) {
      Array.prototype.forEach.call(quickActions.querySelectorAll("button"), function (button) {
        button.disabled = sending;
      });
    }
    if (conversation) conversation.setAttribute("aria-busy", sending ? "true" : "false");
  }

  function updateCharCount() {
    if (!charCount || !input) return;
    charCount.textContent = input.value.length + "/500";
    charCount.classList.toggle("is-near-limit", input.value.length >= 450);
  }

  function submitQuestion(question) {
    question = String(question || "").trim();
    if (!question || isSending || !endpoint) return;
    if (question.length > 500) {
      addMessage("اختصر استفسارك إلى 500 حرف أو أقل.", "assistant", "error");
      return;
    }

    var historyForRequest = history.slice(-6);
    addMessage(question, "user");
    history.push({ role: "user", content: question });
    record("user", question);
    if (input) {
      input.value = "";
      input.style.height = "";
      updateCharCount();
    }
    setSending(true);
    var pending = addMessage("أراجع المعلومة الأنسب لسؤالك…", "assistant", "pending");
    var controller = typeof window.AbortController === "function" ? new window.AbortController() : null;
    var timeout = window.setTimeout(function () {
      if (controller) controller.abort();
    }, 25000);

    fetch(endpoint, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      signal: controller ? controller.signal : undefined,
      body: JSON.stringify({
        question: question,
        history: historyForRequest,
        audience: audience,
        page_context: isInternal ? {
          title: document.title || "",
          path: window.location.pathname || ""
        } : null
      })
    })
      .then(function (response) {
        return response.json().catch(function () {
          return { ok: false, message: "تعذر قراءة رد المساعد الآن." };
        }).then(function (data) {
          if (!response.ok || !data.ok) {
            throw new Error(data.message || "تعذر الوصول إلى المساعد الآن.");
          }
          return data;
        });
      })
      .then(function (data) {
        if (pending) pending.remove();
        addMessage(data.answer, "assistant");
        addSources(data.sources);
        history.push({ role: "assistant", content: data.answer });
        history = history.slice(-6);
        record("assistant", data.answer, "", data.sources);
      })
      .catch(function (error) {
        if (pending) pending.remove();
        var message = error && error.name === "AbortError"
          ? "استغرق الرد وقتًا أطول من المتوقع. تحقق من الاتصال ثم أعد المحاولة."
          : (error.message || "تعذر الوصول إلى المساعد الآن. حاول لاحقًا.");
        addMessage(message, "assistant", "error");
        addRetry(question);
      })
      .then(function () {
        window.clearTimeout(timeout);
        setSending(false);
        if (input) input.focus();
      });
  }

  if (launcher) launcher.addEventListener("click", function () { setOpen(true); });
  if (closeButton) closeButton.addEventListener("click", function () { setOpen(false); });
  if (resetButton) resetButton.addEventListener("click", resetConversation);

  if (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      submitQuestion(input ? input.value : "");
    });
  }

  if (input) {
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        submitQuestion(input.value);
      }
    });
    input.addEventListener("input", function () {
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 110) + "px";
      updateCharCount();
    });
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && panel && !panel.hidden) setOpen(false);
  });

  function restoreConversation() {
    var saved;
    try {
      saved = JSON.parse(window.sessionStorage.getItem(storageKey) || "null");
    } catch (error) {
      return false;
    }
    if (!saved || !Array.isArray(saved.transcript) || !saved.transcript.length) return false;

    history = Array.isArray(saved.history) ? saved.history.slice(-6) : [];
    transcript = saved.transcript.slice(-MAX_STORED_ENTRIES);
    if (!conversation) return false;
    while (conversation.firstChild) conversation.removeChild(conversation.firstChild);
    addMessage(welcomeMessage, "assistant");
    transcript.forEach(function (entry) {
      if (!entry || !entry.text) return;
      // ``pending`` حالةٌ لحظية لا معنى لاستعادتها، و``error`` رسالة عن طلبٍ
      // انتهى — كلاهما يُسقط عند الاستعادة.
      if (entry.cls === "pending" || entry.cls === "error") return;
      addMessage(entry.text, entry.role === "user" ? "user" : "assistant");
      if (entry.role === "assistant" && entry.sources) addSources(entry.sources);
    });
    return true;
  }

  renderQuickActions();
  updateCharCount();
  setSending(false);
  dropForeignConversations();
  restoreConversation();
}());
