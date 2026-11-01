"""The App Settings model choice for ``llm.DECISION_ROLES`` (score, filter,
extract_trigger).

One ``reference_tables`` row named ``llm_models_decision`` (docs/plans/
llm-model-routing.md section 17), separate from ``llm_models`` so a broken row
in either never touches the other tier. A process-level immutable snapshot of it
and the provider ``llm.complete`` consults for a decision role. No Streamlit
import. Env is never read for these roles (``llm.resolve_model_with_source``).

Inert until a row exists: no row, an unreadable table, or an invalid row all
resolve to ``llm.CODE_DEFAULTS`` (owner decision U4); the state stays readable
through ``get_snapshot().state`` (alias ``.status``) for System Trust.

A model other than the reviewed code default needs a stored passing eval
(``eval_id`` + ``criteria_version``). The score role also carries a date key
(``effective_from``, a ``score_date``) so a switch never splits one day's cached
scores across two models.

Stale-if-error: when a reload comes back UNREADABLE (a transient DB failure) and
the previous snapshot was OK, the previous OK snapshot keeps being served, marked
``stale=True`` with ``WARN_STALE`` added, and the read is retried after the short
unreadable interval. That stops a blip from flipping a day's score model to the
code default mid-day. With no previous OK snapshot an unreadable read still
leads to code defaults (U4). INVALID always replaces the snapshot: a bad saved
row must not be masked by an older good one.
"""
from __future__ import annotations

import math
import re
import threading
import time
import types
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone

from stock_analyzer import llm, llm_settings, reference_data, sentiment_cache

TABLE_NAME = "llm_models_decision"
PAYLOAD_VERSION = 1
_TTL_SEC = 300
_UNREADABLE_TTL_SEC = 30

STATE_OK = llm_settings.STATE_OK
STATE_NO_ROW = llm_settings.STATE_NO_ROW
STATE_UNREADABLE = llm_settings.STATE_UNREADABLE
STATE_INVALID = llm_settings.STATE_INVALID

_BASE_KEYS = frozenset({"model", "effort", "capabilities", "checked_at"})
_COMMON_KEYS = _BASE_KEYS | {"eval_id", "criteria_version"}
_SCORE_KEYS = _COMMON_KEYS | {"effective_from", "prior", "cumulative_shift_pts",
                              "cumulative_before"}
_CUM_KEYS = ("cumulative_shift_pts", "cumulative_before")
_PRIOR_KEYS = frozenset({"model", "effort", "capabilities"})
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

WARN_UNREADABLE = ("AI decision-model settings could not be read; "
                   "code defaults in use")
WARN_STALE = ("AI decision-model settings could not be re-read; "
              "serving the last good copy")
_WARN_HASH = ("AI decision-model settings row doesn't match its saved hash "
              "(edited outside App Settings?); code defaults in use")

# Reuse the llm_models hash view (checked_at excluded) and canonical form;
# llm_settings.canonicalize itself is untouched so llm_models hashing stays
# byte-identical.
hash_view = llm_settings.hash_view


def _norm_cumulative(payload):
    """cumulative_shift_pts / cumulative_before -> ``float(x) + 0.0`` so -0.0
    hashes as 0.0."""
    roles = payload.get("roles") if isinstance(payload, dict) else None
    if not isinstance(roles, dict):
        return payload
    out_roles = {}
    for r, e in roles.items():
        if isinstance(e, dict):
            for key in _CUM_KEYS:
                if key not in e:
                    continue
                v = e[key]
                if (isinstance(v, (int, float)) and not isinstance(v, bool)
                        and math.isfinite(v)):
                    e = {**e, key: float(v) + 0.0}
        out_roles[r] = e
    return {**payload, "roles": out_roles}


def canonicalize(payload):
    """``llm_settings.canonicalize`` plus -0.0 -> 0.0 for ``cumulative_shift_pts`` / ``cumulative_before``."""
    return _norm_cumulative(llm_settings.canonicalize(payload))


# --- Pure: validation ---------------------------------------------------------
def _allowed_keys(role) -> frozenset:
    return _SCORE_KEYS if role == llm.ROLE_SCORE else _COMMON_KEYS


def _base_errors(entry: dict) -> list[str]:
    """The llm_models entry rules (model id, effort, capabilities, checked_at)
    applied to the shared keys of ``entry``."""
    base = {k: v for k, v in entry.items() if k in _BASE_KEYS}
    return llm_settings.validate_role_entry(llm.ROLE_FAST, base)


def _is_int(v, minimum: int) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= minimum


def _valid_date(v) -> bool:
    if not (isinstance(v, str) and _DATE_RE.match(v)):
        return False
    try:
        date.fromisoformat(v)
        return True
    except ValueError:
        return False


def validate_role_entry(role, entry) -> list[str]:
    errors: list[str] = []
    if role not in llm.DECISION_ROLES:
        errors.append(f"role {role!r} is not a decision role "
                      f"(decision roles: {sorted(llm.DECISION_ROLES)})")
        return errors
    if not isinstance(entry, dict):
        return ["entry must be an object"]
    extra = sorted(str(k) for k in entry if k not in _allowed_keys(role))
    if extra:
        errors.append(f"unexpected key(s): {extra}")
    errors.extend(_base_errors(entry))

    model = entry.get("model")
    at_default = (isinstance(model, str)
                  and model.strip() == llm.CODE_DEFAULTS[role])
    for key, ok in (("eval_id", lambda v: _is_int(v, 1)),
                    ("criteria_version", lambda v: _is_int(v, 1))):
        if key in entry and entry[key] is not None:
            if not ok(entry[key]):
                errors.append(f"{key} must be a positive int")
        elif not at_default:
            errors.append(f"{key} is required unless the model is the code "
                          f"default ({llm.CODE_DEFAULTS[role]})")

    if role == llm.ROLE_SCORE:
        if not _valid_date(entry.get("effective_from")):
            errors.append("effective_from must be an ISO date (YYYY-MM-DD)")
        prior = entry.get("prior")
        if prior is not None:
            if not isinstance(prior, dict):
                errors.append("prior must be None or an object")
            else:
                pextra = sorted(str(k) for k in prior if k not in _PRIOR_KEYS)
                if pextra:
                    errors.append(f"prior has unexpected key(s): {pextra}")
                errors.extend(f"prior: {e}" for e in _base_errors(prior))
        cum = entry.get("cumulative_shift_pts", 0.0)
        if (isinstance(cum, bool) or not isinstance(cum, (int, float))
                or not math.isfinite(cum)):
            errors.append("cumulative_shift_pts must be a finite number")
        if "cumulative_before" in entry:
            cb = entry["cumulative_before"]
            if (isinstance(cb, bool) or not isinstance(cb, (int, float))
                    or not math.isfinite(cb)):
                errors.append("cumulative_before must be a finite number")
    return errors


def _top_level_errors(payload) -> list[str]:
    if not isinstance(payload, dict):
        return ["payload must be an object"]
    errors: list[str] = []
    extra = sorted(str(k) for k in payload if k not in ("version", "roles"))
    if extra:
        errors.append(f"unexpected key(s): {extra}")
    version = payload.get("version")
    if not (type(version) is int and version == PAYLOAD_VERSION):
        errors.append(f"version must be {PAYLOAD_VERSION}, not {version!r}")
    roles = payload.get("roles")
    if not isinstance(roles, dict):
        errors.append("roles must be an object")
        return errors
    for role, entry in roles.items():
        if role not in llm.DECISION_ROLES:
            errors.append(f"role {role!r} is not a decision role "
                          f"(decision roles: {sorted(llm.DECISION_ROLES)})")
        elif isinstance(entry, dict):
            bad = sorted(str(k) for k in entry if k not in _allowed_keys(role))
            if bad:
                errors.append(f"{role}: unexpected key(s): {bad}")
    return errors


def validate_payload(payload) -> list[str]:
    """Validate the canonical form, so passing on save equals passing on load."""
    try:
        payload = canonicalize(payload)
    except Exception as e:  # noqa: BLE001
        return [f"payload could not be normalised ({type(e).__name__})"]
    errors = _top_level_errors(payload)
    roles = payload.get("roles") if isinstance(payload, dict) else None
    if isinstance(roles, dict):
        for role, entry in roles.items():
            if role in llm.DECISION_ROLES:
                errors.extend(f"{role}: {e}" for e in validate_role_entry(role, entry))
    return errors


# --- Pure: score date key -----------------------------------------------------
def score_effective_from(now_utc: datetime) -> str:
    """The first ``score_date`` a score switch saved at ``now_utc`` applies to:
    the next UTC date (D+1), or D+2 when 300 s (the snapshot TTL) or less remain
    before 00:00 UTC, because another process may still hold the old snapshot."""
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    now_utc = now_utc.astimezone(timezone.utc)
    d = now_utc.date()
    midnight = datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(days=1)
    remaining = (midnight - now_utc).total_seconds()
    return (d + timedelta(days=2 if remaining <= _TTL_SEC else 1)).isoformat()


def _to_setting(e: dict) -> llm.RoleSetting:
    caps = e.get("capabilities")
    return llm.RoleSetting(e["model"], e.get("effort"),
                           types.MappingProxyType(dict(caps)) if caps else None)


def score_entry_for(entry: dict, score_date: str) -> llm.RoleSetting | None:
    """The score setting for ``score_date``: the entry from its
    ``effective_from``, else ``prior``, else None (the code default)."""
    if score_date >= entry["effective_from"]:
        return _to_setting(entry)
    prior = entry.get("prior")
    return _to_setting(prior) if isinstance(prior, dict) else None


def _finite_float(v, default: float = 0.0) -> float:
    ok = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    return float(v) if ok else default


def score_cumulative_for(entry, score_date: str) -> float:
    """The cumulative score shift (pts) of the model that resolves on
    ``score_date``: 0.0 with no entry; ``cumulative_shift_pts`` from the entry's
    ``effective_from``; before it, ``cumulative_before`` (the total of the model
    that resolved when the entry was saved), else ``cumulative_shift_pts``."""
    if not isinstance(entry, Mapping):
        return 0.0
    cum = _finite_float(entry.get("cumulative_shift_pts"))
    if score_date >= str(entry.get("effective_from")):
        return cum
    if "cumulative_before" in entry:
        return _finite_float(entry.get("cumulative_before"), cum)
    return cum


# --- Snapshot -----------------------------------------------------------------
@dataclass(frozen=True)
class Snapshot:
    state: str
    entries: Mapping = field(default_factory=lambda: types.MappingProxyType({}))
    warnings: tuple = ()
    as_of: object = None
    updated_by: object = None
    stale: bool = False   # an OK snapshot kept after a failed reload
    # ISO wall-clock UTC of the first failed reload while an OK snapshot is being
    # served stale; carried across further stale reloads, None once a reload works.
    stale_since: str | None = None

    @property
    def status(self) -> str:
        return self.state


def _empty() -> Mapping:
    return types.MappingProxyType({})


def build_snapshot(status, row) -> Snapshot:
    if status == STATE_UNREADABLE:
        return Snapshot(STATE_UNREADABLE, _empty(), (WARN_UNREADABLE,))
    if status == STATE_NO_ROW or status != STATE_OK or not isinstance(row, dict):
        return Snapshot(STATE_NO_ROW if status == STATE_NO_ROW else STATE_UNREADABLE,
                        _empty(),
                        () if status == STATE_NO_ROW else (WARN_UNREADABLE,))
    as_of, updated_by = row.get("as_of"), row.get("updated_by")
    payload = row.get("payload")
    try:
        recomputed = reference_data.payload_hash_for(TABLE_NAME, canonicalize(payload))
    except Exception:  # noqa: BLE001 - an unhashable payload is a mismatch
        return Snapshot(STATE_INVALID, _empty(), (_WARN_HASH,), as_of, updated_by)
    if recomputed != row.get("payload_hash"):
        return Snapshot(STATE_INVALID, _empty(), (_WARN_HASH,), as_of, updated_by)
    payload = canonicalize(payload)
    top = _top_level_errors(payload)
    if top:
        return Snapshot(STATE_INVALID, _empty(),
                        (f"AI decision-model settings invalid ({'; '.join(top)}); "
                         "code defaults in use",), as_of, updated_by)
    entries: dict = {}
    warnings: list[str] = []
    for role, entry in payload["roles"].items():
        errs = validate_role_entry(role, entry)
        if errs:
            warnings.append(f"{role}: {errs}; code default in use")
            continue
        entries[role] = entry
    return Snapshot(STATE_OK, types.MappingProxyType(entries), tuple(warnings),
                    as_of, updated_by)


# --- Save-payload helpers (used by the App Settings section, chunk C5) --------
def _roles_copy(payload) -> dict:
    roles = payload.get("roles") if isinstance(payload, dict) else None
    return dict(roles) if isinstance(roles, dict) else {}


def apply_role(payload, role, entry) -> dict:
    roles = _roles_copy(payload)
    roles[role] = entry
    return {"version": PAYLOAD_VERSION, "roles": roles}


def reset_role(payload, role, now_utc: datetime) -> dict:
    """Back to the code default. filter / extract_trigger: the entry is removed.
    score: date-keyed, so a new entry at ``CODE_DEFAULTS["score"]`` that takes
    effect from ``score_effective_from(now_utc)``. Its ``prior`` is the model that
    resolves TODAY (``score_entry_for(current, <UTC date of now_utc>)``; None = the
    code default), never a pending not-yet-effective entry, so a reset can't start a
    pending model early. ``cumulative_before`` is ``score_cumulative_for(current,
    today)``, the total of what resolves today (correct while the reset is pending).
    ``cumulative_shift_pts`` (the total once the reset applies) is 0.0 when the code
    default IS the drift origin (``sentiment_cache.LEGACY_CACHE_MODEL``, Haiku 4.5,
    which every total is measured from), else that same carried total.
    TODO(C8): once CODE_DEFAULTS["score"] moves off the drift origin, a reset needs
    the moved default's own total (e.g. a reviewed constant) instead of the carry."""
    roles = _roles_copy(payload)
    if role != llm.ROLE_SCORE:
        roles.pop(role, None)
        return {"version": PAYLOAD_VERSION, "roles": roles}
    current = roles.get(role)
    entry = {
        "model": llm.CODE_DEFAULTS[llm.ROLE_SCORE],
        "effort": None,
        "capabilities": None,
        "checked_at": None,
        "effective_from": score_effective_from(now_utc),
        "prior": None,
        "cumulative_shift_pts": 0.0,
        "cumulative_before": 0.0,
    }
    if isinstance(current, dict):
        # prior = what the current entry resolves to TODAY (UTC, the
        # sentiment_cache.score_date_today key), never a not-yet-effective entry.
        today = (now_utc.replace(tzinfo=timezone.utc) if now_utc.tzinfo is None
                 else now_utc.astimezone(timezone.utc)).date().isoformat()
        try:
            rs = score_entry_for(current, today)
        except Exception:  # noqa: BLE001 - a malformed current entry: code default
            rs = None
        if rs is not None:
            entry["prior"] = {
                "model": rs.model, "effort": rs.effort,
                "capabilities": dict(rs.capabilities) if rs.capabilities else None}
        try:
            cum = score_cumulative_for(current, today) + 0.0
        except Exception:  # noqa: BLE001
            cum = 0.0
        entry["cumulative_before"] = cum
        entry["cumulative_shift_pts"] = (
            0.0 if llm.CODE_DEFAULTS[llm.ROLE_SCORE] == sentiment_cache.LEGACY_CACHE_MODEL
            else cum)
    roles[role] = entry
    return {"version": PAYLOAD_VERSION, "roles": roles}


# --- Process state (the only mutable state; llm.py stays free of it) ----------
_lock = threading.Lock()
_reload_lock = threading.Lock()   # single-flight: one thread reads the DB at a time
_cached: Snapshot | None = None
_expires_at: float = 0.0
_gen = 0                          # bumped by invalidate(); a stale reload isn't stored


def _default_loader():
    from stock_analyzer import db
    return db.load_reference_table_status(TABLE_NAME)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_snapshot(*, loader=None, clock=time.monotonic, wall=_utc_now_iso) -> Snapshot:
    """The cached snapshot while fresh. On expiry one thread reloads (the DB
    read runs outside ``_lock``); the others get the stale snapshot meanwhile,
    and only a first-ever load blocks."""
    global _cached, _expires_at
    with _lock:
        if _cached is not None and clock() < _expires_at:
            return _cached
        stale = _cached
    if stale is not None:
        if not _reload_lock.acquire(blocking=False):
            return stale
    else:
        _reload_lock.acquire()
    try:
        with _lock:
            if _cached is not None and clock() < _expires_at:
                return _cached   # another thread reloaded while we waited
            gen = _gen
        try:
            status, row = (loader or _default_loader)()
        except Exception:  # noqa: BLE001
            status, row = STATE_UNREADABLE, None
        snap = build_snapshot(status, row)
        ttl = _UNREADABLE_TTL_SEC if snap.state == STATE_UNREADABLE else _TTL_SEC
        if (snap.state == STATE_UNREADABLE and stale is not None
                and stale.state == STATE_OK):
            # stale-if-error: keep the last good copy; ``ttl`` stays the short
            # unreadable retry interval.
            snap = replace(stale, stale=True,
                           stale_since=stale.stale_since or wall(),
                           warnings=tuple(w for w in stale.warnings if w != WARN_STALE)
                           + (WARN_STALE,))
        with _lock:
            if gen == _gen:   # an invalidate() during the read voids it
                _cached, _expires_at = snap, clock() + ttl
        return snap
    finally:
        _reload_lock.release()


def invalidate() -> None:
    global _cached, _expires_at, _gen
    with _lock:
        _cached, _expires_at = None, 0.0
        _gen += 1


def current_setting(score_date: str | None = None) -> Mapping:
    """role -> ``llm.RoleSetting`` for the decision roles with a saved setting.
    Score is resolved for ``score_date`` (default: today's UTC date). Empty on no
    row / unreadable / invalid, so every role uses ``CODE_DEFAULTS`` (U4).
    Never raises."""
    try:
        snap = get_snapshot()
        if snap.state != STATE_OK:
            return _empty()
        out: dict = {}
        for role, entry in snap.entries.items():
            if role == llm.ROLE_SCORE:
                rs = score_entry_for(entry, score_date or sentiment_cache.score_date_today())
                if rs is not None:
                    out[role] = rs
            else:
                out[role] = _to_setting(entry)
        return types.MappingProxyType(out)
    except Exception:  # noqa: BLE001
        return _empty()


def save(payload, updated_by) -> dict:
    """Write the row through ``db.save_reference_table`` and drop the cached
    snapshot when it was saved. Returns the db result."""
    from stock_analyzer import db
    result = db.save_reference_table(TABLE_NAME, payload, updated_by)
    if isinstance(result, dict) and result.get("status") == "saved":
        invalidate()
    return result


def resolution_summary() -> str:
    """``score=<model> (<source>, from <date>, prior <model>) · filter=<model>
    (<source>) · extract_trigger=... · decision_settings=<state>``. Never raises."""
    try:
        snap = get_snapshot()
        parts = []
        for role in (llm.ROLE_SCORE, llm.ROLE_FILTER, llm.ROLE_EXTRACT_TRIGGER):
            model, source = llm.resolved_with_source(role)
            detail = source
            entry = snap.entries.get(role) if snap.state == STATE_OK else None
            if role == llm.ROLE_SCORE and isinstance(entry, Mapping):
                prior = entry.get("prior")
                prior_model = prior.get("model") if isinstance(prior, Mapping) else None
                detail += (f", from {entry.get('effective_from')}"
                           f", prior {prior_model or llm.CODE_DEFAULTS[role]}")
            parts.append(f"{role}={model} ({detail})")
        state = snap.state + (" (stale)" if snap.stale else "")
        return " · ".join(parts) + f" · decision_settings={state}"
    except Exception:  # noqa: BLE001
        return "decision model resolution unavailable"
