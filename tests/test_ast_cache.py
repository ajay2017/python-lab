"""Tests for tests/_ast_cache.py (the shared parsed-AST cache)."""
import ast
from pathlib import Path

import pytest

from tests import _ast_cache
from tests._ast_cache import parse_file_cached, source_segment

APP_PY = Path(__file__).resolve().parent.parent / "app.py"


@pytest.fixture
def cache(tmp_path, monkeypatch):
    d = tmp_path / "cache"
    monkeypatch.setattr(_ast_cache, "_cache_dir", lambda: d)
    monkeypatch.setattr(_ast_cache, "_MEMO", {})
    return d


def _src(tmp_path, text="x = 1\ny = x + 2\n"):
    p = tmp_path / "mod.py"
    p.write_text(text, encoding="utf-8")
    return p


def test_cached_equals_fresh_parse(cache, tmp_path):
    p = _src(tmp_path)
    assert ast.dump(parse_file_cached(p)) == ast.dump(ast.parse(p.read_text()))
    assert ast.dump(parse_file_cached(p)) == ast.dump(ast.parse(p.read_text()))


def test_calls_return_distinct_objects(cache, tmp_path):
    p = _src(tmp_path)
    a = parse_file_cached(p)
    a.body.clear()
    b = parse_file_cached(p)
    assert b.body and a is not b
    c = parse_file_cached(p)
    assert c is not b and c.body


def test_unchanged_file_hits_cache(cache, tmp_path, monkeypatch):
    p = _src(tmp_path)
    parse_file_cached(p)

    def boom(*a, **k):
        raise AssertionError("parsed again on a cache hit")

    monkeypatch.setattr(_ast_cache.ast, "parse", boom)
    assert parse_file_cached(p).body  # memo hit
    monkeypatch.setattr(_ast_cache, "_MEMO", {})
    assert parse_file_cached(p).body  # disk hit


def test_changed_content_is_not_stale(cache, tmp_path):
    p = _src(tmp_path)
    before = ast.dump(parse_file_cached(p))
    p.write_text("x = 1\ny = x + 3\n", encoding="utf-8")
    after = ast.dump(parse_file_cached(p))
    assert before != after
    assert after == ast.dump(ast.parse(p.read_text()))


def test_corrupt_cache_file_falls_back(cache, tmp_path):
    p = _src(tmp_path)
    parse_file_cached(p)
    for f in cache.glob("*.pkl"):
        f.write_bytes(b"not a pickle")
    _ast_cache._MEMO.clear()
    assert ast.dump(parse_file_cached(p)) == ast.dump(ast.parse(p.read_text()))


def test_unwritable_cache_dir_falls_back(tmp_path, monkeypatch):
    blocker = tmp_path / "afile"
    blocker.write_text("x")
    monkeypatch.setattr(_ast_cache, "_cache_dir", lambda: blocker / "sub")
    monkeypatch.setattr(_ast_cache, "_MEMO", {})
    p = _src(tmp_path)
    assert ast.dump(parse_file_cached(p)) == ast.dump(ast.parse(p.read_text()))


def test_stale_lock_is_broken(cache, tmp_path, monkeypatch):
    import os
    p = _src(tmp_path)
    cache.mkdir(parents=True)
    key_probe = parse_file_cached(p)  # creates files
    for f in cache.glob("*.pkl"):
        f.unlink()
    _ast_cache._MEMO.clear()
    import hashlib, sys
    h = hashlib.sha256()
    h.update(p.read_bytes()); h.update(sys.version.encode())
    h.update(str(_ast_cache._CACHE_VERSION).encode())
    lock = cache / f"{p.name}-{h.hexdigest()[:32]}.lock"
    lock.write_text("")
    old = lock.stat().st_mtime - 10_000
    os.utime(lock, (old, old))
    assert ast.dump(parse_file_cached(p)) == ast.dump(key_probe)


def test_warm_fills_missing_entries_only(cache, tmp_path, monkeypatch):
    p = _src(tmp_path)
    missing = tmp_path / "nope.py"
    assert _ast_cache.warm([p, missing]) == ["mod.py"]
    assert len(list(cache.glob("mod.py-*.pkl"))) == 1

    def boom(*a, **k):
        raise AssertionError("warm re-parsed a file already in the cache")

    monkeypatch.setattr(_ast_cache, "parse_file_cached", boom)
    assert _ast_cache.warm([p]) == []          # existing entry: skipped


def test_warm_never_raises(tmp_path, monkeypatch):
    blocker = tmp_path / "afile"
    blocker.write_text("x")
    monkeypatch.setattr(_ast_cache, "_cache_dir", lambda: blocker / "sub")
    monkeypatch.setattr(_ast_cache, "_MEMO", {})
    p = _src(tmp_path)
    _ast_cache.warm([p, 123])                  # unwritable dir + a bad path: no raise


def test_prune_removes_only_old_entries(cache, tmp_path):
    import os
    old_p = _src(tmp_path, "a = 1\n")
    parse_file_cached(old_p)
    (old_f,) = cache.glob("*.pkl")
    t = old_f.stat().st_mtime - 8 * 86400
    os.utime(old_f, (t, t))
    new_p = tmp_path / "other.py"
    new_p.write_text("b = 2\n", encoding="utf-8")
    parse_file_cached(new_p)
    assert _ast_cache.prune(max_age_days=7) == 1
    assert [f.name.split("-")[0] for f in cache.glob("*.pkl")] == ["other.py"]


def test_conftest_warms_in_the_controller_only():
    import tests.conftest as cf

    class Worker:
        workerinput = {"workerid": "gw0"}

    calls = []
    orig = _ast_cache.warm
    _ast_cache.warm = lambda paths: calls.append(list(paths)) or []
    try:
        cf.pytest_configure(Worker())
        assert calls == []                      # a worker never warms
    finally:
        _ast_cache.warm = orig


# Deliberately NO "cached app.py == fresh parse of app.py" test: it would pay a
# full uncached ~15-75s parse on EVERY run, re-proving what the tmp-file tests
# above already prove generically (pickle round-trip fidelity + content-keyed
# invalidation). The mutation runs that edit app.py and see wiring tests fail
# are the end-to-end evidence that no stale tree is ever served.


def test_source_segment_matches_stdlib_on_tricky_source():
    src = ("x = (1 +\n     2)\r\n"
           "s = '—'; t = 'café \U0001F600'; u = t + s\r\n"
           "def f(a,\n      b):\n    return a  # — note\n")
    tree = ast.parse(src)
    nodes = list(ast.walk(tree))
    assert any(not hasattr(n, "lineno") for n in nodes)  # Module/Load: no positions
    assert any(getattr(n, "end_lineno", 0) != getattr(n, "lineno", 0)
               for n in nodes if hasattr(n, "lineno"))  # multi-line present
    for n in nodes:
        assert source_segment(src, n) == ast.get_source_segment(src, n)
    assert source_segment(src, tree) is None


def test_source_segment_matches_stdlib_on_app_sample():
    src = APP_PY.read_text(encoding="utf-8")
    tree = parse_file_cached(APP_PY)
    # parity vs the REAL stdlib on app.py: the one deliberate opt-out of the
    # conftest slow-parse guard
    stdlib_segment = getattr(ast.get_source_segment, "__wrapped__", ast.get_source_segment)
    n_checked = 0
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            assert source_segment(src, n) == stdlib_segment(src, n)
            n_checked += 1
            if n_checked >= 200:
                break
    assert n_checked == 200


# ── conftest guard: a test must not parse app.py-sized source directly ─────

_HUGE = "#" * 1_000_001   # just over the guard's threshold; a comment, cheap to parse


def test_guard_blocks_a_direct_huge_parse_from_a_test():
    with pytest.raises(pytest.fail.Exception, match="parse_file_cached"):
        ast.parse(_HUGE)
    with pytest.raises(pytest.fail.Exception, match="source_segment"):
        ast.get_source_segment(_HUGE, ast.parse("x = 1").body[0])


def test_guard_is_not_swallowed_by_except_exception():
    swallowed = False
    with pytest.raises(pytest.fail.Exception):
        try:
            ast.parse(_HUGE)
        except Exception:
            swallowed = True
    assert not swallowed


def test_guard_allows_small_sources_and_the_cache_itself(cache, tmp_path):
    assert ast.parse("x = 1").body                       # small source from a test: fine
    p = tmp_path / "big.py"
    p.write_text(_HUGE, encoding="utf-8")
    assert isinstance(parse_file_cached(p), ast.Module)  # _ast_cache parses it: fine


def test_guard_allows_non_test_callers():
    # production code (e.g. scripts/check_antipatterns.py) parses app.py itself
    ns = {}
    exec(compile("import ast\ndef f(s):\n    return ast.parse(s)\n", "/not_tests/mod.py", "exec"), ns)
    assert isinstance(ns["f"](_HUGE), ast.Module)
