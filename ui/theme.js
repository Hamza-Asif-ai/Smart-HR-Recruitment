(function () {
  var THEME_KEY = "smart_hr_theme";
  var ACCENT_KEY = "smart_hr_accent";
  var themes = ["dark", "light", "system"];
  var accents = ["indigo", "emerald", "rose", "amber"];

  function stored(key, fallback, allowed) {
    try {
      var value = localStorage.getItem(key);
      return value && allowed.indexOf(value) !== -1 ? value : fallback;
    } catch (err) {
      return fallback;
    }
  }

  function apply(theme, accent) {
    document.documentElement.setAttribute("data-theme", theme);
    document.documentElement.setAttribute("data-accent", accent);
    try {
      localStorage.setItem(THEME_KEY, theme);
      localStorage.setItem(ACCENT_KEY, accent);
    } catch (err) {
      /* Storage may be blocked; the visual change still applies. */
    }
  }

  function currentTheme() {
    return document.documentElement.getAttribute("data-theme") ||
      stored(THEME_KEY, "dark", themes);
  }

  function currentAccent() {
    return document.documentElement.getAttribute("data-accent") ||
      stored(ACCENT_KEY, "indigo", accents);
  }

  function sync() {
    var themeSelect = document.getElementById("hr-theme-select");
    var accentSelect = document.getElementById("hr-accent-select");
    if (themeSelect) themeSelect.value = currentTheme();
    if (accentSelect) accentSelect.value = currentAccent();
  }

  document.addEventListener("change", function (event) {
    var target = event.target;
    if (!target || !target.id) return;
    if (target.id === "hr-theme-select" && themes.indexOf(target.value) !== -1) {
      apply(target.value, currentAccent());
      sync();
    }
    if (target.id === "hr-accent-select" && accents.indexOf(target.value) !== -1) {
      apply(currentTheme(), target.value);
      sync();
    }
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", sync);
  } else {
    sync();
  }
  var tries = 0;
  var timer = setInterval(function () {
    sync();
    tries += 1;
    if (tries > 40) clearInterval(timer);
  }, 250);
})();
