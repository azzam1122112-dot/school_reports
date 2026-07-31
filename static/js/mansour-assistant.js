(function () {
  "use strict";

  var root = document.getElementById("mansourAssistant");
  if (!root) return;

  var launcher = document.getElementById("mansourLauncher");
  var panel = document.getElementById("mansourPanel");
  var closeButton = document.getElementById("mansourClose");
  var conversation = document.getElementById("mansourConversation");
  var form = document.getElementById("mansourForm");
  var input = document.getElementById("mansourInput");
  var sendButton = document.getElementById("mansourSend");
  var audienceStatus = document.getElementById("mansourAudienceStatus");
  var quickActions = root.querySelector(".mansour-quick-actions");
  var roleButtons = root.querySelectorAll("[data-mansour-audience]");
  var endpoint = root.getAttribute("data-endpoint");
  var storageKey = "tawtheeq.mansour.audience";
  var history = [];
  var audience = "";
  var isSending = false;

  var roleContent = {
    teacher: {
      label: "معلم",
      confirmation: "سأشرح لك الآن خطوات المعلم فقط، مثل التقارير وملف الإنجاز والطلبات وما يصلك من تعاميم.",
      placeholder: "اسأل منصور عن مهام المعلم…",
      questions: [
        ["إنشاء تقرير", "كيف أنشئ تقريرًا جديدًا وأضيف الصور؟"],
        ["ملف الإنجاز", "كيف أنشئ ملف إنجاز وأشاركه؟"],
        ["التعاميم", "كيف أقرأ التعميم وأؤكد التوقيع؟"]
      ]
    },
    manager: {
      label: "مدير مدرسة",
      confirmation: "سأشرح لك رحلة مدير المدرسة، من إدارة الفريق إلى التعاميم والاشتراك والأرشيف والتخزين.",
      placeholder: "اسأل منصور عن إدارة المدرسة…",
      questions: [
        ["اختيار المستلمين", "كيف أختار معلمين أو قسمًا أو عدة أقسام عند إرسال تعميم؟"],
        ["الأرشيف", "ماذا يشمل أرشيف المدرسة وكيف أنشئ نسخة؟"],
        ["مساحة التخزين", "كيف تُحسب مساحة المدرسة وكيف أزيدها؟"]
      ]
    },
    supervisor: {
      label: "مشرف",
      confirmation: "سأفرّق في إجاباتي بين مشرف التقارير داخل المدرسة ومشرف المنصة ذي نطاق المدارس.",
      placeholder: "اسأل منصور عن مهام المشرف…",
      questions: [
        ["نوع المشرف", "ما الفرق بين مشرف التقارير ومشرف المنصة؟"],
        ["نطاق المدارس", "كيف يصل مشرف المنصة إلى المدارس الواقعة ضمن نطاقه؟"],
        ["الصلاحيات", "ما الذي يستطيع المشرف عرضه أو تنفيذه؟"]
      ]
    }
  };

  function setOpen(open) {
    if (!panel || !launcher) return;
    panel.hidden = !open;
    launcher.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      window.setTimeout(function () {
        var selectedRole = root.querySelector("[data-mansour-audience][aria-pressed='true']");
        if (!audience && roleButtons.length) {
          roleButtons[0].focus();
        } else if (selectedRole && !input) {
          selectedRole.focus();
        } else if (input) {
          input.focus();
        }
      }, 40);
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

  function addSources(sources) {
    if (!conversation || !Array.isArray(sources) || !sources.length) return;
    var wrap = document.createElement("div");
    wrap.className = "mansour-sources";
    wrap.setAttribute("aria-label", "مصادر الإجابة");

    sources.slice(0, 4).forEach(function (source) {
      if (!source || typeof source.url !== "string" || source.url.charAt(0) !== "/") return;
      var link = document.createElement("a");
      link.href = source.url;
      link.textContent = source.title || "اعرف المزيد";
      wrap.appendChild(link);
    });

    if (wrap.childNodes.length) {
      conversation.appendChild(wrap);
      scrollToLatest();
    }
  }

  function setSending(sending) {
    isSending = sending;
    if (sendButton) sendButton.disabled = sending || !audience;
    if (input) input.disabled = sending || !audience;
    if (conversation) conversation.setAttribute("aria-busy", sending ? "true" : "false");
    Array.prototype.forEach.call(roleButtons, function (button) {
      button.disabled = sending;
    });
  }

  function renderQuickActions() {
    if (!quickActions) return;
    quickActions.textContent = "";
    var content = roleContent[audience];
    if (!content) return;

    content.questions.forEach(function (item) {
      var button = document.createElement("button");
      button.className = "mansour-quick-action";
      button.type = "button";
      button.setAttribute("data-mansour-question", item[1]);
      button.textContent = item[0];
      quickActions.appendChild(button);
    });
  }

  function persistAudience(value) {
    try {
      window.localStorage.setItem(storageKey, value);
    } catch (_error) {
      // The assistant remains fully usable when browser storage is unavailable.
    }
  }

  function storedAudience() {
    try {
      var value = window.localStorage.getItem(storageKey);
      return roleContent[value] ? value : "";
    } catch (_error) {
      return "";
    }
  }

  function setAudience(value, options) {
    options = options || {};
    if (!roleContent[value] || isSending) return;

    var changed = audience && audience !== value;
    audience = value;
    history = [];
    root.setAttribute("data-audience", value);
    persistAudience(value);

    Array.prototype.forEach.call(roleButtons, function (button) {
      var isSelected = button.getAttribute("data-mansour-audience") === value;
      button.setAttribute("aria-pressed", isSelected ? "true" : "false");
      button.classList.toggle("is-selected", isSelected);
    });

    if (audienceStatus) {
      audienceStatus.textContent = "الدور الحالي: " + roleContent[value].label;
      audienceStatus.classList.add("is-selected");
    }
    if (input) input.placeholder = roleContent[value].placeholder;
    renderQuickActions();
    setSending(false);

    if (options.announce) {
      addMessage(
        (changed ? "تم تغيير الدور. " : "") + roleContent[value].confirmation,
        "assistant",
        "role-confirmation"
      );
    }
    if (options.focus && input) input.focus();
  }

  function requestRoleSelection() {
    addMessage("اختر دورك أولًا حتى أعطيك الخطوات والصلاحيات المناسبة.", "assistant");
    if (roleButtons.length) roleButtons[0].focus();
  }

  function submitQuestion(question) {
    question = String(question || "").trim();
    if (!audience) {
      requestRoleSelection();
      return;
    }
    if (!question || isSending || !endpoint) return;
    if (question.length > 500) {
      addMessage("اختصر استفسارك إلى 500 حرف أو أقل.", "assistant");
      return;
    }

    var historyForRequest = history.slice(-6);
    addMessage(question, "user");
    history.push({ role: "user", content: question });
    if (input) {
      input.value = "";
      input.style.height = "";
    }
    setSending(true);
    var pending = addMessage("لحظة، أراجع المعلومة المناسبة لدورك…", "assistant", "pending");

    fetch(endpoint, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        question: question,
        history: historyForRequest,
        audience: audience
      })
    })
      .then(function (response) {
        return response.json().catch(function () {
          return { ok: false, message: "تعذر الوصول إلى المساعد الآن." };
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
        addMessage(error.message || "تعذر الوصول إلى المساعد الآن. حاول لاحقًا.", "assistant");
      })
      .then(function () {
        setSending(false);
        if (input) input.focus();
      });
  }

  if (launcher) launcher.addEventListener("click", function () { setOpen(true); });
  if (closeButton) closeButton.addEventListener("click", function () { setOpen(false); });

  Array.prototype.forEach.call(roleButtons, function (button) {
    button.addEventListener("click", function () {
      setAudience(button.getAttribute("data-mansour-audience"), {
        announce: true,
        focus: true
      });
    });
  });

  if (quickActions) {
    quickActions.addEventListener("click", function (event) {
      var button = event.target.closest("[data-mansour-question]");
      if (!button || !quickActions.contains(button)) return;
      submitQuestion(button.getAttribute("data-mansour-question"));
    });
  }

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
    });
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && panel && !panel.hidden) setOpen(false);
  });

  var previousAudience = storedAudience();
  if (previousAudience) {
    setAudience(previousAudience, { announce: false, focus: false });
  } else {
    setSending(false);
  }
}());
