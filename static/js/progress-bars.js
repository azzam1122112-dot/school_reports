/* static/js/progress-bars.js
   ─────────────────────────────────────────────────────────────────────────
   يضبط عرض أشرطة التقدّم من سمة ``data-progress``.

   **لماذا لا تُكتب النسبة في ``style`` مباشرة.** لأن سمة ``style`` واحدة في أي
   قالب تُلزم سياسة المحتوى بحمل ``style-src 'unsafe-inline'`` — وهو الإذن الذي
   يجعل أي حقن HTML قادراً على حقن أنماط. وأشرطة التقدّم كانت آخر ما يمنع
   إغلاقه، لأن قيمتها وحدها تأتي من قاعدة البيانات لا من ورقة أنماط.

   وضبطُ النمط من الجافاسكربت (CSSOM) **لا يحكمه** ``style-src``: الموصوف في
   المواصفة هو سمةُ ``style`` وعنصرُ ``<style>``، لا ``element.style.x``. فالنسبة
   تصل كما كانت، والإذن يُغلق.

   القيمة تُقصّ إلى [0, 100]: رقمٌ فاسد في القاعدة يجب أن يُنتج شريطاً ممتلئاً
   أو فارغاً، لا شريطاً يتجاوز حاويته فيكسر التخطيط.
   ───────────────────────────────────────────────────────────────────────── */
(function () {
  "use strict";

  function clamp(value) {
    var raw = parseFloat(value);
    return isNaN(raw) ? 0 : Math.max(0, Math.min(100, raw));
  }

  function apply(root) {
    var scope = root || document;

    // أشرطة العرض: النسبة تصير عرضاً.
    var bars = scope.querySelectorAll("[data-progress]");
    for (var i = 0; i < bars.length; i++) {
      var bar = bars[i];
      var pct = clamp(bar.getAttribute("data-progress"));
      bar.style.width = pct + "%";
      // قارئ الشاشة يقرأ الرقم، لا عرض الصندوق.
      if (bar.getAttribute("role") === "progressbar") {
        bar.setAttribute("aria-valuenow", String(Math.round(pct)));
      }
    }

    // الحلقات: النسبة تصير خاصيةً مخصّصة تقرؤها ``conic-gradient`` في CSS.
    // اسمُ الخاصية يأتي من القالب لأن المكوّنات القائمة تستعمل أسماءً مختلفة
    // (``--pct`` و``--v``) — وتوحيدُها تغييرٌ بصري مستقل عن إغلاق السياسة.
    var rings = scope.querySelectorAll("[data-progress-var]");
    for (var j = 0; j < rings.length; j++) {
      var ring = rings[j];
      var name = ring.getAttribute("data-progress-var");
      if (!name || name.slice(0, 2) !== "--") continue;
      ring.style.setProperty(name, String(clamp(ring.getAttribute("data-progress-value"))));
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      apply(document);
    });
  } else {
    apply(document);
  }

  // محتوىً يصل بعد التحميل (لوحات تُحدَّث بلا إعادة تحميل) يُعالَج بالنداء نفسه.
  window.applyProgressBars = apply;
})();
