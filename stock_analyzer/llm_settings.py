"""The App Settings model choice for ``llm.EDITABLE_ROLES``.

One ``reference_tables`` row named ``llm_models``, a process-level immutable
snapshot of it, and the provider ``llm.complete`` consults for an editable role
(docs/plans/llm-model-routing.md section 13.2 E, section 14 answer 1). No
Streamlit import: the app wraps the save path, and this module owns what may be
saved and what the runtime will believe.

Behaviour-neutral until a row exists: no row, an unreadable table, a row whose
hash doesn't match its payload, or a role that fails validation all resolve to
the code defaults (with a warning for System Trust where something is wrong).
"""
from __future__ import annotations

import re
import threading
import time
import types
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from stock_analyzer import llm, reference_data

TABLE_NAME = "llm_models"
PAYLOAD_VERSION = 1
_TTL_SEC = 300
_UNREADABLE_TTL_SEC = 30

STATE_OK = "ok"
STATE_NO_ROW = "no_row"
STATE_UNREADABLE = "unreadable"
STATE_INVALID = "invalid"

_ENTRY_KEYS = frozenset({"model", "effort", "capabilities", "checked_at"})
_MODEL_RE = re.compile(r"^claude-[a-z0-9][a-z0-9.\-]*$")

_WARN_UNREADABLE = "AI model settings could not be read; code defaults in use"
_WARN_HASH = ("AI model settings row doesn't match its saved hash "
              "(edited outside App Settings?); code defaults in use")


# --- Pure: canonical form and hash view --------------------------------------
def canonicalize(payload):
    """Recursive, idempotent normal form: dict keys sorted, str stripped (never
    re-cased), list order kept, anything else unchanged."""
    if isinstance(payload, dict):
        return {k: canonicalize(v)
                for k, v in sorted(payload.items(), key=lambda kv: str(kv[0]))}
    if isinstance(payload, list):
        return [canonicalize(v) for v in payload]
    if isinstance(payload, str):
        return payload.strip()
    return payload


def hash_view(normalized):
    """A copy with every ``roles[*]["checked_at"]`` removed (N9), so a re-check
    alone never changes the payload hash."""
    if not isinstance(normalized, dict):
        return normalized
    out = dict(normalized)
    roles = out.get("roles")
    if isinstance(roles, dict):
        out["roles"] = {
            r: ({k: v for k, v in e.items() if k != "checked_at"}
                if isinstance(e, dict) else e)
            for r, e in roles.items()
        }
    return out


# --- Pure: validation ---------------------------------------------------------
def validate_role_entry(role, entry) -> list[str]:
    errors: list[str] = []
    if role not in llm.EDITABLE_ROLES:
        errors.append(f"role {role!r} is not editable "
                      f"(editable: {sorted(llm.EDITABLE_ROLES)})")
    if not isinstance(entry, dict):
        errors.append("entry must be an object")
        return errors
    extra = sorted(str(k) for k in entry if k not in _ENTRY_KEYS)
    if extra:
        errors.append(f"unexpected key(s): {extra}")

    model = entry.get("model")
    model_ok = False
    if not isinstance(model, str):
        errors.append("model must be a string")
    elif not _MODEL_RE.match(model.strip()):
        errors.append(f"model {model!r} is not a claude- model id")
    else:
        model_ok = True
        model = model.strip()

    effort = entry.get("effort")
    if effort is not None and effort not in llm.ALLOWED_EFFORTS:
        errors.append(f"effort must be None or one of {list(llm.ALLOWED_EFFORTS)}, "
                      f"not {effort!r}")

    caps = entry.get("capabilities")
    listed = None
    if caps is not None:
        if not isinstance(caps, dict):
            errors.append("capabilities must be None or an object")
        else:
            if "efforts" in caps:
                ef = caps["efforts"]
                if (isinstance(ef, list) and all(isinstance(x, str) for x in ef)):
                    listed = ef
                else:
                    errors.append("capabilities.efforts must be a list of strings")
            if "max_tokens" in caps:
                mt = caps["max_tokens"]
                if mt is not None and (not isinstance(mt, int) or isinstance(mt, bool)):
                    errors.append("capabilities.max_tokens must be an int or None")
    if effort is not None and listed is not None and effort not in listed:
        errors.append(f"effort {effort!r} is not listed by the model's capabilities "
                      f"({listed})")

    if model_ok:
        profile = llm.profile_for(model)
        if effort is not None and profile in (llm.PROFILE_LEGACY, llm.PROFILE_OPUS_4X):
            errors.append(f"model {model!r} takes no effort setting "
                          f"({profile} profile drops it); leave effort empty")
        if profile == llm.PROFILE_GENERIC and caps is None:
            errors.append(f"model {model!r} isn't known to the code, so its "
                          "capabilities must be stored with it")

    checked_at = entry.get("checked_at")
    if checked_at is not None:
        try:
            if not isinstance(checked_at, str):
                raise ValueError
            datetime.fromisoformat(checked_at)
        except ValueError:
            errors.append(f"checked_at {checked_at!r} is not an ISO timestamp")
    return errors


def _top_level_errors(payload) -> list[str]:
    if not isinstance(payload, dict):
        return ["payload must be an object"]
    errors: list[str] = []
    extra = sorted(str(k) for k in payload if k not in ("version", "roles"))
    if extra:
        errors.append(f"unexpected key(s): {extra}")
    if payload.get("version") != PAYLOAD_VERSION:
        errors.append(f"version must be {PAYLOAD_VERSION}, not {payload.get('version')!r}")
    roles = payload.get("roles")
    if not isinstance(roles, dict):
        errors.append("roles must be an object")
        return errors
    for role in roles:
        if role not in llm.EDITABLE_ROLES:
            if role in llm.PINNED_ROLES:
                errors.append(f"role {role!r} is pinned in code and can't be set here")
            elif role == llm.ROLE_EXTRACT_STRONG:
                errors.append(f"role {role!r} stays code-only and can't be set here")
            else:
                errors.append(f"role {role!r} is not editable "
                              f"(editable: {sorted(llm.EDITABLE_ROLES)})")
    return errors


def validate_payload(payload) -> list[str]:
    errors = _top_level_errors(payload)
    roles = payload.get("roles") if isinstance(payload, dict) else None
    if isinstance(roles, dict):
        for role, entry in roles.items():
            if role in llm.EDITABLE_ROLES:
                errors.extend(f"{role}: {e}" for e in validate_role_entry(role, entry))
    return errors


# --- Snapshot -----------------------------------------------------------------
@dataclass(frozen=True)
class Snapshot:
    state: str
    setting: Mapping = field(default_factory=lambda: types.MappingProxyType({}))
    warnings: tuple = ()
    as_of: object = None
    updated_by: object = None


def _empty() -> Mapping:
    return types.MappingProxyType({})


def build_snapshot(status, row) -> Snapshot:
    if status == STATE_UNREADABLE:
        return Snapshot(STATE_UNREADABLE, _empty(), (_WARN_UNREADABLE,))
    if status == STATE_NO_ROW or status != "ok" or not isinstance(row, dict):
        return Snapshot(STATE_NO_ROW if status == STATE_NO_ROW else STATE_UNREADABLE,
                        _empty(),
                        () if status == STATE_NO_ROW else (_WARN_UNREADABLE,))
    as_of, updated_by = row.get("as_of"), row.get("updated_by")
    payload = row.get("payload")
    try:
        recomputed = reference_data.payload_hash_for(TABLE_NAME, canonicalize(payload))
    except Exception:  # noqa: BLE001 - an unhashable payload is a mismatch
        recomputed = None
    if recomputed != row.get("payload_hash"):
        return Snapshot(STATE_INVALID, _empty(), (_WARN_HASH,), as_of, updated_by)
    payload = canonicalize(payload)
    top = _top_level_errors(payload)
    if top:
        return Snapshot(STATE_INVALID, _empty(),
                        (f"AI model settings invalid ({'; '.join(top)}); "
                         "code defaults in use",), as_of, updated_by)
    setting: dict = {}
    warnings: list[str] = []
    for role, entry in payload["roles"].items():
        errs = validate_role_entry(role, entry)
        if errs:
            warnings.append(f"{role}: {errs}; code default in use")
            continue
        caps = entry.get("capabilities")
        setting[role] = llm.RoleSetting(
            entry["model"], entry.get("effort"),
            types.MappingProxyType(dict(caps)) if caps else None)
    return Snapshot(STATE_OK, types.MappingProxyType(setting), tuple(warnings),
                    as_of, updated_by)


def decide_save(role, model, effort, capabilities, retrieve_error, checked_at):
    """The save-time rule (plan 13.2 E): (entry, []) or (None, errors)."""
    model = model.strip() if isinstance(model, str) else model
    if retrieve_error is not None:
        if str(retrieve_error).startswith("NotFoundError"):
            return None, [f"this API key can't use {model}"]
        if not isinstance(model, str) or llm.profile_for(model) == llm.PROFILE_GENERIC:
            return None, [f"couldn't check {model} with the Models API ({retrieve_error}); "
                          "only a model the code knows can be saved unchecked"]
    entry = {"model": model, "effort": effort, "capabilities": capabilities,
             "checked_at": checked_at}
    errors = validate_role_entry(role, entry)
    if errors:
        return None, errors
    return entry, []


def _roles_copy(payload) -> dict:
    roles = payload.get("roles") if isinstance(payload, dict) else None
    return dict(roles) if isinstance(roles, dict) else {}


def apply_role(payload, role, entry) -> dict:
    roles = _roles_copy(payload)
    roles[role] = entry
    return {"version": PAYLOAD_VERSION, "roles": roles}


def reset_role(payload, role) -> dict:
    roles = _roles_copy(payload)
    roles.pop(role, None)
    return {"version": PAYLOAD_VERSION, "roles": roles}


# --- Process state (the only mutable state; llm.py stays free of it) ----------
_lock = threading.Lock()
_cached: Snapshot | None = None
_expires_at: float = 0.0


def _default_loader():
    from stock_analyzer import db
    return db.load_reference_table_status(TABLE_NAME)


def get_snapshot(*, loader=None, clock=time.monotonic) -> Snapshot:
    global _cached, _expires_at
    with _lock:
        if _cached is not None and clock() < _expires_at:
            return _cached
        try:
            status, row = (loader or _default_loader)()
        except Exception:  # noqa: BLE001
            status, row = STATE_UNREADABLE, None
        snap = build_snapshot(status, row)
        ttl = _UNREADABLE_TTL_SEC if snap.state == STATE_UNREADABLE else _TTL_SEC
        _cached, _expires_at = snap, clock() + ttl
        return snap


def invalidate() -> None:
    global _cached, _expires_at
    with _lock:
        _cached, _expires_at = None, 0.0


def current_setting():
    """The validated setting mapping, or None when there is none. Never raises."""
    try:
        setting = get_snapshot().setting
        return setting if setting else None
    except Exception:  # noqa: BLE001
        return None


def save(payload, updated_by) -> dict:
    """Write the row through ``db.save_reference_table`` and drop the cached
    snapshot when it was saved. Returns the db result."""
    from stock_analyzer import db
    result = db.save_reference_table(TABLE_NAME, payload, updated_by)
    if isinstance(result, dict) and result.get("status") == "saved":
        invalidate()
    return result


def resolution_summary() -> str:
    """``fast=<model> (<source>) · deep=... · extract=... · settings=<state>``.
    Never raises."""
    try:
        parts = []
        for role in (llm.ROLE_FAST, llm.ROLE_DEEP, llm.ROLE_EXTRACT):
            model, source = llm.resolved_with_source(role)
            parts.append(f"{role}={model} ({source})")
        return " · ".join(parts) + f" · settings={get_snapshot().state}"
    except Exception:  # noqa: BLE001
        return "model resolution unavailable"
