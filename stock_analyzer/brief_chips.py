"""Equal-height CSS for 🏠 Home's three Today's Brief cards (pure, no streamlit).

The cards are (left to right) the market tone card, the Today's Actions card and
the Pullback-exposure gauge card. The first two are HTML cards written with
``st.markdown`` and the third is a keyed ``st.container``. Streamlit 1.57's
``st.columns`` row already stretches its columns to one height
(``FlexContainer.Align.STRETCH``), but what sits INSIDE a column does not follow:
the column holds a vertical block, then element containers, then (for markdown)
a ``stMarkdown`` div and a ``stMarkdownContainer`` div, all ``height: auto``.
``st.markdown`` has no ``height="stretch"`` (only ``st.container`` does), so the
cards would each end where their own content ends.

The rule: every ``div`` between a column and a card is made a flex column that
grows (``flex: 1 1 auto``), found with ``:has()`` on the card itself, so the chain
needs no hard-coded depth and survives a Streamlit wrapper being added or removed.
A card is either an element carrying ``.brief-card`` (the HTML cards) or the
keyed container ``.st-key-brief_frag_chip`` (the gauge card). Every selector starts
with ``.st-key-brief_chips`` (the keyed container that wraps the columns row) or
``.st-key-brief_frag_chip``, so nothing else on the page changes.

No tag names may appear in this text, even in a comment: it is emitted with
``st.html``, whose DOMPurify pass drops a style element whose text contains
``<`` followed by a letter, ``/`` or ``!``. Display only: no gate, no threshold,
no count logic.
"""
from __future__ import annotations

CHIPS_KEY = "brief_chips"
FRAG_KEY = "brief_frag_chip"
CARD_CLASS = "brief-card"
# The shared floor all three cards keep (the height the tone and actions cards had
# before). A display size, not a decision threshold, so it lives here.
CARD_MIN_HEIGHT_PX = 165
# Matches the tone and actions cards' fill, border, radius and padding.
_FRAG_BG = "#111827"
_FRAG_BORDER = "1px solid #31333f"
_CARD_PADDING = "14px 20px"
_CARD_RADIUS = "12px"

_ROOT = f".st-key-{CHIPS_KEY}"
_COLUMN = f'{_ROOT} [data-testid="stColumn"]'
_HAS_CARD = f":has(.{CARD_CLASS}, .st-key-{FRAG_KEY})"

_CSS = (
    "<style>"
    # space under the row, where each card's own bottom margin used to be
    f"{_ROOT} {{ margin-bottom: 12px; }} "
    # the column itself, then every div between it and a card, grow as flex columns
    f"{_COLUMN}{_HAS_CARD} {{ display: flex; flex-direction: column; }} "
    f"{_COLUMN} div{_HAS_CARD} "
    "{ display: flex; flex-direction: column; align-items: stretch; flex: 1 1 auto; } "
    # Streamlit gives the markdown container a negative bottom margin
    f'{_COLUMN} [data-testid="stMarkdownContainer"]:has(> .{CARD_CLASS}) '
    "{ margin-bottom: 0; } "
    # the HTML cards fill their column and keep the shared floor
    f"{_ROOT} .{CARD_CLASS} {{ flex: 1 1 auto; box-sizing: border-box; margin: 0; "
    f"min-height: {CARD_MIN_HEIGHT_PX}px; }} "
    # the gauge card: the same look as the tone and actions cards
    f".st-key-{FRAG_KEY} {{ flex: 1 1 auto; box-sizing: border-box; "
    f"background: {_FRAG_BG}; border: {_FRAG_BORDER}; border-radius: {_CARD_RADIUS}; "
    f"padding: {_CARD_PADDING}; min-height: {CARD_MIN_HEIGHT_PX}px; }}"
    "</style>"
)


def chips_css() -> str:
    """The complete ``<style>`` block for the three cards: a constant, no input."""
    return _CSS
