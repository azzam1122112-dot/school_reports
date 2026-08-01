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
  var endpoint = root.getAttribute("data-endpoint");
  var history = [];
  var isSending = false;

  function setOpen(open) {
    if (!panel || !launcher) return;
    panel.hidden = !open;
    launcher.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      window.setTimeout(function () {
        if (input) {
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

  function setSending(sending) {
    isSending = sending;
    if (sendButton) sendButton.disabled = sending;
    if (input) input.disabled = sending;
    if (conversation) conversation.setAttribute("aria-busy", sending ? "true" : "false");
  }

  function submitQuestion(question) {
    question = String(question || "").trim();
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
    var pending = addMessage("لحظة، أراجع المعلومة المناسبة لك…", "assistant", "pending");

    fetch(endpoint, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        question: question,
        history: historyForRequest
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

  setSending(false);
}());
