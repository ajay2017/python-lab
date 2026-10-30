"""Test helper: a `held_stops` mapping for `build_portfolio_df(..., held_stops=)`.

`build_portfolio_df` requires the keyword (finding #26 C3) and no longer derives
a stop from `loaded_data[t]["stop"]`. Tests that only need SOME stop pass
`hs_for(loaded)`: each ticker's bundle stop becomes a full-basis "Entry Stop"
(never a default inside the function under test)."""


def hs_for(loaded: dict, label: str = "Entry Stop") -> dict:
    out = {}
    for t, r in (loaded or {}).items():
        stop = r.get("stop") if isinstance(r, dict) else None
        try:
            ok = stop is not None and float(stop) > 0
        except (TypeError, ValueError):
            ok = False
        if ok:
            out[t] = {"ticker": t, "stop": round(float(stop), 2), "basis": "full",
                      "label": label, "binding": "entry", "note": "", "since": None,
                      "components": {"entry": float(stop), "trailing": None, "ratchet": None},
                      "atr_last": None, "hw_close": None, "ratchet_label": None}
    return out
