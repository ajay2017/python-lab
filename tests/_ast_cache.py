"""Shared, on-disk cache of parsed ASTs for the test suite (test-only).

Why: ``ast.parse`` of app.py (~2.7 MB, ~47K lines) costs ~15s idle and 40-90s
under xdist load, and ~19 test files each parsed it independently, so ~37 of
the 40 slowest tests were just that parse (suite 281s wall vs a 450s hook
timeout). Unpickling the same tree takes ~0.4s.

Guarantees:
* Key = sha256(file bytes + sys.version + _CACHE_VERSION): any content change
  (mutation tests, another snapshot, an edit) or a Python upgrade misses and
  re-parses -- a stale tree is never served.
* Every call returns a FRESH tree (unpickled from bytes), never a shared
  mutable object. An in-process memo holds pickled BYTES, not trees.
* The cache is only an optimisation: any lock/IO/pickle error or timeout falls
  back to a plain ``ast.parse`` of the same source.
"""
from __future__ import annotations

import ast
import hashlib
import os
import pickle
import re
import sys
import tempfile
import time
from pathlib import Path

_CACHE_VERSION = 1
_WAIT_S = 120.0
_STALE_LOCK_S = 180.0
_POLL_S = 0.1

_MEMO: dict[str, bytes] = {}


def _cache_dir() -> Path:
    return Path(tempfile.gettempdir()) / "drishta-test-ast-cache"


def _plain_parse(raw: bytes, path: Path) -> ast.Module:
    return ast.parse(raw.decode("utf-8-sig"), filename=str(path))



def parse_file_cached(path) -> ast.Module:
    """Return a fresh ``ast.Module`` for ``path``, via the on-disk cache."""
    path = Path(path)
    raw = path.read_bytes()
    try:
        h = hashlib.sha256()
        h.update(raw)
        h.update(sys.version.encode())
        h.update(str(_CACHE_VERSION).encode())
        key = h.hexdigest()[:32]
    except Exception:
        return _plain_parse(raw, path)

    memo = _MEMO.get(key)
    if memo is not None:
        try:
            return pickle.loads(memo)
        except Exception:
            _MEMO.pop(key, None)

    try:
        cdir = _cache_dir()
        cache_file = cdir / f"{path.name}-{key}.pkl"
        lock_file = cdir / f"{path.name}-{key}.lock"

        def _try_load():
            try:
                data = cache_file.read_bytes()
                tree = pickle.loads(data)
            except Exception:
                return None
            _MEMO[key] = data
            return tree

        tree = _try_load()
        if tree is not None:
            return tree

        cdir.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + _WAIT_S
        while True:
            try:
                fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                tree = _try_load()
                if tree is not None:
                    return tree
                try:
                    if time.time() - lock_file.stat().st_mtime > _STALE_LOCK_S:
                        os.unlink(str(lock_file))
                        continue
                except OSError:
                    continue  # lock vanished; retry acquiring
                if time.monotonic() > deadline:
                    return _plain_parse(raw, path)
                time.sleep(_POLL_S)
                continue
            break
        try:
            os.close(fd)
            tree = _try_load()  # another worker may have filled it
            if tree is not None:
                return tree
            tree = _plain_parse(raw, path)
            data = pickle.dumps(tree, protocol=pickle.HIGHEST_PROTOCOL)
            tmp = cdir / f"{cache_file.name}.{os.getpid()}.tmp"
            try:
                tmp.write_bytes(data)
                os.replace(str(tmp), str(cache_file))
            finally:
                try:
                    tmp.unlink()
                except OSError:
                    pass
            _MEMO[key] = data
            return pickle.loads(data)
        finally:
            try:
                os.unlink(str(lock_file))
            except OSError:
                pass
    except Exception:
        return _plain_parse(raw, path)


# --- source_segment: ast.get_source_segment without re-splitting per call -----
# The stdlib splits the WHOLE source into lines on every call; in a loop over
# app.py (~49K lines) that is minutes. Same splitter and byte-offset slicing as
# scripts/check_antipatterns.py::_Visitor._source_segment (padded=False).
_LINE_RE = re.compile(r"(.*?(?:\r\n|\n|\r|$))")
_LINES_MEMO: dict[int, tuple[str, list[str]]] = {}


def _split_lines(src: str) -> list[str]:
    hit = _LINES_MEMO.get(id(src))
    if hit is not None and hit[0] is src:  # id reuse after GC can never match
        return hit[1]
    lines = [m[0] for m in _LINE_RE.finditer(src)]
    if len(_LINES_MEMO) > 8:
        _LINES_MEMO.clear()
    _LINES_MEMO[id(src)] = (src, lines)
    return lines


def source_segment(src: str, node) -> str | None:
    """Same result as ``ast.get_source_segment(src, node)`` (padded=False)."""
    start = getattr(node, "lineno", None)
    end = getattr(node, "end_lineno", None)
    col = getattr(node, "col_offset", None)
    end_col = getattr(node, "end_col_offset", None)
    if start is None or end is None or col is None or end_col is None:
        return None
    lineno, end_lineno = start - 1, end - 1
    lines = _split_lines(src)
    if end_lineno == lineno:
        return lines[lineno].encode()[col:end_col].decode()
    first = lines[lineno].encode()[col:].decode()
    last = lines[end_lineno].encode()[:end_col].decode()
    return first + "".join(lines[lineno + 1:end_lineno]) + last
