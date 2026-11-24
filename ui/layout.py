"""Page shell and small formatting helpers shared by the UI screens."""
import html
from decimal import Decimal

MISSING = '<span class="missing" title="no value">—</span>'
CHIP_KINDS = ("ok", "warn", "red", "info", "mute")

THEME_SCRIPT = """<script>
(function () {
  var root = document.documentElement, key = "kalbt-theme", saved = null;
  try { saved = localStorage.getItem(key); } catch (e) {}
  var dark = saved ? saved === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "dark" : "light";
  document.getElementById("theme-toggle").addEventListener("click", function () {
    var next = root.dataset.theme === "dark" ? "light" : "dark";
    root.dataset.theme = next;
    try { localStorage.setItem(key, next); } catch (e) {}
  });
})();
</script>"""


def esc(value):
    if value is None:
        return ""
    return html.escape(str(value))


def chip(word, kind):
    if kind not in CHIP_KINDS:
        raise ValueError(f"unknown chip kind {kind!r}")
    return f'<span class="chip chip-{kind}"><span class="dot"></span>{esc(word)}</span>'


def money(value):
    if value is None:
        return MISSING
    return f"{value:,.2f}"


def price(value):
    if value is None:
        return MISSING
    two_places = value.quantize(Decimal("0.01"))
    if two_places == value:
        return f"{two_places:,.2f}"
    return f"{value.quantize(Decimal('0.0001')):,.4f}"


def count(value):
    if value is None:
        return MISSING
    value = Decimal(value)
    if value == value.to_integral_value():
        return f"{int(value):,}"
    return f"{value.normalize():,f}"


def page(title, body, active):
    current = ' aria-current="page"' if active == "runs" else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · KalBacktester</title>
<link rel="stylesheet" href="/static/app.css">
</head>
<body>
<header class="site-header">
<div class="header-inner">
<span class="brand">KalBacktester</span>
<nav><a href="/runs"{current}>Runs</a></nav>
<button type="button" id="theme-toggle" aria-label="Switch light or dark theme">Theme</button>
</div>
</header>
<main>
{body}
</main>
{THEME_SCRIPT}
</body>
</html>
"""
