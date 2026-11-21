"""#34c C1: the pure capture of SnapTrade's connection / refresh health
(`stock_analyzer/broker_connection.py`, docs/plans/broker-connection-alarm-34c.md).

Pins the P1 probe's real shape (2026-11-21): healthy accounts return
`holdings_unavailable=None`, and that None must read as AVAILABLE; the holdings
stamp is a UTC instant; the transactions stamp is a date-only UTC date."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from stock_analyzer import broker_connection as bc

pytestmark = pytest.mark.fast

_UTC = timezone.utc
_STAMP = "2026-11-20T22:52:17.376190+00:00"


def _acct(stamp=_STAMP, unavailable=None, txn="2026-11-19", acct_id="acct-id-1234", number="RH12345678",
          auth="auth-1"):
    return {
        "id": acct_id,
        "number": number,
        "brokerage_authorization": auth,
        "name": "Individual",
        "sync_status": {
            "holdings": {"initial_sync_completed": True, "holdings_unavailable": unavailable,
                         "last_successful_sync": stamp},
            "transactions": {"initial_sync_completed": True, "last_successful_sync": txn},
        },
    }


def _conn(disabled=False, disabled_date=None, mode=None, conn_id="auth-1"):
    return {"id": conn_id, "disabled": disabled, "disabled_date": disabled_date,
            "data_freshness_mode": mode if mode is not None else {"holdings": "realtime", "transactions": "realtime"}}


def _summ(accounts, connections, **kw):
    base = dict(
        accounts=accounts, connections=connections,
        run_started_at=datetime(2026, 11, 21, 11, 15, tzinfo=_UTC),
        checked_at=datetime(2026, 11, 21, 11, 16, tzinfo=_UTC),
        positions_read_ok=True, snapshot_saved=True, positions_sig="abc", error=None,
    )
    base.update(kw)
    return bc.summarize_status(**base)


# ── parse_holdings_stamp ────────────────────────────────────────────────────
def test_stamp_offset_iso_converted_to_utc():
    got = bc.parse_holdings_stamp("2026-11-19T20:22:00-05:00")
    assert got == datetime(2026, 11, 20, 1, 22, tzinfo=_UTC)
    assert got.utcoffset() == timedelta(0)


def test_stamp_z_suffix_and_p1_value():
    assert bc.parse_holdings_stamp(_STAMP) == datetime(2026, 11, 20, 22, 52, 17, 376190, tzinfo=_UTC)
    assert bc.parse_holdings_stamp("2026-11-20T22:52:17Z") == datetime(2026, 11, 20, 22, 52, 17, tzinfo=_UTC)


def test_stamp_naive_is_read_as_utc():
    assert bc.parse_holdings_stamp("2026-11-20T22:52:17") == datetime(2026, 11, 20, 22, 52, 17, tzinfo=_UTC)
    assert bc.parse_holdings_stamp(datetime(2026, 11, 20, 22, 52)) == datetime(2026, 11, 20, 22, 52, tzinfo=_UTC)


def test_stamp_aware_datetime_converted():
    est = timezone(timedelta(hours=-5))
    assert bc.parse_holdings_stamp(datetime(2026, 11, 20, 17, 52, tzinfo=est)) == \
        datetime(2026, 11, 20, 22, 52, tzinfo=_UTC)


def test_stamp_compact_date_only_is_none_too():
    assert bc.parse_holdings_stamp("20261120") is None
    assert bc.parse_holdings_stamp("2026-1120") is None


def test_stamp_date_only_is_none_never_an_invented_midnight():
    assert bc.parse_holdings_stamp("2026-11-19") is None
    assert bc.parse_holdings_stamp(date(2026, 11, 19)) is None


@pytest.mark.parametrize("junk", [None, "", "  ", "not a date", float("nan"), 12345, True, [], {}, object()])
def test_stamp_junk_is_none(junk):
    assert bc.parse_holdings_stamp(junk) is None


# ── parse_utc_date ──────────────────────────────────────────────────────────
def test_utc_date_accepts_the_compact_form_as_that_date():
    assert bc.parse_utc_date("20261119") == date(2026, 11, 19)
    assert bc.parse_utc_date("20261399") is None            # not a real date: junk, never raises


def test_utc_date_from_string_and_date():
    assert bc.parse_utc_date("2026-11-19") == date(2026, 11, 19)
    assert bc.parse_utc_date(date(2026, 11, 19)) == date(2026, 11, 19)


def test_utc_date_from_aware_datetime_is_the_utc_date_not_et():
    assert bc.parse_utc_date(datetime(2026, 11, 20, 1, 22, tzinfo=_UTC)) == date(2026, 11, 20)
    # 20:22 EST on the 19th is 01:22 UTC on the 20th: proves UTC, not ET.
    est = timezone(timedelta(hours=-5))
    assert bc.parse_utc_date(datetime(2026, 11, 19, 20, 22, tzinfo=est)) == date(2026, 11, 20)
    assert bc.parse_utc_date("2026-11-19T20:22:00-05:00") == date(2026, 11, 20)


def test_utc_date_naive_datetime_is_read_as_utc():
    assert bc.parse_utc_date(datetime(2026, 11, 19, 23, 30)) == date(2026, 11, 19)


@pytest.mark.parametrize("junk", [None, "", "garbage", float("nan"), 7, True, object()])
def test_utc_date_junk_is_none(junk):
    assert bc.parse_utc_date(junk) is None


# ── positions_sig ───────────────────────────────────────────────────────────
def test_sig_is_order_independent():
    a = {"DELL": 20.0, "NVDA": 5.0, "AAPL": 1.5}
    b = {"AAPL": 1.5, "NVDA": 5.0, "DELL": 20.0}
    assert bc.positions_sig(a) == bc.positions_sig(b)


def test_sig_ignores_float_noise_below_rounding():
    assert bc.positions_sig({"DELL": 20.0}) == bc.positions_sig({"DELL": 20.0 + 1e-10})
    assert bc.positions_sig({"DELL": 0.0}) == bc.positions_sig({"DELL": -0.0})


def test_sig_changes_with_a_quantity_or_ticker():
    base = bc.positions_sig({"DELL": 20.0, "NVDA": 5.0})
    assert bc.positions_sig({"DELL": 21.0, "NVDA": 5.0}) != base
    assert bc.positions_sig({"DELL": 20.0, "NVDX": 5.0}) != base
    assert bc.positions_sig({"DELL": 20.0}) != base


def test_sig_none_and_empty():
    assert bc.positions_sig(None) is None
    empty = bc.positions_sig({})
    assert isinstance(empty, str) and len(empty) == 16
    assert empty == bc.positions_sig({})
    assert empty != bc.positions_sig({"DELL": 1.0})


# ── summarize_status ────────────────────────────────────────────────────────
def test_p1_real_shape_reads_healthy():
    accts = [_acct(acct_id=f"a{i}") for i in range(5)]
    row = _summ(accts, [_conn()])
    assert row["any_holdings_unavailable"] is False        # None (healthy) counts as available
    assert row["any_disabled"] is False
    assert row["n_disabled"] == 0 and row["any_linked_disabled"] is False
    assert row["disabled_date"] is None
    assert row["holdings_refreshed_oldest"] == "2026-11-20T22:52:17.376190+00:00"
    assert row["holdings_refreshed_newest"] == "2026-11-20T22:52:17.376190+00:00"
    assert row["holdings_stamp_missing"] == 0
    assert row["txn_refreshed_utc_date"] == "2026-11-19"
    assert row["n_accounts"] == 5 and row["n_connections"] == 1
    assert row["accounts_ok"] is True and row["connections_ok"] is True
    assert row["freshness_modes"] == [{"holdings": "realtime", "transactions": "realtime"}]
    assert row["positions_read_ok"] is True and row["snapshot_saved"] is True
    assert row["positions_sig"] == "abc"
    assert row["error"] is None


def test_oldest_and_newest_stamps_and_oldest_txn_date():
    accts = [
        _acct("2026-11-20T22:52:00+00:00", txn="2026-11-19"),
        _acct("2026-11-18T20:22:00-05:00", txn="2026-11-17"),   # 2026-11-19 01:22 UTC
        _acct("2026-11-20T01:00:00", txn="2026-11-18"),         # naive = UTC
    ]
    row = _summ(accts, [_conn()])
    assert row["holdings_refreshed_oldest"] == "2026-11-19T01:22:00+00:00"
    assert row["holdings_refreshed_newest"] == "2026-11-20T22:52:00+00:00"
    assert row["txn_refreshed_utc_date"] == "2026-11-17"


def test_holdings_unavailable_true_flags_and_string_true_too():
    assert _summ([_acct(), _acct(unavailable=True)], [_conn()])["any_holdings_unavailable"] is True
    assert _summ([_acct(unavailable="TRUE")], [_conn()])["any_holdings_unavailable"] is True
    assert _summ([_acct(unavailable=False)], [_conn()])["any_holdings_unavailable"] is False
    assert _summ([_acct(unavailable="false")], [_conn()])["any_holdings_unavailable"] is False


def test_disabled_true_and_string_true_with_date():
    row = _summ([_acct()], [_conn(), _conn(disabled="true", disabled_date="2026-11-10T14:00:00Z", conn_id="other")])
    assert row["any_disabled"] is True
    assert row["n_disabled"] == 1
    assert row["disabled_date"] == "2026-11-10T14:00:00Z"
    assert _summ([_acct()], [_conn(disabled=True)])["any_disabled"] is True
    assert _summ([_acct()], [_conn(disabled="false")])["any_disabled"] is False


def test_old_disabled_authorization_left_by_a_reconnect_is_not_a_linked_disable():
    """NB1: the live connection is ours (accounts point at it); an OLD disabled one is not."""
    conns = [_conn(conn_id="old-dead", disabled=True, disabled_date="2026-10-01"), _conn(conn_id="auth-1")]
    row = _summ([_acct(auth="auth-1")], conns)
    assert row["any_disabled"] is True
    assert row["n_disabled"] == 1
    assert row["any_linked_disabled"] is False
    assert row["disabled_date"] == "2026-10-01"        # no linked one: the first disabled one's date


def test_a_disabled_authorization_our_account_is_attached_to_is_linked_with_its_date():
    conns = [_conn(conn_id="old-dead", disabled=True, disabled_date="2026-10-01"),
             _conn(conn_id="auth-1", disabled=True, disabled_date="2026-11-18"),
             _conn(conn_id="auth-2", disabled="TRUE", disabled_date="2026-11-19")]
    row = _summ([_acct(auth="auth-1"), _acct(auth="auth-2")], conns)
    assert row["any_linked_disabled"] is True
    assert row["n_disabled"] == 3
    assert row["disabled_date"] == "2026-11-18"        # the first LINKED disabled one, not the first disabled


def test_linked_disabled_is_none_when_either_list_could_not_be_read():
    assert _summ([_acct()], None)["any_linked_disabled"] is None
    assert _summ(None, [_conn(disabled=True)])["any_linked_disabled"] is None
    assert _summ(None, [_conn(disabled=True)])["any_disabled"] is True       # that field is unchanged
    assert _summ([_acct()], None)["n_disabled"] is None


def test_linked_disabled_is_none_not_false_when_accounts_carry_no_authorization_reference():
    row = _summ([_acct(auth=None)], [_conn(disabled=True)])
    assert row["any_linked_disabled"] is None
    assert row["any_disabled"] is True
    # no accounts linked at all is a real "none match"
    assert _summ([], [_conn(disabled=True)])["any_linked_disabled"] is False


def test_authorization_reference_given_as_an_object_with_an_id_still_links():
    acct = _acct()
    acct["brokerage_authorization"] = {"id": "auth-1", "name": "Robinhood"}
    assert _summ([acct], [_conn(disabled=True)])["any_linked_disabled"] is True


def test_ok_flags_are_derived_from_whether_each_payload_was_a_list():
    row = _summ({"results": []}, {"oops": 1})            # a non-list body is not "read"
    assert row["accounts_ok"] is False and row["connections_ok"] is False
    assert row["n_accounts"] is None and row["n_connections"] is None
    assert row["any_disabled"] is None and row["any_holdings_unavailable"] is None
    row = _summ([], [])
    assert row["accounts_ok"] is True and row["connections_ok"] is True


def test_connections_could_not_be_read_is_none_not_false():
    row = _summ([_acct()], None)
    assert row["any_disabled"] is None
    assert row["disabled_date"] is None
    assert row["n_connections"] is None
    assert row["freshness_modes"] is None
    assert row["connections_ok"] is False
    assert row["any_holdings_unavailable"] is False        # accounts were read fine


def test_accounts_could_not_be_read_is_none_not_false():
    row = _summ(None, [_conn()], error="SnapTrade unreachable")
    assert row["any_holdings_unavailable"] is None
    assert row["n_accounts"] is None
    assert row["holdings_stamp_missing"] is None
    assert row["holdings_refreshed_oldest"] is None and row["holdings_refreshed_newest"] is None
    assert row["txn_refreshed_utc_date"] is None
    assert row["accounts_ok"] is False
    assert row["any_disabled"] is False                    # connections were read fine
    assert row["error"] == "SnapTrade unreachable"


def test_no_accounts_linked_is_zero_not_none():
    row = _summ([], [_conn()])
    assert row["n_accounts"] == 0
    assert row["any_holdings_unavailable"] is False
    assert row["holdings_stamp_missing"] == 0


def test_date_only_and_missing_holdings_stamps_count_as_missing():
    accts = [_acct("2026-11-19"), _acct(None), _acct("garbage"), _acct()]
    row = _summ(accts, [_conn()])
    assert row["holdings_stamp_missing"] == 3
    assert row["holdings_refreshed_oldest"] == "2026-11-20T22:52:17.376190+00:00"


def test_row_is_json_serialisable_with_datetimes_dates_and_odd_modes():
    accts = [_acct(datetime(2026, 11, 20, 22, 0, tzinfo=_UTC), txn=date(2026, 11, 19))]
    conns = [_conn(mode={"holdings": datetime(2026, 11, 1, tzinfo=_UTC), "n": float("nan"), "t": (1, 2)},
                   disabled_date=datetime(2026, 11, 1, tzinfo=_UTC), disabled=True)]
    row = _summ(accts, conns)
    json.dumps(row)
    assert row["run_started_at"].endswith("+00:00") and row["checked_at"].endswith("+00:00")


def test_no_id_or_account_number_anywhere_in_the_row():
    row = _summ([_acct(acct_id="SECRET-ACCT-ID", number="RH99887766")],
                [_conn(conn_id="SECRET-CONN-ID")],
                error="failed for SECRET-ACCT-ID")
    blob = json.dumps(row)
    assert "SECRET-CONN-ID" not in blob
    assert "RH99887766" not in blob
    assert "99887766" not in blob


def test_error_is_scrubbed_and_capped(monkeypatch):
    monkeypatch.setenv("SNAPTRADE_CLIENT_ID", "client-secret-xyz")
    monkeypatch.setenv("SNAPTRADE_CONSUMER_KEY", "consumer-secret-abc")
    err = ("Invalid clientId provided - client-secret-xyz key consumer-secret-abc "
           "account 123456789 id 123e4567-e89b-12d3-a456-426614174000 " + "x" * 400)
    row = _summ([_acct()], [_conn()], error=err)
    out = row["error"]
    assert len(out) <= bc.ERROR_MAX_CHARS
    for leak in ("client-secret-xyz", "consumer-secret-abc", "123456789", "123e4567"):
        assert leak not in out
    assert _summ([_acct()], [_conn()], error="   ")["error"] is None


def test_scrub_error_is_public_and_handles_exceptions():
    out = bc.scrub_error(RuntimeError("boom for 123e4567-e89b-12d3-a456-426614174000"))
    assert out == "boom for [id]"
    assert bc.scrub_error(None) is None


def test_sdk_style_objects_without_get_are_read_by_attribute():
    class _O:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    acct = _O(sync_status=_O(holdings=_O(holdings_unavailable=True, last_successful_sync=_STAMP),
                             transactions=_O(last_successful_sync="2026-11-19")))
    conn = _O(disabled=True, disabled_date="2026-11-01", data_freshness_mode="realtime")
    row = _summ([acct], [conn])
    assert row["any_holdings_unavailable"] is True
    assert row["any_disabled"] is True
    assert row["freshness_modes"] == ["realtime"]
    assert row["txn_refreshed_utc_date"] == "2026-11-19"


@pytest.mark.parametrize("accounts,connections", [
    ("junk", "junk"), (5, 5), ({"a": 1}, {"b": 2}), ([None, 3, "x", float("nan")], [None, 3, "x"]),
    ([{"sync_status": "oops"}], [{"disabled": object()}]), (object(), object()),
])
def test_junk_inputs_never_raise_and_stay_serialisable(accounts, connections):
    row = bc.summarize_status(
        accounts=accounts, connections=connections, run_started_at="junk", checked_at=None,
        positions_read_ok=None, snapshot_saved=None, positions_sig=12345, error=object())
    json.dumps(row)
    assert row["positions_sig"] is None
    assert isinstance(row["checked_at"], str)


def test_log_summary_shows_oldest_stamp_in_et_and_never_raises():
    row = _summ([_acct("2026-11-20T22:52:17+00:00")], [_conn()])
    out = bc.log_summary(row)
    assert "accounts=1" in out and "any_linked_disabled=False" in out and "snapshot_saved=True" in out
    assert "Fri Nov 20 05:52 PM ET" in out
    assert bc.log_summary(None) == "accounts=None any_linked_disabled=None oldest_refresh=n/a snapshot_saved=None"
