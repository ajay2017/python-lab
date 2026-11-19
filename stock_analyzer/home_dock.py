"""Bottom dock: a VS Code-style panel pinned to the bottom of every page (pure, no streamlit).

The dock is on every page (owner 2026-11-19). It is a keyed ``st.container``
(``.st-key-home_dock``) declared at module level in app.py, before page
dispatch, so a page that stops early still gets it; the CSS built here pins it
to the bottom of the screen, so where it is declared does not decide where it
shows. Sticky pinning (which keeps the main column's width) was proven on
Railway in the C0 probe, so the probe's fixed-position fallback and its URL
switch are gone. C1: on 🏠 Home the dock has four fixed-label tabs (Act Today,
Notices, Live Prices, Markets; no counts in a label, because Streamlit fixes
the labels when ``st.tabs`` is called, before Home computes anything); every
other page keeps the one-tab Markets dock until C1b. Home and the other pages
use separate widget keys so a tab picked on Home cannot carry onto the one-tab
dock. See docs/plans/home-dock.md. ``.st-key-home_dock`` keeps its old name,
because renaming the key would only churn. Display only: no gate, no
threshold, no count logic.
"""
from __future__ import annotations

DOCK_KEY = "home_dock"
# Keys for the one-tab dock on every page except Home.
DOCK_EXPANDER_KEY = "_home_dock_exp"
DOCK_TABS_KEY = "_home_dock_tabs"
# Separate keys on Home, so a tab index picked there can't carry onto a 1-tab dock.
HOME_DOCK_EXPANDER_KEY = "_home_dock_exp_home"
HOME_DOCK_TABS_KEY = "_home_dock_tabs_home"

# Tab labels are fixed (no counts): Streamlit sets them, and the starting tab, when
# st.tabs is called at module level, before Home has computed anything. The first
# tab is the default.
HOME_DOCK_TABS = ("🔴 Act Today", "⚠️ Notices", "💹 Live Prices", "📈 Markets")
OTHER_DOCK_TABS = ("📈 Markets",)
HOME_DOCK_TITLE = "🏠 Act Today · Notices · Live Prices · Markets"
OTHER_DOCK_TITLE = "📈 Markets"

# Owner Q6 (2026-11-19): about 3 rows of content. A display size, not a decision
# threshold, so it lives here and not in constants.py.
DOCK_PANE_HEIGHT_PX = 170

# The dock's flex child of its parent vertical block, whether the keyed container
# is the child itself or is wrapped one level (Streamlit 1.57's stLayoutWrapper).
_DOCK_CHILD = (
    '[data-testid="stVerticalBlock"] > '
    ":is(.st-key-home_dock, :has(> .st-key-home_dock))"
)
# The vertical block that directly holds the dock (the page's main block).
_DOCK_PARENT = (
    '[data-testid="stVerticalBlock"]:has(> '
    ":is(.st-key-home_dock, :has(> .st-key-home_dock)))"
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
    f"background: {_BG}; border-top: {_BORDER}; padding-top: 4px; margin-top: auto; }} "
    # At the end of the page a sticky element rests at its own place in the flow,
    # so Streamlit's bottom padding (~10rem) showed as a blank band under the dock
    # (C0 probe, owner 2026-11-19). The CSS is emitted on every page now.
    '[data-testid="stMainBlockContainer"] { padding-bottom: 0; } '
    # A page shorter than the screen (📖 User Guide with its sections collapsed)
    # left the dock floating mid-screen: sticky only holds an element at the
    # bottom when the page is taller than the screen (owner 2026-11-19). The
    # vertical block holding the dock is made at least one screen tall (minus
    # Streamlit's 6rem top padding), and the dock's `margin-top: auto` above
    # pushes it to that block's bottom.
    f'{_DOCK_PARENT} {{ min-height: calc(100dvh - 6rem); }} '
    f"{_CAP_RULE}"
    "</style>"
)


def dock_css() -> str:
    """The complete ``<style>`` block for the dock: a constant, no input."""
    return _STICKY_CSS


def dock_tabs(on_home: bool) -> tuple[str, ...]:
    """The tab labels, in order: four on Home, Markets alone elsewhere."""
    return HOME_DOCK_TABS if on_home else OTHER_DOCK_TABS


def dock_title(on_home: bool) -> str:
    """The dock's collapsible title bar."""
    return HOME_DOCK_TITLE if on_home else OTHER_DOCK_TITLE


def home_tabs_key(run_n: int) -> str:
    """Home's ``st.tabs`` key for full script run number ``run_n``.

    Streamlit's frontend keeps the picked tab as element state under the block
    id, which is built from the key alone, and restores it on every run while
    the labels are unchanged. A fixed key would therefore leave Home on a tab
    the owner picked earlier (say Live Prices), hiding the Act Today pointer and
    the Needs-a-look line. A fresh key per FULL script run resets the dock to
    its first tab (Act Today); a fragment rerun (the 60 s Live Prices and
    Markets refresh) does not execute module code, so the counter, and with it
    the key, does not change and a tab picked between full runs survives it.
    """
    return f"{HOME_DOCK_TABS_KEY}_{run_n}"


def dock_tabs_key(on_home: bool, run_n: int = 0) -> str:
    """The ``st.tabs`` widget key: per-run on Home (see ``home_tabs_key``), the
    constant ``DOCK_TABS_KEY`` on every other page (separate from Home's, see the
    module docstring)."""
    return home_tabs_key(run_n) if on_home else DOCK_TABS_KEY


def dock_expander_key(on_home: bool) -> str:
    """The dock expander's widget key (separate on Home)."""
    return HOME_DOCK_EXPANDER_KEY if on_home else DOCK_EXPANDER_KEY
