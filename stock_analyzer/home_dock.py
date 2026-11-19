"""Bottom dock: a VS Code-style panel pinned to the bottom of every page (pure, no streamlit).

The dock is on every page (owner 2026-11-19). It is a keyed ``st.container``
(``.st-key-home_dock``) declared at module level in app.py, before page
dispatch, so a page that stops early still gets it; the CSS built here pins it
to the bottom of the screen, so where it is declared does not decide where it
shows. Sticky pinning (which keeps the main column's width) was proven on
Railway in the C0 probe, so the probe's fixed-position fallback and its URL
switch are gone. Markets is the only tab until C1 (Home) / C1b (other pages);
see docs/plans/home-dock.md. ``.st-key-home_dock`` keeps its old name, because
renaming the key would only churn. Display only: no gate, no threshold, no
count logic.
"""
from __future__ import annotations

DOCK_KEY = "home_dock"
DOCK_EXPANDER_KEY = "_home_dock_exp"
DOCK_TABS_KEY = "_home_dock_tabs"

# Owner Q6 (2026-11-19): about 3 rows of content. A display size, not a decision
# threshold, so it lives here and not in constants.py.
DOCK_PANE_HEIGHT_PX = 170

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
    # At the end of the page a sticky element rests at its own place in the flow,
    # so Streamlit's bottom padding (~10rem) showed as a blank band under the dock
    # (C0 probe, owner 2026-11-19). The CSS is emitted on every page now.
    '[data-testid="stMainBlockContainer"] { padding-bottom: 0; } '
    f"{_CAP_RULE}"
    "</style>"
)


def dock_css() -> str:
    """The complete ``<style>`` block for the dock: a constant, no input."""
    return _STICKY_CSS
