(() => {
  "use strict";

  const topics = Array.from(document.querySelectorAll(".guide-topic"));
  const roleButtons = Array.from(document.querySelectorAll("[data-guide-filter]"));
  const indexGroups = Array.from(document.querySelectorAll("[data-index-role]"));
  const indexLinks = Array.from(document.querySelectorAll(".guide-index a"));
  const searchForm = document.getElementById("guideSearchForm");
  const searchInput = document.getElementById("guideSearch");
  const searchClear = document.getElementById("guideSearchClear");
  const searchStatus = document.getElementById("guideSearchStatus");
  const emptyState = document.getElementById("guideResultsEmpty");
  const emptyReset = document.getElementById("guideEmptyReset");
  const backToTop = document.getElementById("guideBackToTop");
  const guideMenuToggle = document.getElementById("guideMenuToggle");
  const guideSidebar = document.getElementById("guideSidebar");
  const guideDrawerClose = document.getElementById("guideDrawerClose");
  const guideDrawerOverlay = document.getElementById("guideDrawerOverlay");
  const guideMobileMedia = window.matchMedia("(max-width: 820px)");

  let guideDrawerOpen = false;

  const drawerFocusable = () => {
    if (!guideSidebar) return [];
    return Array.from(
      guideSidebar.querySelectorAll('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])')
    ).filter((element) => element.offsetParent !== null);
  };

  const closeGuideDrawer = (returnFocus = false) => {
    if (!guideSidebar || !guideMenuToggle || !guideDrawerOverlay) return;
    guideDrawerOpen = false;
    guideSidebar.classList.remove("is-open");
    guideDrawerOverlay.classList.remove("is-visible");
    guideDrawerOverlay.setAttribute("aria-hidden", "true");
    guideMenuToggle.setAttribute("aria-expanded", "false");
    document.body.classList.remove("guide-drawer-open");

    if (guideMobileMedia.matches) {
      guideSidebar.setAttribute("aria-hidden", "true");
      guideSidebar.setAttribute("inert", "");
    } else {
      guideSidebar.setAttribute("aria-hidden", "false");
      guideSidebar.removeAttribute("inert");
    }

    if (returnFocus) guideMenuToggle.focus();
  };

  const openGuideDrawer = () => {
    if (
      !guideMobileMedia.matches ||
      !guideSidebar ||
      !guideMenuToggle ||
      !guideDrawerOverlay
    ) return;

    guideDrawerOpen = true;
    guideSidebar.removeAttribute("inert");
    guideSidebar.setAttribute("aria-hidden", "false");
    guideSidebar.classList.add("is-open");
    guideDrawerOverlay.classList.add("is-visible");
    guideDrawerOverlay.setAttribute("aria-hidden", "false");
    guideMenuToggle.setAttribute("aria-expanded", "true");
    document.body.classList.add("guide-drawer-open");

    window.requestAnimationFrame(() => {
      const focusTarget = guideDrawerClose || drawerFocusable()[0];
      if (focusTarget) focusTarget.focus();
    });
  };

  const syncGuideDrawerMode = () => closeGuideDrawer(false);

  if (guideMenuToggle && guideSidebar && guideDrawerOverlay) {
    guideMenuToggle.addEventListener("click", () => {
      guideDrawerOpen ? closeGuideDrawer(true) : openGuideDrawer();
    });
    if (guideDrawerClose) {
      guideDrawerClose.addEventListener("click", () => closeGuideDrawer(true));
    }
    guideDrawerOverlay.addEventListener("click", () => closeGuideDrawer(true));
    guideSidebar.addEventListener("click", (event) => {
      if (event.target.closest("a[href]") && guideMobileMedia.matches) {
        closeGuideDrawer(false);
      }
    });

    document.addEventListener("keydown", (event) => {
      if (!guideDrawerOpen) return;
      if (event.key === "Escape") {
        event.preventDefault();
        closeGuideDrawer(true);
        return;
      }
      if (event.key !== "Tab") return;

      const focusable = drawerFocusable();
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });

    if (typeof guideMobileMedia.addEventListener === "function") {
      guideMobileMedia.addEventListener("change", syncGuideDrawerMode);
    } else {
      guideMobileMedia.addListener(syncGuideDrawerMode);
    }
    syncGuideDrawerMode();
  }

  if (!topics.length) return;

  let activeRole = "all";

  const normalize = (value) => String(value || "")
    .toLocaleLowerCase("ar")
    .replace(/[ًٌٍَُِّْـ]/g, "")
    .replace(/[أإآ]/g, "ا")
    .replace(/ة/g, "ه")
    .replace(/ى/g, "ي")
    .trim();

  topics.forEach((topic) => {
    topic.dataset.searchText = normalize(
      `${topic.dataset.guideTitle || ""} ${topic.textContent || ""}`
    );
  });

  const roleMatches = (topic) => {
    const roles = (topic.dataset.guideRole || "all").split(/\s+/);
    return activeRole === "all" || roles.includes("all") || roles.includes(activeRole);
  };

  const applyFilters = () => {
    const query = normalize(searchInput ? searchInput.value : "");
    let visibleCount = 0;

    topics.forEach((topic) => {
      const matchesQuery = !query || topic.dataset.searchText.includes(query);
      const visible = roleMatches(topic) && matchesQuery;
      topic.hidden = !visible;
      if (visible) visibleCount += 1;
    });

    if (searchClear) searchClear.hidden = !query;
    if (emptyState) emptyState.hidden = visibleCount !== 0;

    if (searchStatus) {
      if (!query) {
        searchStatus.textContent = "";
      } else if (visibleCount === 0) {
        searchStatus.textContent = "لا توجد نتائج مطابقة.";
      } else if (visibleCount === 1) {
        searchStatus.textContent = "وجدنا موضوعًا واحدًا مناسبًا.";
      } else if (visibleCount === 2) {
        searchStatus.textContent = "وجدنا موضوعين مناسبين.";
      } else {
        searchStatus.textContent = `وجدنا ${visibleCount} موضوعات مناسبة.`;
      }
    }
  };

  const updateIndex = () => {
    indexGroups.forEach((item) => {
      const role = item.dataset.indexRole || "all";
      item.hidden = activeRole !== "all" && role !== "all" && role !== activeRole;
    });
  };

  roleButtons.forEach((button) => {
    button.addEventListener("click", () => {
      activeRole = button.dataset.guideFilter || "all";

      roleButtons.forEach((candidate) => {
        const selected = candidate === button;
        candidate.classList.toggle("is-active", selected);
        candidate.setAttribute("aria-pressed", selected ? "true" : "false");
      });

      updateIndex();
      applyFilters();

      const firstVisible = topics.find((topic) => {
        const roles = (topic.dataset.guideRole || "").split(/\s+/);
        return !topic.hidden && activeRole !== "all" && roles.includes(activeRole);
      }) || topics.find((topic) => !topic.hidden);
      if (firstVisible) {
        firstVisible.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
  });

  if (searchForm) {
    searchForm.addEventListener("submit", (event) => event.preventDefault());
  }

  if (searchInput) {
    searchInput.addEventListener("input", applyFilters);
  }

  const resetSearch = () => {
    if (!searchInput) return;
    searchInput.value = "";
    applyFilters();
    searchInput.focus();
  };

  if (searchClear) searchClear.addEventListener("click", resetSearch);
  if (emptyReset) emptyReset.addEventListener("click", resetSearch);

  document.querySelectorAll('a[href^="#"]').forEach((link) => {
    link.addEventListener("click", () => {
      if (guideDrawerOpen) closeGuideDrawer(false);
    });
  });

  if ("IntersectionObserver" in window && indexLinks.length) {
    const linkById = new Map(
      indexLinks
        .map((link) => [link.getAttribute("href")?.slice(1), link])
        .filter(([id]) => Boolean(id))
    );

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting && !entry.target.hidden)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);

        if (!visible.length) return;
        indexLinks.forEach((link) => link.classList.remove("is-current"));
        const currentLink = linkById.get(visible[0].target.id);
        if (currentLink) currentLink.classList.add("is-current");
      },
      { rootMargin: "-18% 0px -70% 0px", threshold: 0 }
    );

    topics.forEach((topic) => observer.observe(topic));
  }

  const updateBackToTop = () => {
    if (!backToTop) return;
    backToTop.classList.toggle("is-visible", window.scrollY > 700);
  };

  window.addEventListener("scroll", updateBackToTop, { passive: true });
  if (backToTop) {
    backToTop.addEventListener("click", () => {
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }

  updateIndex();
  applyFilters();
  updateBackToTop();
})();
