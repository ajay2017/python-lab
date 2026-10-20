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
    n_checked = 0
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            assert source_segment(src, n) == ast.get_source_segment(src, n)
            n_checked += 1
            if n_checked >= 200:
                break
    assert n_checked == 200
