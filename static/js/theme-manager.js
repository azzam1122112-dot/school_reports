(function () {
  'use strict';

  var root = document.documentElement;
  var media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  var storageKey = 'theme';

  function readCookie(name) {
    var parts = document.cookie ? document.cookie.split('; ') : [];
    for (var index = 0; index < parts.length; index += 1) {
      var separator = parts[index].indexOf('=');
      var key = separator > -1 ? parts[index].slice(0, separator) : parts[index];
      if (key === name) {
        return separator > -1 ? decodeURIComponent(parts[index].slice(separator + 1)) : '';
      }
    }
    return '';
  }

  function readPreference() {
    var saved = '';
    try { saved = window.localStorage.getItem(storageKey) || ''; } catch (error) {}
    if (saved !== 'dark' && saved !== 'light') saved = readCookie(storageKey);
    return saved === 'dark' || saved === 'light' ? saved : '';
  }

  function persist(theme) {
    try { window.localStorage.setItem(storageKey, theme); } catch (error) {}
    var secure = window.location.protocol === 'https:' ? '; Secure' : '';
    document.cookie = storageKey + '=' + encodeURIComponent(theme)
      + '; path=/; max-age=31536000; SameSite=Lax' + secure;
  }

  function preferredTheme() {
    return readPreference() || (media && media.matches ? 'dark' : 'light');
  }

  function updateButton(button, theme) {
    var dark = theme === 'dark';
    var label = dark ? 'تفعيل الوضع النهاري' : 'تفعيل الوضع الليلي';
    button.setAttribute('aria-label', label);
    button.setAttribute('title', label);
    button.setAttribute('aria-pressed', dark ? 'true' : 'false');
    var icon = button.querySelector('i');
    if (icon) icon.className = dark ? 'fa-solid fa-sun' : 'fa-solid fa-moon';
  }

  function announce(theme) {
    var live = document.getElementById('themeStatus');
    if (!live) {
      live = document.createElement('span');
      live.id = 'themeStatus';
      live.className = 'theme-status';
      live.setAttribute('role', 'status');
      live.setAttribute('aria-live', 'polite');
      document.body.appendChild(live);
    }
    live.textContent = theme === 'dark' ? 'تم تفعيل الوضع الليلي' : 'تم تفعيل الوضع النهاري';
  }

  function apply(theme, options) {
    options = options || {};
    var normalized = theme === 'dark' ? 'dark' : 'light';
    root.setAttribute('data-theme', normalized);
    root.style.colorScheme = normalized;
    root.classList.add('theme-changing');
    window.setTimeout(function () { root.classList.remove('theme-changing'); }, 220);

    var themeColor = document.querySelector('meta[name="theme-color"]');
    if (themeColor) themeColor.setAttribute('content', normalized === 'dark' ? '#061512' : '#006c35');

    document.querySelectorAll('.theme-toggle').forEach(function (button) {
      updateButton(button, normalized);
    });

    if (options.persist) persist(normalized);
    if (options.announce) announce(normalized);
    window.dispatchEvent(new CustomEvent('themechange', { detail: { theme: normalized } }));
  }

  function createFloatingToggle() {
    // صفحة الهبوط تختار عدم إظهار الزر العائم حتى لا يزاحم دعوات الإجراء،
    // مع بقاء تطبيق السمة المحفوظة فلا يرى زائرٌ اختار الوضع الليلي صفحةً نهارية.
    if (root.getAttribute('data-theme-toggle') === 'off') return;
    if (document.querySelector('.theme-toggle')) return;
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'theme-toggle theme-toggle--floating';
    button.innerHTML = '<i class="fa-solid fa-moon" aria-hidden="true"></i>';
    document.body.appendChild(button);
  }

  function initialize() {
    createFloatingToggle();
    apply(preferredTheme());

    document.querySelectorAll('.theme-toggle').forEach(function (button) {
      if (button.dataset.themeBound === 'true') return;
      button.dataset.themeBound = 'true';
      button.addEventListener('click', function () {
        var next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
        apply(next, { persist: true, announce: true });
      });
    });

    var followSystem = function (event) {
      if (!readPreference()) apply(event.matches ? 'dark' : 'light', { announce: true });
    };
    if (media) {
      if (media.addEventListener) media.addEventListener('change', followSystem);
      else if (media.addListener) media.addListener(followSystem);
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize);
  else initialize();
})();
