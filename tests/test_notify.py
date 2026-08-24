"""Tests for stock_analyzer/notify.py — the Resend HTTP email delivery layer
(protective-alert, pullback-awareness, buy-picks, daily-action, intraday-entry,
weekly-debrief, and monthly-intelligence emails). Previously zero test
coverage despite real subject-line branching logic. Pure: every render_*
function is a pure string builder; the ONE I/O boundary is
`send_email_resend`'s `requests.post` call, mocked below via
`unittest.mock.patch`.
"""
from unittest.mock import patch, MagicMock

import pytest

from stock_analyzer import notify


# ─── render_alert_email ──────────────────────────────────────────────────────

def test_render_alert_email_subject_with_hard_alerts():
    alerts = [{"ticker": "AAPL", "kind": "stop_breach", "directive": "Sell now"}]
    subject, body = notify.render_alert_email(alerts, "2026-01-15 08:00:00")
    assert "1 protective action" in subject
    assert "AAPL" in subject
    assert "STOP BREACH" in body


def test_render_alert_email_subject_velocity_only_singular():
    vel = [{"ticker": "MSFT", "delta": -5.0, "n_days": 3}]
    subject, body = notify.render_alert_email([], "2026-01-15 08:00:00", velocity_alerts=vel)
    assert "1 WATCH accelerating" in subject
    assert "MSFT" in subject


def test_render_alert_email_subject_velocity_only_plural():
    vel = [
        {"ticker": "MSFT", "delta": -5.0, "n_days": 3},
        {"ticker": "NVDA", "delta": -3.0, "n_days": 2},
    ]
    subject, body = notify.render_alert_email([], "2026-01-15 08:00:00", velocity_alerts=vel)
    assert "2 WATCH signals accelerating" in subject


def test_render_alert_email_body_escapes_html():
    alerts = [{"ticker": "<script>", "kind": "stop_breach", "directive": "x"}]
    _, body = notify.render_alert_email(alerts, "2026-01-15 08:00:00")
    assert "<script>" not in body
    assert "&lt;script&gt;" in body


# ─── render_test_email ───────────────────────────────────────────────────────

def test_render_test_email_includes_count_and_fixed_subject():
    subject, body = notify.render_test_email(3, "2026-01-15 08:00:00")
    assert subject == "DRISHTA · alerts pipeline test — delivery OK"
    assert ">3<" in body


def test_render_test_email_singular_plural_wording():
    _, body_one = notify.render_test_email(1, "2026-01-15 08:00:00")
    _, body_many = notify.render_test_email(2, "2026-01-15 08:00:00")
    assert "action today" in body_one
    assert "actions today" in body_many


# ─── render_pullback_email ───────────────────────────────────────────────────

def test_render_pullback_email_basic_subject():
    pb = {"index_pct": -2.5, "book_implied_pct": -3.0, "mult": 1.2, "severity": "moderate", "exposed": ["AAPL"]}
    subject, body = notify.render_pullback_email(pb, "2026-01-15 08:00:00")
    assert "-2.5%" in subject
    assert "AAPL" in body
    assert "1.2" in body


def test_render_pullback_email_no_book_data_omits_book_line():
    pb = {"index_pct": -1.5}
    _, body = notify.render_pullback_email(pb, "2026-01-15 08:00:00")
    assert "market exposure" not in body


# ─── render_buy_picks_email ──────────────────────────────────────────────────

def _pick(ticker="AAA", composite_score=75.0, score=80.0, sector="Tech", conviction="High",
          day_change=1.0, thesis="Strong momentum", sizing=None):
    return {
        "ticker": ticker, "composite_score": composite_score, "score": score,
        "sector": sector, "conviction": conviction, "day_change": day_change,
        "thesis": thesis, "composite_label": "Buy",
        "sizing": sizing or {"entry_lo": 100.0, "entry_hi": 105.0, "shares": 10,
                              "total_cost": 1020.0, "stop": 95.0, "port_pct": 2.0},
    }


def test_render_buy_picks_email_subject_and_card_content():
    picks = [_pick()]
    subject, body = notify.render_buy_picks_email(picks, "2026-01-15 08:00:00")
    assert "1 buy setup today" in subject
    assert "AAA" in body
    assert "Buy in" in body
    assert "stop" in body


def test_render_buy_picks_email_multiple_picks_plural_subject():
    picks = [_pick(ticker="AAA"), _pick(ticker="BBB")]
    subject, _ = notify.render_buy_picks_email(picks, "2026-01-15 08:00:00")
    assert "2 buy setups today" in subject


# ─── render_daily_action_email — 3 subject-line formats ─────────────────────

def test_render_daily_action_email_subject_with_exit_alerts():
    top_pick = _pick(ticker="AAA", composite_score=75.0)
    exit_alerts = [{"ticker": "ZZZ", "signal_type": "EXIT"}]
    subject, _ = notify.render_daily_action_email(top_pick, exit_alerts, [], "2026-01-15 08:00:00")
    assert subject == "DRISHTA · Exit ZZZ + Enter AAA"


def test_render_daily_action_email_subject_with_composite_no_exits():
    top_pick = _pick(ticker="AAA", composite_score=82.0)
    subject, _ = notify.render_daily_action_email(top_pick, [], [], "2026-01-15 08:00:00")
    assert subject == "DRISHTA · Act on AAA — 82/100"


def test_render_daily_action_email_subject_fallback_no_composite():
    top_pick = _pick(ticker="AAA", composite_score=None)
    subject, _ = notify.render_daily_action_email(top_pick, [], [], "2026-01-15 08:00:00")
    assert subject == "DRISHTA · Morning action: AAA"


def test_render_daily_action_email_high_conviction_badge():
    from stock_analyzer.constants import SCAN_TOP_PICK_MIN_COMPOSITE
    top_pick = _pick(ticker="AAA", composite_score=float(SCAN_TOP_PICK_MIN_COMPOSITE))
    _, body = notify.render_daily_action_email(top_pick, [], [], "2026-01-15 08:00:00")
    assert "HIGH CONVICTION" in body


def test_render_daily_action_email_moderate_badge_below_threshold():
    from stock_analyzer.constants import SCAN_TOP_PICK_MIN_COMPOSITE
    top_pick = _pick(ticker="AAA", composite_score=float(SCAN_TOP_PICK_MIN_COMPOSITE) - 1)
    _, body = notify.render_daily_action_email(top_pick, [], [], "2026-01-15 08:00:00")
    assert "MODERATE" in body


def test_render_daily_action_email_other_picks_rendered():
    top_pick = _pick(ticker="AAA")
    other = [_pick(ticker="BBB", composite_score=60.0)]
    _, body = notify.render_daily_action_email(top_pick, [], other, "2026-01-15 08:00:00")
    assert "BBB" in body
    assert "OTHER SETUPS" in body


# ─── _book_drift_banner (F-252 follow-up, 2026-08-24) ────────────────────────

def test_book_drift_banner_silent_for_none():
    assert notify._book_drift_banner(None) == ""


@pytest.mark.parametrize("state", ["unknown", "stale_clean", "awaiting_sync", "none"])
def test_book_drift_banner_silent_for_non_drift_states(state):
    """Deliberately diverges from the in-app Home banner, which also renders
    on 'unknown' -- this is a PUSH surface, so silence never asserts
    cleanliness, and rendering 'unknown' on every push email a user with no
    broker linked receives would be exactly the amber-fatigue this module's
    docstrings warn against. Fire only on state == 'drift'."""
    assert notify._book_drift_banner({"state": state}) == ""


def test_book_drift_banner_renders_for_drift_with_impact():
    banner = notify._book_drift_banner({"state": "drift", "impact": {"overstated": 500.0}})
    assert "disagrees with your broker" in banner
    assert "$500" in banner


def test_book_drift_banner_renders_without_fabricating_a_number_when_impact_absent():
    banner = notify._book_drift_banner({"state": "drift"})
    assert "disagrees with your broker" in banner
    assert "$" not in banner


def test_book_drift_banner_ignores_non_dict_input():
    """Defensive: a non-dict verdict (e.g. a stray string or list from a
    caller bug) must render silent, not raise."""
    assert notify._book_drift_banner("drift") == ""
    assert notify._book_drift_banner([]) == ""


# ─── render_daily_action_email / render_intraday_entry_email — book_drift wiring ──

def test_daily_action_email_omits_drift_banner_by_default():
    top_pick = _pick(ticker="AAA")
    _, body = notify.render_daily_action_email(top_pick, [], [], "2026-01-15 08:00:00")
    assert "disagrees with your broker" not in body


def test_daily_action_email_shows_drift_banner_when_state_is_drift():
    top_pick = _pick(ticker="AAA")
    _, body = notify.render_daily_action_email(
        top_pick, [], [], "2026-01-15 08:00:00",
        book_drift={"state": "drift", "impact": {"overstated": 500.0}},
    )
    assert "disagrees with your broker" in body


def test_daily_action_email_sizing_text_unchanged_regardless_of_drift_state():
    """The core invariant: this annotation must never alter the suggested
    share size, whichever drift state is passed in."""
    top_pick = _pick(ticker="AAA")
    _, body_none    = notify.render_daily_action_email(top_pick, [], [], "2026-01-15 08:00:00", book_drift=None)
    _, body_unknown = notify.render_daily_action_email(top_pick, [], [], "2026-01-15 08:00:00", book_drift={"state": "unknown"})
    _, body_drift   = notify.render_daily_action_email(top_pick, [], [], "2026-01-15 08:00:00", book_drift={"state": "drift", "impact": {}})
    for body in (body_none, body_unknown, body_drift):
        assert "~10 shares" in body
        assert "2.0% of book" in body


def test_intraday_entry_email_omits_drift_banner_by_default():
    entries = [_entry()]
    _, body = notify.render_intraday_entry_email(entries, -1.0, "2026-01-15 08:00:00")
    assert "disagrees with your broker" not in body


def test_intraday_entry_email_shows_drift_banner_when_state_is_drift():
    entries = [_entry()]
    _, body = notify.render_intraday_entry_email(
        entries, -1.0, "2026-01-15 08:00:00",
        book_drift={"state": "drift", "impact": {"overstated": 200.0}},
    )
    assert "disagrees with your broker" in body


# ─── render_intraday_entry_email ─────────────────────────────────────────────

def _entry(ticker="AAA", intraday_drop_pct=-4.0, current_price=96.0, open_price=100.0, composite_score=70.0):
    return {
        "ticker": ticker, "intraday_drop_pct": intraday_drop_pct,
        "current_price": current_price, "open_price": open_price,
        "composite_score": composite_score, "sector": "Tech",
        "sizing": {"entry_lo": 90.0, "entry_hi": 95.0, "stop": 85.0},
    }


def test_render_intraday_entry_email_single_entry_subject():
    entries = [_entry()]
    subject, body = notify.render_intraday_entry_email(entries, -1.0, "2026-01-15 08:00:00")
    assert "Entry window: AAA down 4.0% from open" in subject
    assert "SPY" in body


def test_render_intraday_entry_email_multi_entry_subject():
    entries = [_entry(ticker="AAA"), _entry(ticker="BBB")]
    subject, _ = notify.render_intraday_entry_email(entries, None, "2026-01-15 08:00:00")
    assert subject == "DRISHTA · 2 entry windows now — AAA leads"


def test_render_intraday_entry_email_no_spy_drop_omits_spy_line():
    entries = [_entry()]
    _, body = notify.render_intraday_entry_email(entries, None, "2026-01-15 08:00:00")
    assert "SPY" not in body


# ─── _email_md_inline ─────────────────────────────────────────────────────────

def test_email_md_inline_bold():
    assert notify._email_md_inline("this is **bold** text") == "this is <strong>bold</strong> text"


def test_email_md_inline_italic():
    assert notify._email_md_inline("this is *italic* text") == "this is <em>italic</em> text"


def test_email_md_inline_both():
    result = notify._email_md_inline("**bold** and *italic*")
    assert result == "<strong>bold</strong> and <em>italic</em>"


def test_email_md_inline_no_markdown_passthrough():
    assert notify._email_md_inline("plain text") == "plain text"


# ─── _email_section ───────────────────────────────────────────────────────────

def test_email_section_falsy_content_returns_empty_string():
    assert notify._email_section("Title", "", {}) == ""
    assert notify._email_section("Title", None, {}) == ""


def test_email_section_renders_title_and_paragraph():
    result = notify._email_section("What happened", "Some content here.", {"What happened": "#3b82f6"})
    assert "What happened" in result
    assert "Some content here." in result
    assert "#3b82f6" in result


def test_email_section_renders_bullet_list():
    result = notify._email_section("Patterns", "• first item\n• second item", {})
    assert "<ul" in result
    assert "<li" in result
    assert "first item" in result
    assert "second item" in result


# ─── render_debrief_email — inner _pct_cell via public surface ──────────────

def _debrief(perf=5.0, spy=2.0, alpha=3.0):
    return {
        "week_ending": "2026-01-17", "generated_at": "2026-01-17T00:00:00",
        "performance_pct": perf, "spy_pct": spy, "alpha_pct": alpha,
        "section_facts": "Facts here.", "section_decisions": "",
        "section_patterns": "", "section_watchnext": "",
    }


def test_render_debrief_email_pct_cell_normal_positive_value():
    html = notify.render_debrief_email(_debrief(perf=5.0))
    assert "+5.0%" in html
    assert "#16a34a" in html  # green for positive


def test_render_debrief_email_pct_cell_normal_negative_value():
    html = notify.render_debrief_email(_debrief(perf=-3.0))
    assert "-3.0%" in html
    assert "#dc2626" in html  # red for negative


def test_render_debrief_email_pct_cell_none_renders_na():
    # perf itself gates whether the whole tile row renders at all (None perf
    # -> the row is omitted entirely, not rendered with N/A cells) -- so to
    # exercise _pct_cell's None branch we need perf present and spy/alpha None.
    html = notify.render_debrief_email(_debrief(perf=5.0, spy=None, alpha=None))
    assert "N/A" in html


def test_render_debrief_email_pct_cell_nan_renders_na_not_nan():
    html = notify.render_debrief_email(_debrief(perf=float("nan")))
    assert "nan" not in html.lower() or "N/A" in html
    assert "N/A" in html


def test_render_debrief_email_pct_cell_zero_boundary_is_not_none():
    # 0.0 is falsy but must NOT be treated as missing -- v >= 0 -> green, "+0.0%".
    html = notify.render_debrief_email(_debrief(perf=0.0))
    assert "+0.0%" in html
    assert "#16a34a" in html


def test_render_debrief_email_week_had_trades_shows_warning():
    html = notify.render_debrief_email(_debrief(), week_had_trades=True)
    assert "Trades occurred this week" in html


# ─── render_intelligence_email — inner _alpha_html (NaN guard) ──────────────

def _report(engine_alpha=5.0, acted=3, missed=1):
    return {
        "period_start": "2026-01-01", "period_end": "2026-01-31",
        "generated_at": "2026-02-01", "engine_alpha_pct": engine_alpha,
        "acted_count": acted, "missed_count": missed,
        "section_entry_quality": "Entry text.", "section_signal_discipline": "",
        "section_patterns": "",
    }


def test_render_intelligence_email_alpha_html_none_renders_na():
    html = notify.render_intelligence_email(_report(engine_alpha=None))
    assert "N/A" in html


def test_render_intelligence_email_alpha_html_nan_renders_na_not_nan():
    # This is the bonus fix under test: NaN must render as N/A, not "+nan%".
    html = notify.render_intelligence_email(_report(engine_alpha=float("nan")))
    assert "+nan%" not in html
    assert "N/A" in html


def test_render_intelligence_email_alpha_html_normal_value():
    html = notify.render_intelligence_email(_report(engine_alpha=7.5))
    assert "+7.5%" in html
    assert "#16a34a" in html


def test_render_intelligence_email_alpha_html_negative_value():
    html = notify.render_intelligence_email(_report(engine_alpha=-2.5))
    assert "-2.5%" in html
    assert "#dc2626" in html


# ─── send_email_resend ───────────────────────────────────────────────────────

def test_send_email_resend_missing_api_key():
    ok, detail = notify.send_email_resend(api_key="", sender="a@b.com", to="c@d.com",
                                           subject="s", html="h")
    assert ok is False
    assert "no api_key" in detail


def test_send_email_resend_missing_sender():
    ok, detail = notify.send_email_resend(api_key="key", sender="", to="c@d.com",
                                           subject="s", html="h")
    assert ok is False
    assert "no sender" in detail


def test_send_email_resend_missing_recipient():
    ok, detail = notify.send_email_resend(api_key="key", sender="a@b.com", to="",
                                           subject="s", html="h")
    assert ok is False
    assert "no recipient" in detail


@patch("stock_analyzer.notify.requests.post")
def test_send_email_resend_mocked_success(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_post.return_value = mock_resp
    ok, detail = notify.send_email_resend(api_key="key", sender="a@b.com", to="c@d.com",
                                           subject="s", html="h")
    assert ok is True
    assert detail == ""
    mock_post.assert_called_once()


@patch("stock_analyzer.notify.requests.post")
def test_send_email_resend_mocked_failure(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 422
    mock_resp.text = "Invalid sender domain"
    mock_post.return_value = mock_resp
    ok, detail = notify.send_email_resend(api_key="key", sender="a@b.com", to="c@d.com",
                                           subject="s", html="h")
    assert ok is False
    assert "422" in detail
    assert "Invalid sender domain" in detail
