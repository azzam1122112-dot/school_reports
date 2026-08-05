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
        if (url.origin !== window.location.origin) {
          link.target = "_blank";
          link.rel = "noopener noreferrer";
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

  renderQuickActions();
  updateCharCount();
  setSending(false);
}());
