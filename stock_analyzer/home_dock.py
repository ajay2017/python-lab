"""Home dock: a VS Code-style panel pinned to the bottom of 🏠 Home (pure, no streamlit).

The dock is a keyed ``st.container`` (``.st-key-home_dock``) declared at the top
of the Home branch in app.py, so later fill sites keep working; the CSS built
here pins it to the bottom of the screen, so where it is declared does not
decide where it shows. Display only: no gate, no threshold, no count logic.

C0 is a probe (docs/plans/home-dock.md): it puts only the market index strip
in the dock to prove the pinning on Railway. Two pinning candidates exist:
"sticky" (the default; keeps the main column's width) and "fixed" (the
fallback). ``?dock=fixed`` in the URL switches to the fallback for the probe
only; that switch is removed once C0 picks one.
"""
from __future__ import annotations

DOCK_KEY = "home_dock"
DOCK_EXPANDER_KEY = "_home_dock_exp"
DOCK_TABS_KEY = "_home_dock_tabs"

# Owner Q6 (2026-11-19): about 3 rows of content. A display size, not a decision
# threshold, so it lives here and not in constants.py.
DOCK_PANE_HEIGHT_PX = 170

DOCK_MODES = ("sticky", "fixed")
DEFAULT_DOCK_MODE = "sticky"

# The dock's flex child of its parent vertical block, whether the keyed container
# is the child itself or is wrapped one level (Streamlit 1.57's stLayoutWrapper).
_DOCK_CHILD = (
    '[data-testid="stVerticalBlock"] > '
    ":is(.st-key-home_dock, :has(> .st-key-home_dock))"
)

# Streamlit's own theme backgroundColor (.streamlit/config.toml) and a muted border.
_BG = "#131722"
_BORDER = "1px solid #31333f"

# Cap the open dock so it can't eat a phone screen.
_CAP_RULE = (
    ".st-key-home_dock { max-height: 45vh; overflow: auto; }"
)

_STICKY_CSS = (
    "<style>"
    f"{_DOCK_CHILD} {{ order: 9999; position: sticky; bottom: 0; z-index: 990; "
    f"background: {_BG}; border-top: {_BORDER}; padding-top: 4px; }} "
    f"{_CAP_RULE}"
    "</style>"
)

_FIXED_CSS = (
    "<style>"
    f"{_DOCK_CHILD} {{ position: fixed; left: 0; right: 0; bottom: 0; z-index: 999; "
    f"background: {_BG}; border-top: {_BORDER}; padding: 4px 16px; }} "
    '[data-testid="stMainBlockContainer"] { padding-bottom: 320px; } '
    f"{_CAP_RULE}"
    "</style>"
)


def dock_mode(raw) -> str:
    """Resolve the ``?dock=`` query value to a pinning mode. Never raises.

    ``raw`` is whatever ``st.query_params.get("dock")`` returns (None or a str in
    Streamlit 1.57, which returns the last item of a repeated key; a list is
    tolerated anyway). Anything that is not exactly one of DOCK_MODES (after
    strip + lower) falls back to DEFAULT_DOCK_MODE.
    """
    try:
        if isinstance(raw, str):
            val = raw.strip().lower()
            if val in DOCK_MODES:
                return val
    except Exception:
        pass
    return DEFAULT_DOCK_MODE


def dock_css(mode) -> str:
    """The complete ``<style>`` block for ``mode``.

    Chosen from two module-level constants; the input is never interpolated into
    the CSS, so a hostile query value cannot reach the page. An unknown mode gets
    the sticky CSS.
    """
    if mode == "fixed":
        return _FIXED_CSS
    return _STICKY_CSS
