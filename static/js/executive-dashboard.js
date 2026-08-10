(function () {
  "use strict";

  const rows = Array.from(document.querySelectorAll("[data-school-row]"));
  if (!rows.length) return;

  const search = document.querySelector("[data-school-search]");
  const filters = Array.from(document.querySelectorAll("[data-risk-filter]"));
  const empty = document.querySelector("[data-school-empty]");
  const live = document.querySelector("[data-school-count]");
  let risk = "all";

  function normalize(value) {
    return (value || "")
      .toLocaleLowerCase("ar")
      .replace(/[أإآ]/g, "ا")
      .replace(/ة/g, "ه")
      .trim();
  }

  function applyFilters() {
    const query = normalize(search ? search.value : "");
    let shown = 0;

    rows.forEach(function (row) {
      const matchesRisk = risk === "all" || row.dataset.risk === risk;
      const matchesSearch = !query || normalize(row.dataset.search).includes(query);
      const visible = matchesRisk && matchesSearch;
      row.hidden = !visible;
      if (visible) shown += 1;
    });

    if (empty) empty.hidden = shown !== 0;
    if (live) live.textContent = "عدد المدارس الظاهرة: " + shown;
  }

  if (search) search.addEventListener("input", applyFilters);
  filters.forEach(function (button) {
    button.addEventListener("click", function () {
      risk = button.dataset.riskFilter || "all";
      filters.forEach(function (item) {
        const active = item === button;
        item.classList.toggle("is-active", active);
        item.setAttribute("aria-pressed", active ? "true" : "false");
      });
      applyFilters();
    });
  });

  applyFilters();
})();
