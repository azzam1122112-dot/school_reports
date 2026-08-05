(function () {
  const picker = document.getElementById('schoolPicker');
  if (!picker) return;

  const searchUrl = picker.dataset.searchUrl;
  const resultsEl = document.getElementById('schoolsResults');
  const searchInput = document.getElementById('schoolSearchInput');
  const metaEl = document.getElementById('schoolsResultMeta');
  const loadMoreBtn = document.getElementById('loadMoreSchoolsBtn');
  const refreshBtn = document.getElementById('refreshSchoolsBtn');
  const clearBtn = document.getElementById('clearSelectedSchoolsBtn');
  const selectVisibleBtn = document.getElementById('selectVisibleSchoolsBtn');
  const selectedIdsInput = document.getElementById('selectedSchoolIds');
  const selectedStrip = document.getElementById('selectedSchoolsStrip');
  const targetAll = document.getElementById('targetAll');
  const targetSpecific = document.getElementById('targetSpecific');
  const resetForm = document.getElementById('resetForm');
  const selectedDataCount = document.getElementById('selectedDataCount');
  const dataOptionInputs = Array.from(resetForm.querySelectorAll('.data-grid .data-card input[type="checkbox"]'));

  const selected = new Map();
  let visibleRows = [];
  let page = 1;
  let hasNext = false;
  let loading = false;
  let timer = null;

  function escapeHtml(value) {
    return String(value || '').replace(/[&<>"']/g, function (char) {
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char];
    });
  }

  function syncHiddenIds() {
    selectedIdsInput.value = Array.from(selected.keys()).join(',');
  }

  function renderSelected() {
    selectedStrip.innerHTML = '';
    if (!selected.size) {
      const hint = document.createElement('span');
      hint.className = 'hint';
      hint.textContent = 'لم يتم اختيار مدارس بعد.';
      selectedStrip.appendChild(hint);
      syncHiddenIds();
      return;
    }
    const count = document.createElement('span');
    count.className = 'status-pill';
    count.textContent = selected.size + ' مدرسة مختارة';
    selectedStrip.appendChild(count);
    Array.from(selected.values()).slice(0, 12).forEach(function (school) {
      const chip = document.createElement('span');
      chip.className = 'school-chip';
      chip.innerHTML = '<span>' + escapeHtml(school.name) + '</span><button type="button" data-remove-id="' + school.id + '" title="إزالة"><i class="fa-solid fa-xmark" style="pointer-events:none"></i></button>';
      selectedStrip.appendChild(chip);
    });
    if (selected.size > 12) {
      const more = document.createElement('span');
      more.className = 'hint';
      more.textContent = '+' + (selected.size - 12) + ' أخرى';
      selectedStrip.appendChild(more);
    }
    syncHiddenIds();
  }

  const AVATAR_COLORS = ['#0f8f87','#b9975b','#ec4899','#0072bc','#14b8a6','#b9975b','#16845b','#f43f5e'];
  function avatarColor(id) { return AVATAR_COLORS[Math.abs(parseInt(id, 10) || 0) % AVATAR_COLORS.length]; }
  function avatarInitial(name) { return (name || '?').trim()[0] || '?'; }

  function renderRows(rows, append) {
    if (!append) resultsEl.innerHTML = '';
    visibleRows = append ? visibleRows.concat(rows) : rows.slice();
    if (!rows.length && !append) {
      resultsEl.innerHTML = '<div style="text-align:center;padding:3rem 2rem;color:#94a3b8;font-weight:700;"><i class="fa-regular fa-circle-xmark" style="font-size:2rem;display:block;margin-bottom:.75rem;"></i>لا توجد مدارس مطابقة للبحث</div>';
      return;
    }
    rows.forEach(function (school) {
      const isChecked = selected.has(String(school.id));
      const row = document.createElement('label');
      row.className = 'school-row' + (isChecked ? ' is-selected' : '');
      row.dataset.schoolId = school.id;
      const stateClass = school.is_active ? 'school-state' : 'school-state off';
      const stateLabel = school.is_active ? 'نشطة' : 'غير نشطة';
      const color = avatarColor(school.id);
      const initial = escapeHtml(avatarInitial(school.name));
      row.innerHTML =
        '<input type="checkbox" value="' + school.id + '"' + (isChecked ? ' checked' : '') + '>' +
        '<div class="school-avatar" style="background:' + color + '">' + initial + '</div>' +
        '<div class="school-info">' +
          '<div class="school-name">' + escapeHtml(school.name) + '</div>' +
          '<div class="school-meta">#' + school.id + ' · ' + escapeHtml(school.code) + ' · ' + escapeHtml(school.city || 'بدون مدينة') + '</div>' +
        '</div>' +
        '<div class="school-row-right">' +
          '<span class="' + stateClass + '">' + stateLabel + '</span>' +
          '<div class="school-check-icon"><i class="fa-solid fa-check"></i></div>' +
        '</div>';
      resultsEl.appendChild(row);
    });
  }

  async function loadSchools(options) {
    if (loading) return;
    const append = options && options.append;
    loading = true;
    metaEl.textContent = 'جاري تحميل المدارس...';
    loadMoreBtn.disabled = true;
    const url = new URL(searchUrl, window.location.origin);
    url.searchParams.set('q', searchInput.value || '');
    url.searchParams.set('page', String(page));
    try {
      const response = await fetch(url.toString(), {headers: {'X-Requested-With': 'XMLHttpRequest'}});
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const payload = await response.json();
      hasNext = Boolean(payload.has_next);
      renderRows(payload.results || [], append);
      metaEl.textContent = 'تم العثور على ' + payload.total + ' مدرسة';
      loadMoreBtn.style.display = hasNext ? 'inline-flex' : 'none';
    } catch (error) {
      resultsEl.innerHTML = '<div class="school-row"><div></div><div class="hint">تعذر تحميل المدارس. حاول مرة أخرى.</div><div></div></div>';
      metaEl.textContent = 'خطأ في تحميل المدارس';
    } finally {
      loading = false;
      loadMoreBtn.disabled = false;
    }
  }

  function resetSearch() {
    page = 1;
    visibleRows = [];
    loadSchools({append: false});
  }

  function syncDataSelectionCount() {
    if (!selectedDataCount) return;
    const checkedCount = dataOptionInputs.filter(function (input) { return input.checked; }).length;
    selectedDataCount.textContent = checkedCount
      ? checkedCount + ' من ' + dataOptionInputs.length + ' محددة للحذف'
      : 'لم تحدد بيانات تشغيلية للحذف';
  }

  resultsEl.addEventListener('change', function (event) {
    const input = event.target;
    if (!input || input.type !== 'checkbox') return;
    const id = String(input.value);
    const row = visibleRows.find(function (item) { return String(item.id) === id; });
    if (input.checked && row) selected.set(id, row);
    if (!input.checked) selected.delete(id);
    const label = input.closest('.school-row');
    if (label) label.classList.toggle('is-selected', input.checked);
    renderSelected();
  });

  selectedStrip.addEventListener('click', function (event) {
    const button = event.target.closest('button[data-remove-id]');
    if (!button) return;
    selected.delete(String(button.dataset.removeId));
    const input = resultsEl.querySelector('input[value="' + button.dataset.removeId + '"]');
    if (input) input.checked = false;
    renderSelected();
  });

  searchInput.addEventListener('input', function () {
    clearTimeout(timer);
    timer = setTimeout(resetSearch, 250);
  });
  refreshBtn.addEventListener('click', resetSearch);
  clearBtn.addEventListener('click', function () {
    selected.clear();
    resultsEl.querySelectorAll('input[type="checkbox"]').forEach(function (input) { input.checked = false; });
    renderSelected();
  });
  loadMoreBtn.addEventListener('click', function () {
    if (!hasNext) return;
    page += 1;
    loadSchools({append: true});
  });
  selectVisibleBtn.addEventListener('click', function () {
    if (targetAll.checked) return;
    visibleRows.forEach(function (school) { selected.set(String(school.id), school); });
    resultsEl.querySelectorAll('input[type="checkbox"]').forEach(function (input) { input.checked = true; });
    renderSelected();
  });

  function syncMode() {
    const all = targetAll.checked;
    picker.style.opacity = all ? '0.55' : '1';
    picker.style.pointerEvents = all ? 'none' : 'auto';
  }
  targetAll.addEventListener('change', syncMode);
  targetSpecific.addEventListener('change', syncMode);
  dataOptionInputs.forEach(function (input) {
    input.addEventListener('change', syncDataSelectionCount);
  });

  resetForm.addEventListener('submit', function (event) {
    if (targetSpecific.checked && selected.size === 0) {
      event.preventDefault();
      metaEl.textContent = 'اختر مدرسة واحدة على الأقل أو فعّل خيار جميع المدارس.';
      searchInput.focus();
    }
  });

  renderSelected();
  syncMode();
  syncDataSelectionCount();
  resetSearch();
})();
