(function () {
  "use strict";

  const form = document.querySelector("[data-group-assignment-form]");
  if (!form) return;

  const title = form.querySelector('[name="title"]');
  const priority = form.querySelector('[name="priority"]');
  const due = form.querySelector('[name="due_at"]');
  const evidence = form.querySelector('[name="requires_evidence"]');
  const evidenceCount = form.querySelector('[name="min_evidence_count"]');
  const schools = Array.from(form.querySelectorAll('[name="schools"]'));
  const submitButton = form.querySelector('[type="submit"]');

  const output = {
    title: form.querySelector("[data-summary-title]"),
    priority: form.querySelector("[data-summary-priority]"),
    due: form.querySelector("[data-summary-due]"),
    evidence: form.querySelector("[data-summary-evidence]"),
    schools: form.querySelector("[data-summary-schools]")
  };

  function selectedText(select) {
    if (!select || select.selectedIndex < 0) return "—";
    return select.options[select.selectedIndex].textContent.trim();
  }

  function formatDue(value) {
    if (!value) return "لم يحدد";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return value;
    return new Intl.DateTimeFormat("ar-SA", {
      dateStyle: "medium",
      timeStyle: "short"
    }).format(parsed);
  }

  function updateSummary() {
    const chosen = schools.filter(function (item) { return item.checked; }).length;
    if (output.title) output.title.textContent = title && title.value.trim() ? title.value.trim() : "لم يُكتب بعد";
    if (output.priority) output.priority.textContent = selectedText(priority);
    if (output.due) output.due.textContent = formatDue(due ? due.value : "");
    if (output.schools) output.schools.textContent = chosen + " من " + schools.length;
    if (output.evidence) {
      output.evidence.textContent = evidence && evidence.checked
        ? "مطلوبة · " + ((evidenceCount && evidenceCount.value) || "1")
        : "غير مطلوبة";
    }
    if (evidenceCount) evidenceCount.disabled = !(evidence && evidence.checked);
    if (submitButton) {
      submitButton.setAttribute(
        "data-confirm",
        "سيصدر التكليف إلى " + chosen + " مدرسة. هل راجعت المطلوب والموعد والشواهد؟"
      );
      submitButton.setAttribute("data-confirm-title", "تأكيد إصدار التكليف");
      submitButton.setAttribute("data-confirm-ok", "نعم، إصدار التكليف");
    }
  }

  [title, priority, due, evidence, evidenceCount].concat(schools).forEach(function (control) {
    if (!control) return;
    control.addEventListener("input", updateSummary);
    control.addEventListener("change", updateSummary);
  });

  updateSummary();
})();
