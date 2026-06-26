/* hijri-datepicker.js
 * منتقي تاريخ هجري موحّد (تقويم أم القرى) — بدون مكتبات خارجية.
 *
 * يعمل كطبقة تحسين فوق حقول <input type="date">:
 *  - يبقى الحقل مخزّنًا بالتاريخ الميلادي (YYYY-MM-DD) فلا يتأثر الخادم.
 *  - يعرض المقابل الهجري بشكل حيّ ويتيح الاختيار من تقويم هجري.
 *
 * الاستثناء: أضف data-no-hijri على أي حقل لا تريد تحسينه.
 */
(function () {
  "use strict";

  // التحقق من دعم تقويم أم القرى في المتصفح
  function hijriSupported() {
    try {
      var s = new Intl.DateTimeFormat("en-u-ca-islamic-umalqura", { day: "numeric" }).format(new Date());
      return !!s;
    } catch (e) {
      return false;
    }
  }
  if (!hijriSupported()) return;

  var WEEKDAYS = ["الأحد", "الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت"];
  var HIJRI_MONTHS = [
    "محرم", "صفر", "ربيع الأول", "ربيع الآخر", "جمادى الأولى", "جمادى الآخرة",
    "رجب", "شعبان", "رمضان", "شوال", "ذو القعدة", "ذو الحجة"
  ];

  var _partsFmt = new Intl.DateTimeFormat("en-u-ca-islamic-umalqura-nu-latn", {
    day: "numeric", month: "numeric", year: "numeric"
  });

  function hijriParts(date) {
    var out = { y: 0, m: 1, d: 1 };
    _partsFmt.formatToParts(date).forEach(function (p) {
      if (p.type === "day") out.d = parseInt(p.value, 10);
      else if (p.type === "month") out.m = parseInt(p.value, 10);
      else if (p.type === "year") out.y = parseInt(p.value, 10);
    });
    return out;
  }

  function toArabicDigits(n) {
    var map = "٠١٢٣٤٥٦٧٨٩";
    return String(n).replace(/[0-9]/g, function (d) { return map[+d]; });
  }

  function pad(n) { return n < 10 ? "0" + n : "" + n; }
  function toISO(date) { return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate()); }
  function parseISO(v) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v || "");
    if (!m) return null;
    var d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
    d.setHours(0, 0, 0, 0);
    return isNaN(d.getTime()) ? null : d;
  }
  function addDays(date, n) {
    var d = new Date(date.getTime());
    d.setDate(d.getDate() + n);
    d.setHours(0, 0, 0, 0);
    return d;
  }
  function sameDay(a, b) {
    return a && b && a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  }

  function hijriLabel(date) {
    var h = hijriParts(date);
    var wd = WEEKDAYS[date.getDay()];
    return wd + " " + toArabicDigits(h.d) + " " + HIJRI_MONTHS[h.m - 1] + " " + toArabicDigits(h.y) + " هـ";
  }

  // أول يوم ميلادي للشهر الهجري الذي يقع فيه التاريخ المعطى
  function hijriMonthStart(date) {
    var h = hijriParts(date);
    var start = addDays(date, -(h.d - 1));
    // ضبط دقيق: تأكد أننا في اليوم الأول
    for (var i = 0; i < 3; i++) {
      if (hijriParts(start).d === 1) break;
      start = addDays(start, hijriParts(start).d === 1 ? 0 : -(hijriParts(start).d - 1));
    }
    return start;
  }

  function buildPopover(input) {
    var pop = document.createElement("div");
    pop.className = "hijri-pop";
    pop.setAttribute("dir", "rtl");
    pop.innerHTML =
      '<div class="hijri-pop-head">' +
      '  <button type="button" class="hijri-nav" data-dir="prev" aria-label="الشهر السابق"><i class="fa-solid fa-chevron-right"></i></button>' +
      '  <span class="hijri-title"></span>' +
      '  <button type="button" class="hijri-nav" data-dir="next" aria-label="الشهر التالي"><i class="fa-solid fa-chevron-left"></i></button>' +
      '</div>' +
      '<div class="hijri-grid hijri-week"></div>' +
      '<div class="hijri-grid hijri-days"></div>' +
      '<div class="hijri-pop-foot">' +
      '  <button type="button" class="hijri-today">اليوم</button>' +
      '  <button type="button" class="hijri-clear">مسح</button>' +
      '</div>';

    var title = pop.querySelector(".hijri-title");
    var week = pop.querySelector(".hijri-week");
    var daysWrap = pop.querySelector(".hijri-days");
    WEEKDAYS.forEach(function (w) {
      var c = document.createElement("span");
      c.className = "hijri-wd";
      c.textContent = w.slice(0, 3);
      week.appendChild(c);
    });

    var cursor = parseISO(input.value) || new Date();
    cursor.setHours(0, 0, 0, 0);

    function render() {
      daysWrap.innerHTML = "";
      var start = hijriMonthStart(cursor);
      var hStart = hijriParts(start);
      title.textContent = HIJRI_MONTHS[hStart.m - 1] + " " + toArabicDigits(hStart.y) + " هـ";

      var today = new Date(); today.setHours(0, 0, 0, 0);
      var selected = parseISO(input.value);

      // خلايا فارغة قبل اليوم الأول حسب يوم الأسبوع (الأحد=0)
      var offset = start.getDay();
      for (var i = 0; i < offset; i++) {
        var blank = document.createElement("span");
        blank.className = "hijri-day is-blank";
        daysWrap.appendChild(blank);
      }

      var greg = start;
      var monthIndex = hStart.m;
      while (hijriParts(greg).m === monthIndex && hijriParts(greg).y === hStart.y) {
        (function (dayGreg) {
          var hp = hijriParts(dayGreg);
          var cell = document.createElement("button");
          cell.type = "button";
          cell.className = "hijri-day";
          cell.textContent = toArabicDigits(hp.d);
          cell.title = hijriLabel(dayGreg);
          if (sameDay(dayGreg, today)) cell.classList.add("is-today");
          if (sameDay(dayGreg, selected)) cell.classList.add("is-selected");
          cell.addEventListener("click", function () {
            input.value = toISO(dayGreg);
            input.dispatchEvent(new Event("input", { bubbles: true }));
            input.dispatchEvent(new Event("change", { bubbles: true }));
            close();
          });
          daysWrap.appendChild(cell);
        })(greg);
        greg = addDays(greg, 1);
      }
    }

    pop.querySelectorAll(".hijri-nav").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var start = hijriMonthStart(cursor);
        if (btn.getAttribute("data-dir") === "prev") {
          cursor = hijriMonthStart(addDays(start, -1));
        } else {
          var greg = start, m = hijriParts(start).m, y = hijriParts(start).y;
          while (hijriParts(greg).m === m && hijriParts(greg).y === y) { greg = addDays(greg, 1); }
          cursor = greg;
        }
        render();
      });
    });
    pop.querySelector(".hijri-today").addEventListener("click", function () {
      var t = new Date(); t.setHours(0, 0, 0, 0);
      input.value = toISO(t);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      cursor = t; render(); close();
    });
    pop.querySelector(".hijri-clear").addEventListener("click", function () {
      input.value = "";
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      close();
    });

    function reposition() { /* CSS handles positioning relative to wrapper */ }
    function close() {
      pop.classList.remove("open");
      document.removeEventListener("click", onDocClick, true);
      document.removeEventListener("keydown", onKey, true);
    }
    function open() {
      cursor = parseISO(input.value) || new Date();
      cursor.setHours(0, 0, 0, 0);
      render();
      pop.classList.add("open");
      reposition();
      document.addEventListener("click", onDocClick, true);
      document.addEventListener("keydown", onKey, true);
    }
    function onDocClick(e) {
      if (!pop.contains(e.target) && !e.target.classList.contains("hijri-trigger")) {
        if (!pop.parentNode || !pop.parentNode.contains(e.target)) close();
      }
    }
    function onKey(e) { if (e.key === "Escape") close(); }

    pop._open = open;
    pop._close = close;
    pop._isOpen = function () { return pop.classList.contains("open"); };
    return pop;
  }

  function enhance(input) {
    if (input._hijriEnhanced) return;
    input._hijriEnhanced = true;

    var wrap = document.createElement("span");
    wrap.className = "hijri-wrap";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);

    var trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "hijri-trigger";
    trigger.setAttribute("aria-label", "اختيار تاريخ هجري");
    trigger.innerHTML = '<span class="hijri-trigger-txt">هـ</span>';
    wrap.appendChild(trigger);

    var readout = document.createElement("span");
    readout.className = "hijri-readout";
    wrap.appendChild(readout);

    var pop = buildPopover(input);
    wrap.appendChild(pop);

    function updateReadout() {
      var d = parseISO(input.value);
      readout.textContent = d ? hijriLabel(d) : "";
      readout.classList.toggle("is-empty", !d);
    }
    input.addEventListener("input", updateReadout);
    input.addEventListener("change", updateReadout);
    updateReadout();

    trigger.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      if (pop._isOpen()) { pop._close(); } else { pop._open(); }
    });
  }

  function init(root) {
    var scope = root || document;
    var inputs = scope.querySelectorAll('input[type="date"]:not([data-no-hijri])');
    Array.prototype.forEach.call(inputs, enhance);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { init(document); });
  } else {
    init(document);
  }

  // إتاحة إعادة التهيئة لمحتوى يُحمّل ديناميكيًا
  window.HijriDatePicker = { init: init, label: hijriLabel };
})();
