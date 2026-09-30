"""The headless cron must not log `missing ScriptRunContext!` on every DB
write (db.is_readonly). Kept out of test_db_readonly.py because that module
is marked `fast` and this one spawns a subprocess that imports streamlit."""
import pathlib
import subprocess
import sys

from stock_analyzer import db


def test_real_streamlit_headless_logs_no_script_ctx_warning():
    """End-to-end against the real installed streamlit, in a clean subprocess
    (re-importing streamlit in-process trips its own singleton guard): the
    headless path must not emit the warning that was burying real cron
    failures in the Railway logs. Before the fix this printed one per call."""
    code = "\n".join([
        "import threading",
        "from stock_analyzer import db",
        "for _ in range(5): db.is_readonly()",
        "t = threading.Thread(target=db.is_readonly); t.start(); t.join()",
        "db.set_readonly(False)",
    ])
    root = pathlib.Path(db.__file__).resolve().parents[1]
    res = subprocess.run([sys.executable, "-c", code], cwd=root,
                         capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stderr
    assert "ScriptRunContext" not in res.stderr + res.stdout
