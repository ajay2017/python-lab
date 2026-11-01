"""Plan 17 C3: score-cache provenance (model / effort / served_model), one score
entry per bundle, the legacy-select fallback and the stale-if-error snapshot."""
import ast
import inspect

import pandas as pd
import pytest

from stock_analyzer import bundle_loader, db, llm, llm_decision_settings as lds
from stock_analyzer import news_intelligence as ni
from stock_analyzer import reference_data
from stock_analyzer import sentiment_cache as sc

H45 = llm.HAIKU_4_5
H55 = "claude-haiku-5-5"
DEFAULT = llm.RoleSetting(model=llm.CODE_DEFAULTS[llm.ROLE_SCORE])


# ---- row_matches ----
def test_legacy_model_is_haiku_4_5_not_the_code_default_alias():
    assert sc.LEGACY_CACHE_MODEL == H45


def test_null_row_hits_the_code_default_entry():
    assert llm.CODE_DEFAULTS[llm.ROLE_SCORE] == H45   # the premise: today's behaviour
    for row in ({}, {"model": None, "effort": None}, {"model": "", "effort": ""}):
        assert sc.row_matches(row, DEFAULT) is True


def test_null_row_misses_a_haiku_5_5_entry():
    assert sc.row_matches({"model": None}, llm.RoleSetting(model=H55)) is False


def test_effort_only_difference_misses():
    entry = llm.RoleSetting(model=H55, effort="low")
    assert sc.row_matches({"model": H55, "effort": "medium"}, entry) is False
    assert sc.row_matches({"model": H55, "effort": None}, entry) is False
    assert sc.row_matches({"model": H55, "effort": "low"},
                          llm.RoleSetting(model=H55)) is False


def test_equal_pair_hits():
    assert sc.row_matches({"model": H55, "effort": "low"},
                          llm.RoleSetting(model=H55, effort="low")) is True
    assert sc.row_matches({"model": H55, "effort": ""},
                          llm.RoleSetting(model=H55, effort=None)) is True


def test_non_dict_row_reads_as_legacy():
    assert sc.row_matches(None, DEFAULT) is True


# ---- resolve_score_entry ----
def test_resolve_no_row_gives_code_default(monkeypatch):
    monkeypatch.setattr(lds, "current_setting", lambda score_date=None: {})
    e = sc.resolve_score_entry("2026-11-10")
    assert e == DEFAULT and e.effort is None and e.capabilities is None


def test_resolve_provider_raising_gives_code_default(monkeypatch):
    def boom(score_date=None):
        raise RuntimeError("x")
    monkeypatch.setattr(lds, "current_setting", boom)
    assert sc.resolve_score_entry("2026-11-10") == DEFAULT


def test_resolve_passes_the_date_and_honours_effective_from(monkeypatch):
    entry = {"model": H55, "effort": "low", "capabilities": None, "checked_at": None,
             "eval_id": 1, "criteria_version": 1, "effective_from": "2026-11-10",
             "prior": None}
    seen = []

    def cur(score_date=None):
        seen.append(score_date)
        rs = lds.score_entry_for(entry, score_date)
        return {llm.ROLE_SCORE: rs} if rs is not None else {}

    monkeypatch.setattr(lds, "current_setting", cur)
    before = sc.resolve_score_entry("2026-11-09")
    after = sc.resolve_score_entry("2026-11-10")
    assert before == DEFAULT
    assert (after.model, after.effort) == (H55, "low")
    assert seen == ["2026-11-09", "2026-11-10"]


# ---- db: legacy-select fallback + save ----
class _Q:
    def __init__(self, data, fail_cols=False, fail_upsert_prov=False, err=None):
        self.data, self.fail_cols, self.fail_upsert_prov = data, fail_cols, fail_upsert_prov
        self.err = err or ("{'code': '42703', 'message': "
                           "'column sentiment_llm_cache.model does not exist'}")
        self.selects, self.upserts = [], []
        self._pending = None

    def table(self, name):
        return self

    def select(self, cols):
        self.cols = cols
        self.selects.append(cols)
        self._pending = None
        return self

    def upsert(self, row):
        self.upserts.append(row)
        self._pending = row
        return self

    def __getattr__(self, n):
        if n in ("eq", "limit"):
            return lambda *a, **k: self
        raise AttributeError(n)

    def execute(self):
        if self._pending is not None:
            row, self._pending = self._pending, None
            if self.fail_upsert_prov and "model" in row:
                raise Exception(self.err)
            return type("R", (), {"data": []})()
        if self.fail_cols and "model" in self.cols:
            raise Exception(self.err)
        return type("R", (), {"data": [dict(r) for r in self.data]})()


@pytest.fixture
def fq(monkeypatch):
    def make(*a, **k):
        q = _Q(*a, **k)
        monkeypatch.setattr(db, "has_db", lambda: True)
        monkeypatch.setattr(db, "_client", lambda: q)
        return q
    return make


HEADS = [{"headline": "h", "score": 0.1}]


def test_load_selects_provenance_columns(fq):
    q = fq([{"headlines": HEADS, "avg_sent": 0.1, "model": H55, "effort": "low",
             "served_model": H55}])
    row = db.load_sentiment_llm_cache("aaa", "2026-11-10")
    assert q.selects == ["headlines,avg_sent,model,effort,served_model"]
    assert row["model"] == H55 and row["effort"] == "low" and row["served_model"] == H55


def test_load_legacy_select_fallback_returns_the_row(fq, capsys):
    q = fq([{"headlines": HEADS, "avg_sent": 0.1}], fail_cols=True)
    row = db.load_sentiment_llm_cache("AAA", "2026-11-10")
    assert row == {"headlines": HEADS, "avg_sent": 0.1, "model": None, "effort": None,
                   "served_model": None}
    assert q.selects[-1] == "headlines,avg_sent" and len(q.selects) == 2
    assert "legacy select" in capsys.readouterr().out


def test_load_other_errors_stay_a_miss(fq):
    fq([], fail_cols=True, err="connection reset")
    assert db.load_sentiment_llm_cache("AAA", "2026-11-10") is None


def test_save_includes_provenance(fq):
    q = fq([])
    assert db.save_sentiment_llm_cache("aaa", "2026-11-10", HEADS, 0.1,
                                       model=H55, effort="low", served_model="srv")
    r = q.upserts[0]
    assert (r["model"], r["effort"], r["served_model"]) == (H55, "low", "srv")
    assert r["ticker"] == "AAA"


def test_save_without_provenance_keeps_the_old_shape(fq):
    q = fq([])
    assert db.save_sentiment_llm_cache("AAA", "2026-11-10", HEADS, 0.1)
    assert not ({"model", "effort", "served_model"} & set(q.upserts[0]))


def test_save_writes_null_effort_when_a_model_is_given(fq):
    q = fq([])
    db.save_sentiment_llm_cache("AAA", "2026-11-10", HEADS, 0.1, model=H45)
    assert q.upserts[0]["effort"] is None and q.upserts[0]["served_model"] is None


def test_save_falls_back_without_provenance_on_missing_column(fq, capsys):
    q = fq([], fail_upsert_prov=True)
    assert db.save_sentiment_llm_cache("AAA", "2026-11-10", HEADS, 0.1, model=H55) is True
    assert len(q.upserts) == 2 and "model" not in q.upserts[1]
    assert "saving without them" in capsys.readouterr().out


def test_save_other_errors_return_false(fq):
    fq([], fail_upsert_prov=True, err="rls violation")
    assert db.save_sentiment_llm_cache("AAA", "2026-11-10", HEADS, 0.1, model=H55) is False


# ---- rescore meta ----
def test_rescore_sets_served_model_in_meta(monkeypatch):
    res = llm.LLMResult(text='[{"idx": 0, "score": 0.2}]', stop_reason="end_turn",
                        error=None, model="served-x", usage=None)
    monkeypatch.setattr(ni.llm, "complete", lambda *a, **k: res)
    meta = {}
    out = ni.rescore_headlines_llm([{"headline": "h", "score": 0.0}], "k", "AAA", meta=meta)
    assert out is not None and meta == {"served_model": "served-x"}


def test_rescore_leaves_meta_untouched_on_failure(monkeypatch):
    res = llm.LLMResult(text=None, stop_reason=None, error="boom", model="m", usage=None)
    monkeypatch.setattr(ni.llm, "complete", lambda *a, **k: res)
    meta = {}
    assert ni.rescore_headlines_llm([{"headline": "h", "score": 0.0}], "k", "AAA",
                                    meta=meta) is None
    assert meta == {}


# ---- bundle_loader wiring ----
NOW = 1_800_000_000.0


def _price_df(n=60):
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    c = [100 + i * 0.3 for i in range(n)]
    return pd.DataFrame({"Close": c, "High": [x + 1 for x in c], "Low": [x - 1 for x in c],
                         "Open": c, "Volume": [1e6] * n}, index=idx)


class _Db:
    cache_row = None
    saved = []

    @classmethod
    def reset(cls, cache_row=None):
        cls.cache_row, cls.saved = cache_row, []

    save_bundle_cache = staticmethod(lambda *a, **k: None)
    load_bundle_cache = staticmethod(lambda *a, **k: None)
    save_fundamentals_cache = staticmethod(lambda *a, **k: None)
    load_fundamentals_cache = staticmethod(lambda *a, **k: None)
    load_analyst_coverage = staticmethod(lambda *a, **k: None)
    load_analyst_coverage_or_none = staticmethod(lambda *a, **k: None)
    save_sector_cache = staticmethod(lambda *a, **k: None)
    load_sector_cache = staticmethod(lambda *a, **k: "")
    save_etf_lookthrough_cache = staticmethod(lambda *a, **k: None)
    load_etf_lookthrough_cache = staticmethod(lambda *a, **k: None)
    load_sentiment_llm_cache = classmethod(lambda cls, *a, **k: cls.cache_row)

    @classmethod
    def save_sentiment_llm_cache(cls, *a, **k):
        cls.saved.append((a, k))


def _bundle():
    item = {"uuid": "t", "title": "Acme beats earnings", "publisher": "Barrons.com",
            "link": "https://x/y", "providerPublishTime": int(NOW - 3600),
            "relatedTickers": ["ACME"]}
    return {"history": _price_df(), "info": {"sector": ""}, "news": [item],
            "news_status": "ok", "earnings": {}, "revisions": {}, "_info_source": "t"}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(bundle_loader, "db", _Db)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(bundle_loader, "fetch_ticker_bundle", lambda t, p: _bundle())
    _Db.reset()
    rec = {"resolve": [], "match": [], "rescore": [], "entry": DEFAULT,
           "alt": llm.RoleSetting(model=H55, effort="low")}

    def resolve(d):
        rec["resolve"].append(d)
        return rec["entry"]

    real_match = sc.row_matches

    def match(row, e):
        rec["match"].append(e)
        return real_match(row, e)

    def rescore(heads, key, ticker="", **kw):
        rec["rescore"].append(kw)
        kw["meta"]["served_model"] = "served-y"
        return [dict(h, score=0.4) for h in heads]

    monkeypatch.setattr(sc, "resolve_score_entry", resolve)
    monkeypatch.setattr(sc, "row_matches", match)
    monkeypatch.setattr(ni, "rescore_headlines_llm", rescore)
    return rec


_ROW = {"headlines": [{"headline": "Acme beats earnings", "score": 0.1}], "avg_sent": 0.1}


def test_null_row_is_a_hit_with_no_decision_row(env):
    _Db.reset(cache_row=dict(_ROW, model=None, effort=None, served_model=None))
    out = bundle_loader.load_bundle("ACME")
    assert env["rescore"] == [] and _Db.saved == []
    assert out["headlines"] == _ROW["headlines"]
    assert len(env["resolve"]) == 1


def test_other_model_row_is_a_miss_rescored_and_overwritten(env):
    env["entry"] = env["alt"]
    _Db.reset(cache_row=dict(_ROW, model=H45, effort=None))
    bundle_loader.load_bundle("ACME")
    assert len(env["rescore"]) == 1
    (args, kw), = _Db.saved
    assert (kw["model"], kw["effort"], kw["served_model"]) == (H55, "low", "served-y")


def test_one_entry_flows_to_compare_call_and_save(env):
    env["entry"] = env["alt"]
    _Db.reset(cache_row=dict(_ROW, model=H45))
    bundle_loader.load_bundle("ACME")
    assert len(env["resolve"]) == 1 and env["resolve"][0] == sc.score_date_today()
    assert env["match"] == [env["alt"]]
    assert env["rescore"][0]["setting"][llm.ROLE_SCORE] is env["alt"]
    (args, kw), = _Db.saved
    assert (kw["model"], kw["effort"]) == (env["alt"].model, env["alt"].effort)


def test_miss_with_code_default_passes_the_entry_and_saves_legacy_pair(env):
    _Db.reset(cache_row=None)
    bundle_loader.load_bundle("ACME")
    assert env["rescore"][0]["setting"] == {llm.ROLE_SCORE: DEFAULT}
    (args, kw), = _Db.saved
    assert (kw["model"], kw["effort"]) == (H45, None)


def test_code_default_entry_builds_the_same_request_as_no_setting():
    """HEAD-equivalence: RoleSetting(CODE_DEFAULTS model) builds HEAD's request."""
    def req(setting):
        seen = {}

        class _Msgs:
            @staticmethod
            def create(**kw):
                seen.update(kw)
                raise RuntimeError("stop")

        class _C:
            messages = _Msgs

        llm.complete(llm.ROLE_SCORE, api_key="k", max_tokens=512, temperature=0,
                     messages=[{"role": "user", "content": "x"}], setting=setting,
                     client_factory=lambda api_key, **kw: _C())
        return seen

    with_entry = req({llm.ROLE_SCORE: DEFAULT})
    assert with_entry and with_entry == req({})
    assert with_entry["model"] == H45


def _names(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def test_ast_pin_one_entry_name_on_both_ends():
    src = inspect.getsource(bundle_loader)
    tree = ast.parse(src)
    rescore_kw, save_kw = {}, {}
    entry_assign = 0
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_entry" for t in n.targets):
            entry_assign += 1
        if isinstance(n, ast.Call):
            f = n.func
            fname = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
            if fname == "_rescore_hl":
                rescore_kw = {k.arg: k.value for k in n.keywords}
            if fname == "save_sentiment_llm_cache":
                save_kw = {k.arg: k.value for k in n.keywords}
    assert entry_assign == 1 and src.count("resolve_score_entry(") == 1
    assert "_entry" in _names(rescore_kw["setting"])
    assert "_entry" in _names(save_kw["model"]) and "_entry" in _names(save_kw["effort"])


# ---- llm_decision_settings stale-if-error (reviewer NB11) ----
NEW = H55


def _evald(**kw):
    e = {"model": NEW, "effort": "low", "capabilities": None, "checked_at": None,
         "eval_id": 7, "criteria_version": 1}
    e.update(kw)
    return e


def _row(payload):
    norm = lds.canonicalize(payload)
    return {"payload": norm,
            "payload_hash": reference_data.payload_hash_for(lds.TABLE_NAME, norm),
            "as_of": "2026-11-01", "updated_by": "t"}


class _Clock:
    t = 0.0

    def __call__(self):
        return self.t


def _seq(results):
    it = iter(results)
    return lambda: next(it)


_OK = ("ok", None)


def _ok():
    return ("ok", _row({"version": 1, "roles": {"filter": _evald()}}))


@pytest.fixture(autouse=True)
def _fresh_snapshot():
    lds.invalidate()
    yield
    lds.invalidate()


def test_ok_then_unreadable_keeps_the_old_entries_stale():
    clock = _Clock()
    loader = _seq([_ok(), ("unreadable", None), ("unreadable", None)])
    first = lds.get_snapshot(loader=loader, clock=clock)
    assert first.state == "ok" and first.stale is False
    clock.t += 10_000.0
    second = lds.get_snapshot(loader=loader, clock=clock)
    assert second.state == "ok" and second.stale is True
    assert second.entries["filter"]["model"] == NEW
    assert lds.WARN_STALE in second.warnings
    # retried after the short unreadable interval, not the 300 s TTL
    clock.t += lds._UNREADABLE_TTL_SEC + 1
    third = lds.get_snapshot(loader=loader, clock=clock)
    assert third.stale is True and third.warnings.count(lds.WARN_STALE) == 1


def test_stale_clears_when_the_reload_succeeds():
    clock = _Clock()
    loader = _seq([_ok(), ("unreadable", None), _ok()])
    lds.get_snapshot(loader=loader, clock=clock)
    clock.t += 10_000.0
    assert lds.get_snapshot(loader=loader, clock=clock).stale is True
    clock.t += lds._UNREADABLE_TTL_SEC + 1
    again = lds.get_snapshot(loader=loader, clock=clock)
    assert again.state == "ok" and again.stale is False


def test_ok_then_invalid_replaces_the_entries():
    clock = _Clock()
    bad = _ok()[1]
    bad["payload_hash"] = "0" * 64
    loader = _seq([_ok(), ("ok", bad)])
    lds.get_snapshot(loader=loader, clock=clock)
    clock.t += 10_000.0
    snap = lds.get_snapshot(loader=loader, clock=clock)
    assert snap.state == "invalid" and dict(snap.entries) == {} and snap.stale is False


def test_unreadable_with_no_previous_ok_gives_code_defaults():
    snap = lds.get_snapshot(loader=_seq([("unreadable", None)]), clock=_Clock())
    assert snap.state == "unreadable" and snap.stale is False
    assert dict(snap.entries) == {}
