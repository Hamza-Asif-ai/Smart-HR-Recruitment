(function () {
  var THEME_KEY = "smart_hr_theme";
  var ACCENT_KEY = "smart_hr_accent";
  var themes = ["dark", "light", "system"];
  var accents = ["indigo", "emerald", "rose", "amber"];
  var theme = "dark";
  var accent = "indigo";
  try {
    var savedTheme = localStorage.getItem(THEME_KEY);
    var savedAccent = localStorage.getItem(ACCENT_KEY);
    if (savedTheme && themes.indexOf(savedTheme) !== -1) theme = savedTheme;
    if (savedAccent && accents.indexOf(savedAccent) !== -1) accent = savedAccent;
  } catch (err) {
    theme = "dark";
    accent = "indigo";
  }
  document.documentElement.setAttribute("data-theme", theme);
  document.documentElement.setAttribute("data-accent", accent);
})();
